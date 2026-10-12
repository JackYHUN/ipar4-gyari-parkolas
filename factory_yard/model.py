"""Deterministic factory-yard process and onboard virtual sensors.

The coordinator receives only the messages returned by ``step``.  The private
world state is used for physics, virtual sensor sampling and reference checks.
Distances are metres, time is seconds and headings are clockwise SVG degrees.
Pedestrians cooperate at the curb; this is a bounded simulation, not a general
autonomous-driving or functional-safety claim.  Vehicle steering is an idealised
polyline motion and outbound dispatch removes a parked vehicle after its dwell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import math
import random
from typing import Any

from .proximity import ParkingSensorModel


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _quantized_sample_period(nominal: float, dt: float) -> float:
    """Actual fixed-step cadence for the existing due-on-next-tick sampler.

    The same 1 ns comparison tolerance is used by ``World.step``. No sample
    is interpolated between physics ticks. At floating-point overflow-scale
    ratios the finite nominal period already has no representable tick offset.
    """
    ratio = max(0.0, nominal - 1e-9) / dt
    if not math.isfinite(ratio):
        return nominal
    interval = max(1, math.ceil(ratio)) * dt
    return interval if math.isfinite(interval) else nominal


def _route_lengths(route: list[list[float]]) -> list[float]:
    lengths = [0.0]
    for a, b in zip(route, route[1:]):
        lengths.append(lengths[-1] + _distance(a, b))
    return lengths


def _pose(route: list[list[float]], lengths: list[float], progress: float) -> tuple[float, float, float]:
    if len(route) < 2:
        return route[0][0], route[0][1], 0.0
    for index in range(len(route) - 1):
        if progress < lengths[index + 1] - 1e-9 or index == len(route) - 2:
            a, b = route[index], route[index + 1]
            segment = lengths[index + 1] - lengths[index]
            fraction = min(1.0, max(0.0, (progress - lengths[index]) / max(segment, 1e-9)))
            return (a[0] + fraction * (b[0] - a[0]),
                    a[1] + fraction * (b[1] - a[1]),
                    math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360)
    return route[-1][0], route[-1][1], 0.0


def _project(route: list[list[float]], lengths: list[float], x: float, y: float) -> tuple[float, float]:
    best = (float("inf"), 0.0)
    for index, (a, b) in enumerate(zip(route, route[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        norm = dx * dx + dy * dy
        fraction = min(1.0, max(0.0, ((x - a[0]) * dx + (y - a[1]) * dy) / max(norm, 1e-9)))
        px, py = a[0] + fraction * dx, a[1] + fraction * dy
        lateral = math.hypot(x - px, y - py)
        progress = lengths[index] + fraction * math.sqrt(norm)
        if lateral < best[0]:
            best = lateral, progress
    return best[1], best[0]


def _signed_route_projection(route: list[list[float]], lengths: list[float],
                             x: float, y: float, heading: float) -> dict:
    """Project a measured pose on the known route without controlling motion.

    SVG coordinates have positive y downwards. Positive lateral error is to
    the right when looking along the route. At an equidistant corner the
    measured heading selects the more compatible segment; exact ties prefer
    the earlier segment. Degenerate segments are not usable references.
    """
    best = None
    for index, (a, b) in enumerate(zip(route, route[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        norm = dx * dx + dy * dy
        if norm <= 1e-18:
            continue
        segment = math.sqrt(norm)
        fraction = min(1.0, max(0.0, ((x - a[0]) * dx + (y - a[1]) * dy) / norm))
        px, py = a[0] + fraction * dx, a[1] + fraction * dy
        route_heading = math.degrees(math.atan2(dy, dx)) % 360
        angle_error = (heading - route_heading + 180) % 360 - 180
        distance = math.hypot(x - px, y - py)
        if (best is None or distance < best[0] - 1e-9
                or (abs(distance - best[0]) <= 1e-9 and abs(angle_error) < best[1] - 1e-9)):
            best = distance, abs(angle_error), {
                "available": True,
                "source": "noisy_pose_projection_on_assigned_map_route",
                "projected_x_m": round(px, 6), "projected_y_m": round(py, 6),
                "segment_index": index,
                "progress_m": round(lengths[index] + fraction * segment, 6),
                "lateral_error_m": round((dx * (y - py) - dy * (x - px)) / segment, 6),
                "heading_error_deg": round(angle_error, 6),
                "route_heading_deg": round(route_heading, 6),
            }
    return best[2] if best else {"available": False, "source": "noisy_pose_projection_on_assigned_map_route"}


def _rect_interval(route: list[list[float]], lengths: list[float], zone: dict) -> tuple[float, float] | None:
    """Liang-Barsky segment clipping; zone x/y denote its centre."""
    xmin, xmax = zone["x"] - zone["w"] / 2, zone["x"] + zone["w"] / 2
    ymin, ymax = zone["y"] - zone["h"] / 2, zone["y"] + zone["h"] / 2
    hits = []
    for index, (a, b) in enumerate(zip(route, route[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        low, high = 0.0, 1.0
        valid = True
        for p, q in ((-dx, a[0] - xmin), (dx, xmax - a[0]),
                     (-dy, a[1] - ymin), (dy, ymax - a[1])):
            if abs(p) < 1e-12:
                if q < 0:
                    valid = False
                    break
            elif p < 0:
                low = max(low, q / p)
            else:
                high = min(high, q / p)
        if valid and low <= high:
            segment = math.hypot(dx, dy)
            hits.extend((lengths[index] + low * segment, lengths[index] + high * segment))
    return (min(hits), max(hits)) if hits else None


@dataclass
class Car:
    id: str
    created_at: float
    x: float
    y: float
    request_id: str
    heading: float = 0.0
    speed: float = 0.0
    state: str = "waiting"
    wait_reason: str = "Parkolóengedélyre vár"
    slot_id: str | None = None
    reservation_id: str | None = None
    route: list[list[float]] = field(default_factory=list)
    lengths: list[float] = field(default_factory=list)
    progress: float = 0.0
    distance: float = 0.0
    seq: int = 0
    next_request: float = 0.0
    parked_at: float | None = None
    occupancy_observed: bool = False
    frozen_until: float = 0.0
    sensor_at: float = 0.0
    sensor_view: dict | None = None
    control_view: dict | None = None
    perceived_cars: list[dict] = field(default_factory=list)
    perceived_crossings: dict[str, str] = field(default_factory=dict)
    seen_crossings: set[str] = field(default_factory=set)
    committed_crossings: set[str] = field(default_factory=set)
    observed_progress: dict[str, float] = field(default_factory=dict)
    admitted_at: float = 0.0
    zone_requests: dict[str, str] = field(default_factory=dict)
    zone_grants: dict[str, str] = field(default_factory=dict)
    zone_released: set[str] = field(default_factory=set)
    next_zone_request: dict[str, float] = field(default_factory=dict)
    zone_intervals: dict[str, tuple[float, float]] = field(default_factory=dict)


class World:
    """Finite, seeded yard process; local perception is sampled separately."""

    def __init__(self, config: dict, run_id: str, seed: int = 42):
        self.config = config
        self.network_mode = bool(config.get("network"))
        self.run_id = run_id
        self.seed = seed
        self.initial_rng = random.Random(seed)
        self.pedestrian_rng = random.Random(seed ^ 0x51A7)
        self.sensor_rng = random.Random(seed ^ 0xAE31)
        # Observer-only localization noise must not consume the established
        # traffic/perception generators or alter any braking decisions.
        self.navigation_rng = random.Random(seed ^ 0x6E4156)
        self.sim_time = 0.0
        self.cars: dict[str, Car] = {}
        self.pedestrians: list[dict] = []
        self.slots = [dict(slot) for slot in config["slots"]]
        self.crossings = [dict(crossing) for crossing in config.get("crossings", [])]
        self.zones = [dict(zone) for zone in config.get("zones", [])]
        vehicle = config["vehicle"]
        simulation = config["simulation"]
        self.length = float(vehicle.get("length", 4.5))
        self.width = float(vehicle.get("width", 1.8))
        self.margin = float(vehicle.get("margin", 1.0))
        self.max_speed = float(vehicle.get("max_speed", 4.0))
        self.acceleration = float(vehicle.get("acceleration", 1.0))
        self.braking = float(vehicle.get("braking", 2.0))
        self.sensor_range = float(vehicle.get("sensor_range", 18.0))
        self.sensor_timeout = float(vehicle.get("sensor_timeout", 0.5))
        self.position_uncertainty = float(vehicle.get("position_uncertainty", 0.1))
        # Observer-only short-range parking geometry. Its exact deterministic
        # fan rays neither consume sensor RNG nor feed the existing controller.
        self.parking_sensors = ParkingSensorModel(**vehicle.get("parking_sensors", {}))
        # A virtual measurement assumption, not a hardware accuracy claim.
        self.virtual_heading_uncertainty = 1.0
        self.nominal_sample_period = float(simulation.get("telemetry_period", 0.1))
        self._configured_sample_step = float(simulation.get("dt", 0.05))
        if not math.isfinite(self.nominal_sample_period) or self.nominal_sample_period <= 0:
            raise ValueError("A névleges mintavételi periódus pozitív, véges szám legyen")
        if (not math.isfinite(self._configured_sample_step)
                or not 0 < self._configured_sample_step <= 0.25):
            raise ValueError("A modell időlépése 0 és 0,25 s között legyen")
        self._sampling_step: float | None = None
        self._maximum_sampling_step = 0.0
        self._variable_sampling_steps = False
        self.sample_period = _quantized_sample_period(self.nominal_sample_period,
                                                      self._configured_sample_step)
        self.spawn_interval = float(simulation.get("spawn_interval", 8.0))
        self.max_active = int(simulation.get("max_active", 6))
        self.parking_dwell = float(simulation.get("parking_dwell", 25.0))
        self.production_limit = simulation.get("production_limit")
        self.origin = list(config.get("world", {}).get("production_exit", config.get("world", {}).get("origin", [14.0, 58.0])))
        occupied_count = min(len(self.slots), int(simulation.get("initial_occupied_count", 3)))
        self.initial_occupied = set(self.initial_rng.sample([slot["id"] for slot in self.slots], occupied_count))
        self.blocked: set[str] = set()
        self.sensor_failed_until = 0.0
        self._sensor_fault_active = False
        self._sensor_fault_counter = 0
        self._reported_states: dict[str, tuple[str, str]] = {}
        self._motion_phases: dict[str, str] = {}
        self._next_spawn = 0.0
        self._next_sample = 0.0
        self._car_counter = 0
        self._offered_cars = 0
        self._issued_ids: set[str] = set()
        self._vehicle_serial = 0
        self._buffer: list[tuple[str, float]] = []
        self._ped_counter = 0
        self._parking_seq = 0
        self._parking_sample_at: float | None = None
        self._outbox: list[tuple[str, dict]] = []
        self._events: list[dict] = []
        self._parking_times: list[float] = []
        self._yard_times: list[float] = []
        self._distances: list[float] = []
        self._collision_pairs: set[tuple[str, str]] = set()
        self._collisions = 0
        self._pedestrian_stops = 0
        self._connected = True
        self._next_pedestrian = {crossing["id"]: self.pedestrian_rng.uniform(7.0, 14.0) for crossing in self.crossings}
        self._automatic_pedestrians = simulation.get("automatic_pedestrians", True)
        if self.network_mode:
            self._initialise_cars(simulation.get("initial_cars", []))

    def _initialise_cars(self, initial_cars: list[dict]) -> None:
        """Admit configured poses before production begins, with no relocation.

        The scenario compiler validates that these positions share a routable
        ingress. A granted route is projected onto the *current* measured pose;
        its earlier prefix is never traversed backwards.
        """
        if len(initial_cars) > self.max_active:
            raise ValueError("A kezdeti autók száma meghaladja az aktív járműkorlátot")
        if self.production_limit is not None and len(initial_cars) > int(self.production_limit):
            raise ValueError("A kezdeti autók száma meghaladja a gyártási darabszámot")
        for index, entry in enumerate(initial_cars, 1):
            car_id = entry.get("id", f"CAR{index:03d}")
            if car_id in self.cars:
                raise ValueError("A kezdeti járműazonosítók nem ismétlődhetnek")
            car = Car(car_id, 0.0, float(entry["x"]), float(entry["y"]),
                      request_id=f"{car_id}-parking", heading=float(entry.get("heading", 0.0)))
            self.cars[car_id] = car
            self._issued_ids.add(car_id)
            self._offered_cars += 1
            self._car_counter += 1
            self._event("initial_vehicle", "Kezdeti jármű a forgatókönyv szerinti tényleges helyen", car_id,
                        position_m=[car.x, car.y], source="scenario_initial_pose",
                        request_id=car.request_id, offered_at_s=0.0, buffer_wait_s=0.0)
            self._report_state(car)
        if initial_cars:
            self._next_spawn = self.spawn_interval

    def _emit(self, topic: str, payload: dict) -> None:
        self._outbox.append((topic, {"sim_time": round(self.sim_time, 6), **payload}))

    def _event(self, kind: str, message: str, car_id: str | None = None, **details: Any) -> None:
        category = ("state" if kind in {"state_changed", "braking_started", "vehicle_stopped", "vehicle_resumed", "pedestrian_stop"}
                    else "fault" if kind in {"collision", "sensor_failure", "sensor_recovered", "car_frozen", "slot_blocked"}
                    else "decision" if kind == "parking_assigned"
                    else "transport" if kind in {"command_rejected", "command_duplicate"}
                    else "lifecycle")
        event = {"type": kind, "message": message, "car_id": car_id,
                 "sim_time": round(self.sim_time, 6), "category": category,
                 "level": "error" if kind == "collision" else "info", **details}
        self._events.append(event)
        self._events = self._events[-100:]
        self._emit("events", event)

    def _car_context(self, car: Car) -> dict:
        zone_id = next((zone for zone in car.zone_grants if zone not in car.zone_released), None)
        if zone_id is None and car.state == "waiting_zone":
            zone_id = next((zone for zone in car.zone_requests if zone not in car.zone_released), None)
        return {"request_id": car.zone_requests.get(zone_id, car.request_id),
                "parking_request_id": car.request_id, "reservation_id": car.reservation_id,
                "slot_id": car.slot_id, "zone_id": zone_id,
                "grant_id": car.zone_grants.get(zone_id),
                "speed_m_s": round(car.speed, 6), "position_m": [round(car.x, 6), round(car.y, 6)]}

    def _report_state(self, car: Car, previous_speed: float | None = None) -> None:
        """State/reason evidence only: this observer never changes physics."""
        previous = self._reported_states.get(car.id, (None, None))
        current = (car.state, car.wait_reason)
        if current == previous:
            return
        phase = "stopped" if car.speed <= 1e-6 else (
            "braking" if previous_speed is not None and car.speed < previous_speed - 1e-6 else "moving")
        labels = {"waiting": "várakozás", "driving": "haladás", "waiting_zone": "zónaengedélyre várás",
                  "pedestrian_stop": "zebra miatti fékezési/várakozási állapot", "parking": "parkolás",
                  "parked": "beparkolt", "communication_stop": "kommunikációs hiba miatti fékezési állapot",
                  "sensor_stop": "elavult érzékelés miatti fékezési állapot", "frozen": "injektált megállítási állapot"}
        self._event("state_changed", f"Járműállapot: {labels.get(car.state, car.state)}; " +
                    ("a jármű áll" if phase == "stopped" else "a jármű még mozog"), car.id,
                    from_state=previous[0], to_state=car.state, from_reason=previous[1], reason=car.wait_reason,
                    motion_phase=phase, level="warning" if car.state in {"communication_stop", "sensor_stop"} else "info",
                    **self._car_context(car))
        self._reported_states[car.id] = current

    def _report_motion(self, car: Car, previous_speed: float) -> None:
        phase = "stopped" if car.speed <= 1e-6 else ("braking" if car.speed < previous_speed - 1e-6 else "moving")
        previous_phase = self._motion_phases.get(car.id, "stopped")
        if phase == "braking" and previous_phase != "braking" and car.wait_reason:
            self._event("braking_started", "Fékezés megkezdődött; a jármű még mozog", car.id,
                        reason=car.wait_reason, motion_phase=phase, **self._car_context(car))
        if previous_speed > 1e-6 and car.speed <= 1e-6:
            self._event("vehicle_stopped", "A jármű ténylegesen megállt", car.id,
                        reason=car.wait_reason, motion_phase="stopped", **self._car_context(car))
        elif previous_speed <= 1e-6 and car.speed > 1e-6:
            self._event("vehicle_resumed", "A jármű mozgása megkezdődött vagy folytatódott", car.id,
                        reason=car.wait_reason, motion_phase="moving", **self._car_context(car))
        self._motion_phases[car.id] = phase

    def _command_notice(self, topic: str, payload: dict, reason: str, message: str,
                        duplicate: bool = False, **evidence: Any) -> None:
        p = payload if isinstance(payload, dict) else {}
        self._event("command_duplicate" if duplicate else "command_rejected", message, p.get("car_id"),
                    category="transport", level="info" if duplicate else "warning", reason=reason,
                    request_id=p.get("request_id"), reservation_id=p.get("reservation_id"),
                    zone_id=p.get("zone_id"), grant_id=p.get("grant_id"), command_kind=p.get("kind"),
                    topic=topic, **evidence)

    def _spawn(self) -> None:
        while (self.sim_time + 1e-9 >= self._next_spawn and
               (self.production_limit is None or self._offered_cars < int(self.production_limit))):
            self._offered_cars += 1
            if self.network_mode:
                self._vehicle_serial += 1
                while f"CAR{self._vehicle_serial:03d}" in self._issued_ids:
                    self._vehicle_serial += 1
                offered_id = f"CAR{self._vehicle_serial:03d}"
                self._issued_ids.add(offered_id)
            else:
                offered_id = f"CAR{self._offered_cars:03d}"
            self._buffer.append((offered_id, self._next_spawn))
            self._next_spawn += self.spawn_interval
        if not self._buffer or len(self.cars) >= self.max_active:
            return
        if any(_distance((car.x, car.y), self.origin) < self.length + self.margin + 2.0 for car in self.cars.values()):
            return
        self._car_counter += 1
        car_id, offered_at = self._buffer.pop(0)
        self.cars[car_id] = Car(car_id, offered_at, *self.origin, request_id=f"{car_id}-parking")
        self.cars[car_id].admitted_at = self.sim_time
        self._event("production_exit", "Jármű érkezett a gyártósori kijárathoz", car_id,
                    offered_at_s=offered_at, buffer_wait_s=round(self.sim_time - offered_at, 6))
        self._report_state(self.cars[car_id])

    def _request_parking(self, car: Car) -> None:
        if car.slot_id is None and self.sim_time >= car.next_request:
            self._emit("requests/parking", {"car_id": car.id, "request_id": car.request_id,
                                          "origin": [car.x, car.y] if self.network_mode else list(self.origin)})
            car.next_request = self.sim_time + 1.0

    def handle_command(self, topic: str, payload: dict) -> None:
        if not isinstance(payload, dict):
            self._command_notice(topic, {}, "invalid_payload", "A vezérlési üzenet nem objektum")
            return
        if payload.get("run_id", self.run_id) != self.run_id:
            self._command_notice(topic, payload, "wrong_run_id", "Másik futás vezérlési üzenete elutasítva",
                                 received_run_id=payload.get("run_id"), expected_run_id=self.run_id)
            return
        car_id = payload.get("car_id")
        car = self.cars.get(car_id) if isinstance(car_id, str) else None
        if car is None:
            self._command_notice(topic, payload, "unknown_car_id", "Ismeretlen vagy már kiadott jármű vezérlése elutasítva")
            return
        if not isinstance(topic, str) or not topic.endswith(car.id):
            self._command_notice(topic, payload, "topic_car_mismatch", "A vezérlési téma és a járműazonosító nem egyezik")
            return
        kind = payload.get("kind")
        if not isinstance(kind, str):
            self._command_notice(topic, payload, "invalid_command_kind", "Hiányzó vagy hibás vezérlési műveletazonosító")
            return
        if kind in {"parking_grant", "parking_wait"} and payload.get("request_id") != car.request_id:
            self._command_notice(topic, payload, "parking_request_mismatch", "Régi vagy hibás parkolókérési azonosító",
                                 expected_request_id=car.request_id)
            return
        if kind == "parking_grant":
            if car.reservation_id is not None:
                identical = payload.get("reservation_id") == car.reservation_id and payload.get("slot_id") == car.slot_id
                self._command_notice(topic, payload, "idempotent_grant" if identical else "reservation_conflict",
                                     "Ismételt parkolóengedély nem indítja újra a mozgást" if identical else "A meglévőtől eltérő foglalás elutasítva",
                                     duplicate=identical, current_reservation_id=car.reservation_id, current_slot_id=car.slot_id)
                return  # duplicate QoS1 response cannot restart motion
            slot_id = payload.get("slot_id")
            route = payload.get("route")
            if not isinstance(slot_id, str) or slot_id not in {slot["id"] for slot in self.slots}:
                self._command_notice(topic, payload, "unknown_slot_id", "Ismeretlen parkolóhelyre szóló engedély")
                return
            if not isinstance(payload.get("reservation_id"), str) or not payload["reservation_id"]:
                self._command_notice(topic, payload, "invalid_reservation_id", "Hiányzó vagy hibás foglalási azonosító")
                return
            if not isinstance(route, list) or len(route) < 2:
                self._command_notice(topic, payload, "invalid_route", "Hiányos parkolási útvonal")
                return
            if any(not isinstance(point, (list, tuple)) or len(point) != 2 or
                   any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in point)
                   for point in route):
                self._command_notice(topic, payload, "invalid_route_coordinates", "Hibás vagy nem véges útvonal-koordináták")
                return
            if _distance(route[0], self.origin) > 0.1:
                self._command_notice(topic, payload, "route_origin_mismatch", "Az útvonal nem a gyártósori kijáratnál kezdődik",
                                     expected_origin_m=list(self.origin))
                return
            route = [[float(value) for value in point] for point in route]
            expected_route = next(slot["route"] for slot in self.slots if slot["id"] == slot_id)
            if self.network_mode and (len(route) != len(expected_route) or
                                     any(_distance(received, expected) > 1e-6 for received, expected in zip(route, expected_route))):
                self._command_notice(topic, payload, "route_configuration_mismatch",
                                     "A kapott útvonal eltér a forgatókönyv hitelesített parkolóútvonalától")
                return
            lengths = _route_lengths(route)
            initial_progress, lateral = _project(route, lengths, car.x, car.y)
            if self.network_mode and lateral > .1:
                self._command_notice(topic, payload, "route_pose_mismatch",
                                     "Az engedélyezett útvonal nem érinti a jármű tényleges kezdőhelyét",
                                     position_m=[car.x, car.y], route_distance_m=lateral)
                return
            intervals = {zone["id"]: interval for zone in self.zones
                         if (interval := _rect_interval(route, lengths, zone)) is not None}
            if self.network_mode and any(enter - self.length / 2 - self.margin < initial_progress < leave + self.length / 2 + self.margin
                                         for enter, leave in intervals.values()):
                self._command_notice(topic, payload, "initial_pose_in_protected_zone",
                                     "A kezdeti járműhely a védett zóna megállási tartományába esik")
                return
            car.slot_id = slot_id
            car.reservation_id = payload.get("reservation_id")
            car.route = route
            car.lengths = lengths
            car.progress = initial_progress if self.network_mode else 0.0
            car.zone_intervals = intervals
            if self.network_mode:
                car.zone_released.update(zone_id for zone_id, (enter, leave) in intervals.items()
                                         if initial_progress >= leave + self.length / 2 + self.margin)
            car.state = "driving"
            car.wait_reason = ""
            self._event("parking_assigned", f"Parkoló kijelölve: {slot_id}", car.id, slot_id=slot_id,
                        reservation_id=car.reservation_id,
                        route_length_m=round(lengths[-1], 6), remaining_route_m=round(lengths[-1] - car.progress, 6),
                        route_source="directed_road_graph" if self.network_mode else "configured_polyline",
                        upcoming_zones=[zone_id for zone_id, interval in sorted(intervals.items(), key=lambda item: item[1][0])
                                        if zone_id not in car.zone_released])
        elif kind == "parking_wait" and car.slot_id is None:
            car.wait_reason = payload.get("reason", "Nincs kiosztható parkoló")
        elif kind == "zone_grant":
            zone_id = payload.get("zone_id")
            if not isinstance(zone_id, str):
                self._command_notice(topic, payload, "invalid_zone_id", "Hibás konfliktuszóna-azonosító")
                return
            if payload.get("request_id") == car.zone_requests.get(zone_id) and zone_id not in car.zone_released:
                if not isinstance(payload.get("grant_id"), str) or not payload["grant_id"]:
                    self._command_notice(topic, payload, "invalid_zone_grant_id", "Hiányzó vagy hibás zónaengedély-azonosító")
                    return
                if car.zone_grants.get(zone_id) == payload["grant_id"]:
                    self._command_notice(topic, payload, "idempotent_grant", "Azonos zónaengedély ismételten megérkezett", duplicate=True)
                car.zone_grants[zone_id] = payload.get("grant_id")
            else:
                self._command_notice(topic, payload, "zone_request_mismatch_or_closed", "Régi, lezárt vagy hibás zónakérés engedélye elutasítva")
        elif kind == "zone_wait":
            if not isinstance(payload.get("zone_id"), str):
                self._command_notice(topic, payload, "invalid_zone_id", "Hibás konfliktuszóna-azonosító")
                return
            if payload.get("request_id") == car.zone_requests.get(payload.get("zone_id")):
                car.wait_reason = payload.get("reason", "Konfliktuszóna foglalt")
            else:
                self._command_notice(topic, payload, "zone_request_mismatch", "Régi vagy hibás zónakérési azonosító")
        elif kind != "parking_wait":
            self._command_notice(topic, payload, "unknown_command_kind", "Ismeretlen vezérlési művelet")
        self._report_state(car)

    def control(self, action: str, data: dict) -> None:
        if action == "pedestrian":
            crossing_id = data.get("crossing_id", self.crossings[0]["id"] if self.crossings else "")
            crossing = next((item for item in self.crossings if item["id"] == crossing_id), None)
            if crossing is None:
                raise ValueError("Ismeretlen zebra")
            if any(ped["crossing_id"] == crossing_id and ped["state"] != "finished" for ped in self.pedestrians):
                return
            self._ped_counter += 1
            axis = crossing.get("axis", "vertical")
            half_span = float(crossing.get("half_span", 6.0))
            x = crossing["x"] - (half_span if axis == "horizontal" else 0.0)
            y = crossing["y"] - (half_span if axis == "vertical" else 0.0)
            self.pedestrians.append({"id": f"PED{self._ped_counter:03d}", "crossing_id": crossing_id,
                                     "x": x, "y": y, "state": "waiting", "progress": 0.0,
                                     "speed": float(crossing.get("pedestrian_speed", 1.4)),
                                     "created_at": self.sim_time})
            self._event("pedestrian_waiting", f"Gyalogos vár a járdaszegélynél: {crossing_id}", crossing_id=crossing_id)
        elif action == "block_slot":
            slot_id = data.get("slot_id")
            if slot_id not in {slot["id"] for slot in self.slots}:
                raise ValueError("Ismeretlen parkoló")
            if slot_id in self.blocked:
                self.blocked.remove(slot_id)
            else:
                self.blocked.add(slot_id)
            self._next_sample = self.sim_time
            self._event("slot_blocked", f"Parkoló lezárása megváltozott: {slot_id}", slot_id=slot_id,
                        blocked=slot_id in self.blocked)
        elif action == "sensor_failure":
            seconds = float(data.get("seconds", 5.0))
            if not 0 < seconds <= 120:
                raise ValueError("Az érzékelőhiba időtartama 0–120 s közé essen")
            self.sensor_failed_until = self.sim_time + seconds
            self._sensor_fault_active = True
            self._sensor_fault_counter += 1
            self._event("sensor_failure", f"Virtuális érzékelők kiesése: {seconds:g} s", duration_s=seconds,
                        level="warning", lifecycle="started", fault_id=f"sensor-{self._sensor_fault_counter}",
                        failure_until_s=round(self.sensor_failed_until, 6), reason="injected_sensor_fault")
        elif action == "freeze_car":
            car_id = data.get("car_id") or next((car.id for car in self.cars.values() if car.state != "parked"), None)
            if car_id not in self.cars:
                raise ValueError("Ismeretlen jármű")
            seconds = float(data.get("seconds", 8.0))
            if not 0 < seconds <= 120:
                raise ValueError("A megállítás időtartama 0–120 s közé essen")
            self.cars[car_id].frozen_until = self.sim_time + seconds
            self._event("car_frozen", f"Jármű megállítási kérése: {seconds:g} s", car_id, duration_s=seconds)
        else:
            raise ValueError("Ismeretlen modellvezérlési művelet")

    def _crossing_progress(self, car: Car, crossing: dict) -> float | None:
        if not car.route:
            return None
        progress, lateral = _project(car.route, car.lengths, crossing["x"], crossing["y"])
        return progress if lateral < 0.2 else None

    def _pedestrian_step(self, dt: float) -> None:
        if self._automatic_pedestrians:
            for crossing in self.crossings:
                if self.sim_time >= self._next_pedestrian[crossing["id"]]:
                    self.control("pedestrian", {"crossing_id": crossing["id"]})
                    self._next_pedestrian[crossing["id"]] = self.sim_time + self.pedestrian_rng.uniform(20.0, 32.0)
        for ped in self.pedestrians:
            crossing = next(item for item in self.crossings if item["id"] == ped["crossing_id"])
            half_span = float(crossing.get("half_span", 6.0))
            if ped["state"] == "waiting":
                safe = True
                for car in self.cars.values():
                    if car.state == "parked":
                        continue
                    if (self.network_mode and not car.route and
                            _distance((car.x, car.y), (crossing["x"], crossing["y"])) < self.length / 2 + .4):
                        # A configured initial car can occupy a crossing before
                        # any parking route has been granted. Lack of a route
                        # is not evidence that the physical crossing is empty.
                        safe = False
                        break
                    progress = self._crossing_progress(car, crossing)
                    if progress is None:
                        continue
                    gap = progress - car.progress
                    if abs(gap) < self.length / 2 + .4:
                        safe = False
                        break
                    # A crossing may start in front of a stopped vehicle.  Otherwise
                    # only a gap exceeding the entire crossing time is accepted.
                    if gap >= 0 and gap < self.max_speed * (2 * half_span / ped["speed"] + 1.0):
                        if car.speed > 0.05 or gap < self.length / 2 + .4:
                            safe = False
                            break
                if safe:
                    ped["state"] = "crossing"
                    self._event("pedestrian_crossing", f"Gyalogos áthalad: {ped['crossing_id']}",
                                crossing_id=ped["crossing_id"])
            if ped["state"] == "crossing":
                ped["progress"] = min(2 * half_span, ped["progress"] + dt * ped["speed"])
                if crossing.get("axis", "vertical") == "horizontal":
                    ped["x"] = crossing["x"] - half_span + ped["progress"]
                    ped["y"] = crossing["y"]
                else:
                    ped["x"] = crossing["x"]
                    ped["y"] = crossing["y"] - half_span + ped["progress"]
                if ped["progress"] >= 2 * half_span:
                    ped["state"] = "finished"
                    ped["finished_at"] = self.sim_time
                    self._event("pedestrian_finished", f"Zebra szabaddá vált: {ped['crossing_id']}",
                                crossing_id=ped["crossing_id"])
        self.pedestrians = [ped for ped in self.pedestrians
                            if ped["state"] != "finished" or self.sim_time - ped.get("finished_at", self.sim_time) < 1.0]

    def _proximity_bodies(self) -> list[dict]:
        """Supply declared simulation bodies; zebra markers are not obstacles.

        Initial occupancy has no active Car instance. A static rectangle at the
        occupied slot, oriented by its final approach, is an explicit geometric
        proxy. Supplied building x/y fields denote their upper-left map corner.
        Boundaries, parking markings and pedestrian state icons are excluded.
        """
        bodies = [{"id": car.id, "kind": "vehicle", "label": car.id,
                   "source": "sampled_simulation_vehicle_body", "x_m": car.x, "y_m": car.y,
                   "heading_deg": car.heading, "length_m": self.length, "width_m": self.width}
                  for car in self.cars.values()]
        for slot in self.slots:
            if slot["id"] not in self.initial_occupied:
                continue
            heading = 0.0
            for a, b in reversed(list(zip(slot.get("route", []), slot.get("route", [])[1:]))):
                if _distance(a, b) > 1e-9:
                    heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360
                    break
            bodies.append({"id": "OCCUPIED:" + slot["id"], "kind": "parked_vehicle",
                           "label": "Foglalt parkoló: " + slot["id"],
                           "source": "initial_occupied_slot_rectangular_proxy",
                           "x_m": slot["x"], "y_m": slot["y"], "heading_deg": heading,
                           "length_m": self.length, "width_m": self.width})
        for index, building in enumerate(self.config.get("world", {}).get("buildings", []), 1):
            bodies.append({"id": "BUILDING:" + str(building.get("id", index)), "kind": "building",
                           "label": building.get("label", "Csarnok"),
                           "source": "configured_map_building_rectangle",
                           "x_m": building["x"] + building["w"] / 2,
                           "y_m": building["y"] + building["h"] / 2, "heading_deg": 0.0,
                           "length_m": building["w"], "width_m": building["h"]})
        return bodies

    def _sample_local(self) -> None:
        if self.sim_time < self.sensor_failed_until:
            return
        proximity_bodies = self._proximity_bodies()
        for car in self.cars.values():
            car.sensor_at = self.sim_time
            car.perceived_cars = []
            for other in self.cars.values():
                if other.id == car.id or _distance((car.x, car.y), (other.x, other.y)) > self.sensor_range:
                    continue
                # Uniform bounded position error, with a conservative uncertainty
                # allowance in the stopping constraint.  No object identity loss.
                car.perceived_cars.append({"id": other.id,
                                           "x": other.x + self.sensor_rng.uniform(-self.position_uncertainty, self.position_uncertainty),
                                           "y": other.y + self.sensor_rng.uniform(-self.position_uncertainty, self.position_uncertainty),
                                           "speed": other.speed})
            car.perceived_crossings = {}
            for ped in self.pedestrians:
                if ped["state"] in {"waiting", "crossing"} and _distance((car.x, car.y), (ped["x"], ped["y"])) <= self.sensor_range:
                    car.perceived_crossings[ped["crossing_id"]] = ped["state"]
            # All navigation visualization data share this sample instant.
            # Neither these noisy measurements nor the route projection are
            # read by the ideal polyline motion or coordinator.
            previous = car.sensor_view
            measured_x = car.x + self.navigation_rng.uniform(-self.position_uncertainty, self.position_uncertainty)
            measured_y = car.y + self.navigation_rng.uniform(-self.position_uncertainty, self.position_uncertainty)
            measured_heading = (car.heading + self.navigation_rng.uniform(
                -self.virtual_heading_uncertainty, self.virtual_heading_uncertainty)) % 360
            sample_interval = self.sim_time - previous["sample_sim_time_s"] if previous else 0
            yaw_rate = None
            if previous and sample_interval > 1e-9:
                heading_delta = (car.heading - previous["reference_pose"]["heading_deg"] + 180) % 360 - 180
                yaw_rate = round(heading_delta / sample_interval, 6)
            car.sensor_view = {
                "schema_version": 1, "source": "virtual_onboard_bundle",
                "sample_sim_time_s": round(self.sim_time, 6),
                "sample_interval_s": round(sample_interval, 6) if previous else None,
                "pose": {"x_m": round(measured_x, 6), "y_m": round(measured_y, 6),
                         "heading_deg": round(measured_heading, 6),
                         "heading_uncertainty_deg": self.virtual_heading_uncertainty,
                         "source": "bounded_uniform_virtual_localization"},
                "reference_pose": {"x_m": round(car.x, 6), "y_m": round(car.y, 6),
                                   "heading_deg": round(car.heading, 6),
                                   "source": "sampled_simulation_reference"},
                "guidance": _signed_route_projection(car.route, car.lengths,
                                                       measured_x, measured_y, measured_heading),
                "odometry": {"speed_m_s": round(car.speed, 6), "distance_m": round(car.distance, 6),
                             "yaw_rate_deg_s": yaw_rate, "source": "ideal_model_proxy"},
                "proximity": self.parking_sensors.sample(
                    {"id": car.id, "x_m": car.x, "y_m": car.y, "heading_deg": car.heading,
                     "length_m": self.length, "width_m": self.width},
                    proximity_bodies, self.sim_time),
                "environment": {
                    "coverage": "radial_360_no_occlusion",
                    "source": "existing_sampled_virtual_proximity_sensor",
                    "vehicles": [{"id": other["id"], "x_m": round(other["x"], 6),
                                  "y_m": round(other["y"], 6), "speed_m_s": round(other["speed"], 6),
                                  "heading_deg": round(self.cars[other["id"]].heading, 6),
                                  "heading_source": "sampled_simulation_reference",
                                  "length_m": self.length, "width_m": self.width,
                                  "distance_m": round(_distance((car.x, car.y), (other["x"], other["y"])), 6)}
                                 for other in car.perceived_cars],
                    "crossings": [{"id": crossing["id"], "x_m": crossing["x"], "y_m": crossing["y"],
                                   "distance_m": round(_distance((car.x, car.y), (crossing["x"], crossing["y"])), 6),
                                   "position_source": "known_map_crossing",
                                   "pedestrian_state": car.perceived_crossings.get(crossing["id"], "no_pedestrian_observed"),
                                   "pedestrian_state_source": "sampled_onboard_pedestrian_state"}
                                  for crossing in self.crossings
                                  if (_distance((car.x, car.y), (crossing["x"], crossing["y"])) <= self.sensor_range
                                      or crossing["id"] in car.perceived_crossings)],
                },
            }

    def _sensor_age(self, car: Car) -> float | None:
        """An absent sample has no age; simulation time zero is a valid sample."""
        return max(0.0, self.sim_time - car.sensor_at) if car.sensor_view is not None else None

    def _sensor_fresh(self, car: Car) -> bool:
        age = self._sensor_age(car)
        return age is not None and age <= self.sensor_timeout

    def _update_sample_timing(self, dt: float) -> None:
        """Use actual integration ticks, including direct World.step callers.

        A fixed dt yields ceil(nominal/dt) ticks per sample. Once changing
        ticks are observed, nominal + the largest observed dt is a conservative
        bound for the due-on-next-tick policy. Fault outages are separate gaps.
        """
        if self._sampling_step is not None and not math.isclose(dt, self._sampling_step, rel_tol=1e-12):
            self._variable_sampling_steps = True
        self._sampling_step = dt
        self._maximum_sampling_step = max(self._maximum_sampling_step, dt)
        self.sample_period = (self.nominal_sample_period + self._maximum_sampling_step
                              if self._variable_sampling_steps
                              else _quantized_sample_period(self.nominal_sample_period, dt))

    def _sample_timing(self) -> dict:
        return {
            "policy": "first_integration_tick_at_or_after_nominal_deadline",
            "period_kind": ("variable_step_upper_bound" if self._variable_sampling_steps
                            else "fixed_step_quantized_interval"),
            "integration_step_s": self._sampling_step if self._sampling_step is not None else self._configured_sample_step,
            "integration_step_source": "actual_step" if self._sampling_step is not None else "configured_before_first_step",
            "maximum_observed_step_s": self._maximum_sampling_step if self._sampling_step is not None else None,
            "bound_excludes_fault_outages": True,
            "interpolated_samples": False,
        }

    def _sensor_bundle(self, car: Car) -> dict:
        """Return a detached observation; aging never generates a new sample."""
        sampled = car.sensor_view is not None
        age = self._sensor_age(car)
        result = copy.deepcopy(car.sensor_view) if sampled else {
            "schema_version": 1, "source": "virtual_onboard_bundle", "sample_sim_time_s": None,
            "sample_interval_s": None,
            "pose": None, "reference_pose": None,
            "guidance": {"available": False, "source": "noisy_pose_projection_on_assigned_map_route"},
            "odometry": None,
            "proximity": None,
            "environment": {"coverage": "radial_360_no_occlusion", "vehicles": [], "crossings": []},
        }
        result.update({"sampled": sampled, "age_s": round(age, 6) if sampled else None,
                       "fresh": self._sensor_fresh(car),
                       "fault_active": self.sim_time < self.sensor_failed_until,
                       "period_s": self.sample_period, "nominal_period_s": self.nominal_sample_period,
                       "sampling": self._sample_timing(), "timeout_s": self.sensor_timeout,
                       "range_m": self.sensor_range,
                       "position_uncertainty_m": self.position_uncertainty,
                       "position_euclidean_bound_m": round(math.sqrt(2) * self.position_uncertainty, 6),
                       "assumptions": {"position_error_distribution": "independent_uniform_per_axis",
                                       "heading_error_distribution": "uniform_plus_or_minus_one_degree",
                                       "localization_drives_motion": False,
                                       "environment_perception_drives_braking": True,
                                       "range_selection": "simulation_reference_center_distance",
                                       "occlusion_modelled": False,
                                       "odometry_yaw_rate_is_ideal_polyline_proxy": True}})
        return result

    def _sample_messages(self) -> None:
        if self.sim_time < self.sensor_failed_until:
            # The controller observer keeps recording decisions during a sensor
            # outage. It is a separate topic: the coordinator must not receive
            # fresh pose telemetry manufactured from simulation truth. The
            # embedded observation keeps its original sample clock and ages.
            for car in self.cars.values():
                self._emit(f"diagnostics/perception/{car.id}", {
                    "car_id": car.id, "seq": None,
                    "x": round(car.x, 6), "y": round(car.y, 6),
                    "speed": round(car.speed, 6), "heading": round(car.heading, 6),
                    "state": car.state, "wait_reason": car.wait_reason,
                    "distance_m": round(car.distance, 6),
                    "route_progress_m": round(car.progress, 6),
                    "period_s": self.sample_period,
                    "nominal_period_s": self.nominal_sample_period,
                    "sample_interval_s": car.sensor_view.get("sample_interval_s") if car.sensor_view else None,
                    "sampling": self._sample_timing(),
                    "slot_id": car.slot_id, "reservation_id": car.reservation_id,
                    "source": "simulation_controller_diagnostics",
                    "position_source": "post_step_simulation_reference",
                    "units": {"position": "m", "speed": "m/s"},
                    "control": copy.deepcopy(car.control_view),
                    "sensors": self._sensor_bundle(car),
                })
            return
        self._parking_seq += 1
        parking_interval = (self.sim_time - self._parking_sample_at
                            if self._parking_sample_at is not None else None)
        self._parking_sample_at = self.sim_time
        occupancy = {}
        for car in self.cars.values():
            if car.state == "parked" and car.slot_id:
                occupancy[car.slot_id] = car.id
                car.occupancy_observed = True
        self._emit("sensors/parking", {"seq": self._parking_seq, "source": "virtual_sensor",
                                      "period_s": self.sample_period,
                                      "nominal_period_s": self.nominal_sample_period,
                                      "sample_interval_s": round(parking_interval, 6) if parking_interval is not None else None,
                                      "sampling": self._sample_timing(),
                                      "slots": [{"id": slot["id"],
                                                 "occupied": slot["id"] in self.initial_occupied or slot["id"] in occupancy,
                                                 "blocked": slot["id"] in self.blocked} for slot in self.slots]})
        for car in self.cars.values():
            car.seq += 1
            self._emit(f"telemetry/{car.id}", {"car_id": car.id, "seq": car.seq,
                                               "x": round(car.x, 6), "y": round(car.y, 6),
                                               "speed": round(car.speed, 6), "state": car.state,
                                               "wait_reason": car.wait_reason, "distance_m": round(car.distance, 6),
                                               "period_s": self.sample_period,
                                               "nominal_period_s": self.nominal_sample_period,
                                               "sample_interval_s": car.sensor_view.get("sample_interval_s") if car.sensor_view else None,
                                               "sampling": self._sample_timing(),
                                               "heading": round(car.heading, 6), "route_progress_m": round(car.progress, 6),
                                               "route_length_m": round(car.lengths[-1], 6) if car.lengths else None,
                                               "slot_id": car.slot_id, "reservation_id": car.reservation_id,
                                               "units": {"position": "m", "speed": "m/s"},
                                               "source": "virtual_pose_sensor",
                                               "position_uncertainty_m": self.position_uncertainty,
                                               "control": copy.deepcopy(car.control_view),
                                               "sensors": self._sensor_bundle(car)})

    def _limits(self, car: Car) -> tuple[float, str, str]:
        stop_at = car.lengths[-1]
        state, reason = "driving", ""
        # A conservative frame-period allowance avoids moving a static stopping line
        # backwards between sensor updates.  Vehicles never reverse here, so a
        # previously observed leading-car pose is conservative during data age;
        # stale sensors separately trigger bounded braking after the timeout.
        allowance = self.position_uncertainty * math.sqrt(2) + self.max_speed * self.sample_period
        for other in car.perceived_cars:
            other_progress, lateral = _project(car.route, car.lengths, other["x"], other["y"])
            if lateral > self.width + self.margin + self.position_uncertainty or other_progress <= car.progress:
                continue
            # A non-reversing leader's lower position bound cannot retreat due
            # only to measurement noise; retaining that bound avoids demanding
            # instantaneous braking when a new noisy frame shifts backwards.
            other_progress = max(other_progress, car.observed_progress.get(other["id"], other_progress))
            car.observed_progress[other["id"]] = other_progress
            obstacle_stop = other_progress - self.length - self.margin - allowance
            if obstacle_stop < stop_at:
                stop_at, state, reason = obstacle_stop, "driving", "Követési távolság"
        for crossing in self.crossings:
            if crossing["id"] not in car.perceived_crossings:
                continue
            crossing_progress = self._crossing_progress(car, crossing)
            if crossing_progress is None or car.progress > crossing_progress + self.length / 2 + self.margin:
                continue
            crossing_stop = crossing_progress - self.length / 2 - self.margin - allowance
            if crossing["id"] not in car.seen_crossings:
                car.seen_crossings.add(crossing["id"])
                stopping_distance = car.speed ** 2 / (2 * self.braking) + car.speed * self.sample_period
                if (car.perceived_crossings[crossing["id"]] == "waiting" and
                        crossing_stop - car.progress < stopping_distance):
                    car.committed_crossings.add(crossing["id"])
            # A pedestrian arriving at the curb cannot demand a physically
            # impossible stop from a vehicle already committed to the crossing.
            # The pedestrian entry rule holds the person at the curb until that
            # vehicle has cleared.  This uses the sampled pedestrian state.
            if (car.perceived_crossings[crossing["id"]] == "waiting" and
                    (crossing["id"] in car.committed_crossings or car.progress > crossing_stop + .02)):
                continue
            if crossing_stop < stop_at:
                stop_at, state, reason = crossing_stop, "pedestrian_stop", f"Gyalogos: {crossing['id']}"
        zone_order = self.zones
        if self.network_mode:
            zone_order = sorted(self.zones, key=lambda zone: car.zone_intervals.get(zone["id"], (float("inf"),))[0])
        earlier_zone_active = False
        for zone in zone_order:
            zone_id = zone["id"]
            if zone_id in car.zone_released:
                continue
            interval = car.zone_intervals.get(zone_id) or _rect_interval(car.route, car.lengths, zone)
            if interval is None:
                continue
            enter, leave = interval
            stop_point = enter - self.length / 2 - self.margin
            if car.progress >= leave + self.length / 2 + self.margin and zone_id in car.zone_grants:
                grant_id = car.zone_grants.pop(zone_id)
                car.zone_released.add(zone_id)
                self._event("zone_exit", f"Jármű teljesen elhagyta a konfliktuszónát: {zone_id}", car.id,
                            zone_id=zone_id, grant_id=grant_id)
                continue
            if (stop_point - car.progress < self.sensor_range + self.max_speed ** 2 / (2 * self.braking)
                    and (not self.network_mode or not earlier_zone_active)):
                if zone_id not in car.zone_requests:
                    car.zone_requests[zone_id] = f"{car.id}-{zone_id}"
                if zone_id not in car.zone_grants and self.sim_time >= car.next_zone_request.get(zone_id, 0):
                    self._emit("requests/zone", {"car_id": car.id, "request_id": car.zone_requests[zone_id],
                                                "zone_id": zone_id})
                    car.next_zone_request[zone_id] = self.sim_time + 0.5
            # A network vehicle requests at most its next conflict resource.
            # This excludes hold-and-wait cycles across several junctions.
            # Scenario validation must also leave a full-car waiting bay
            # between junctions, otherwise exclusive resources can block one
            # another through physical geometry rather than protocol ownership.
            earlier_zone_active = True
            if zone_id not in car.zone_grants and stop_point < stop_at:
                stop_at, state, reason = stop_point, "waiting_zone", f"Zónaengedély: {zone_id}"
        if car.slot_id in self.blocked:
            slot = next(item for item in self.slots if item["id"] == car.slot_id)
            progress, _ = _project(car.route, car.lengths, slot["x"], slot["y"])
            slot_stop = progress - self.length - self.margin
            if slot_stop < stop_at:
                stop_at, state, reason = slot_stop, "waiting", "A kijelölt parkoló lezárt; feloldásra vár"
        return max(car.progress, stop_at), state, reason

    def _record_control(self, car: Car, dt: float, old_speed: float, desired: float,
                        stop_at: float | None, remaining: float | None,
                        target_state: str, reason: str) -> None:
        """Record the already executed controller evaluation without rerunning it.

        This is controller evidence, separate from the pre-motion sensor sample.
        The analytical braking distance assumes the model's constant deceleration;
        it excludes the reaction allowance and is not a physical safety guarantee.
        """
        car.control_view = {
            "source": "ideal_route_controller",
            "decision_sim_time_s": round(self.sim_time, 6),
            "motion_interval_s": dt,
            "target_speed_m_s": round(desired, 6),
            "speed_before_m_s": round(old_speed, 6),
            "speed_after_m_s": round(car.speed, 6),
            "applied_acceleration_m_s2": round((car.speed - old_speed) / dt, 6),
            "braking_distance_m": round(old_speed ** 2 / (2 * self.braking), 6),
            "reaction_allowance_m": round(old_speed * self.sample_period, 6),
            "reaction_period_s": self.sample_period,
            "nominal_period_s": self.nominal_sample_period,
            "braking_deceleration_m_s2": self.braking,
            "braking_assumption": "ideal_constant_deceleration_excluding_reaction_allowance",
            "stop_limit_progress_m": round(stop_at, 6) if stop_at is not None else None,
            "stop_limit_remaining_m": round(remaining, 6) if remaining is not None else None,
            "stop_reason": reason,
            "target_state": target_state,
            "result_state": car.state,
            "sensor_sample_sim_time_s": car.sensor_view.get("sample_sim_time_s") if car.sensor_view else None,
            "sensor_age_s": round(self._sensor_age(car), 6) if car.sensor_view is not None else None,
            "connected": self._connected,
            "zone_grants": dict(car.zone_grants),
        }

    def _car_step(self, car: Car, dt: float) -> None:
        if car.state == "parked":
            self._record_control(car, dt, car.speed, 0.0, car.progress, 0.0, "parked", car.wait_reason)
            return
        self._request_parking(car)
        if not car.route:
            old_speed = car.speed
            car.speed = 0.0
            car.state = "waiting"
            self._record_control(car, dt, old_speed, 0.0, None, None, "waiting", car.wait_reason)
            self._report_state(car)
            return
        stop_at, target_state, reason = self._limits(car)
        remaining = max(0.0, stop_at - car.progress)
        desired = min(self.max_speed, max(0.0, math.sqrt(2 * self.braking * remaining) - 2 * self.braking * dt))
        if not self._sensor_fresh(car):
            desired, target_state, reason = 0.0, "sensor_stop", "A helyi érzékelő adatának frissessége lejárt"
        if not self._connected:
            desired, target_state, reason = 0.0, "communication_stop", "Az MQTT-kapcsolat megszakadt"
        if self.sim_time < car.frozen_until:
            desired, target_state, reason = 0.0, "frozen", "Hibainjektálás: jármű megállítva"
        old_speed = car.speed
        if desired >= old_speed:
            car.speed = min(desired, old_speed + self.acceleration * dt)
        else:
            car.speed = max(desired, old_speed - self.braking * dt)
        # Stopping constraints are targets, not a physical teleport/clamp.  If
        # an injected fault makes a target infeasible, the car still obeys its
        # braking bound and the independent reference check can expose failure.
        route_remaining = max(0.0, car.lengths[-1] - car.progress)
        movement = min(route_remaining, max(0.0, (old_speed + car.speed) * 0.5 * dt))
        if stop_at == car.lengths[-1] and movement + 1e-9 >= route_remaining and route_remaining < self.max_speed * dt:
            car.speed = 0.0
        car.progress += movement
        car.distance += movement
        # Terminal placement uses a 2 cm numerical tolerance, below the stated
        # 0.1 m sensor-position bound.  The conservative discrete braking law
        # would otherwise asymptotically stop a few millimetres before the point.
        if stop_at == car.lengths[-1] and car.lengths[-1] - car.progress <= .02 and old_speed <= self.braking * dt:
            car.distance += car.lengths[-1] - car.progress
            car.progress = car.lengths[-1]
            car.speed = 0.0
        car.x, car.y, car.heading = _pose(car.route, car.lengths, car.progress)
        next_state = target_state if target_state in {"sensor_stop", "communication_stop", "frozen"} or remaining < 8.0 else "driving"
        if next_state == "pedestrian_stop" and car.state != "pedestrian_stop":
            self._pedestrian_stops += 1
            self._event("pedestrian_stop", "Helyi érzékelés alapján fékez a zebra előtt", car.id)
        car.state = next_state
        car.wait_reason = reason
        if car.progress >= car.lengths[-1] - 1e-8:
            car.state, car.speed, car.wait_reason = "parked", 0.0, "Kiadásra vár; egyszerűsített kiadási modell"
            car.parked_at = self.sim_time
            elapsed = self.sim_time - car.created_at
            self._parking_times.append(elapsed)
            self._yard_times.append(self.sim_time - car.admitted_at)
            self._distances.append(car.distance)
            self._event("parked", f"Jármű beparkolt: {car.slot_id}", car.id, slot_id=car.slot_id,
                        reservation_id=car.reservation_id, parking_time_s=round(elapsed, 6),
                        yard_time_s=round(self.sim_time - car.admitted_at, 6),
                        distance_m=round(car.distance, 6))
        self._record_control(car, dt, old_speed, desired, stop_at, remaining, target_state, reason)
        self._report_state(car, old_speed)
        self._report_motion(car, old_speed)

    @staticmethod
    def _body_corners(body: dict) -> list[tuple[float, float]]:
        """Use the same declared oriented rectangle as the proximity observer."""
        angle = math.radians(body["heading_deg"])
        cosine, sine = math.cos(angle), math.sin(angle)
        half_l, half_w = body["length_m"] / 2, body["width_m"] / 2
        return [(body["x_m"] + along * cosine - lateral * sine,
                 body["y_m"] + along * sine + lateral * cosine)
                for along, lateral in ((-half_l, -half_w), (half_l, -half_w),
                                       (half_l, half_w), (-half_l, half_w))]

    def _corners(self, car: Car) -> list[tuple[float, float]]:
        return self._body_corners({"x_m": car.x, "y_m": car.y, "heading_deg": car.heading,
                                   "length_m": self.length, "width_m": self.width})

    @staticmethod
    def _overlap(first: list[tuple[float, float]], second: list[tuple[float, float]]) -> bool:
        for polygon in (first, second):
            for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                axis = (-(b[1] - a[1]), b[0] - a[0])
                first_projection = [point[0] * axis[0] + point[1] * axis[1] for point in first]
                second_projection = [point[0] * axis[0] + point[1] * axis[1] for point in second]
                if max(first_projection) <= min(second_projection) + 1e-9 or max(second_projection) <= min(first_projection) + 1e-9:
                    return False
        return True

    def _check_collisions(self) -> None:
        cars = list(self.cars.values())
        # The short-range observer already declares these surfaces physical.
        # Check their true rectangles independently of sensor range, freshness,
        # ray gaps or controller decisions. Active vehicles are checked below;
        # including them here would count the same pair twice.
        static_bodies = [body for body in self._proximity_bodies() if body["kind"] != "vehicle"]
        static_geometry = [(body, self._body_corners(body),
                            math.hypot(body["length_m"], body["width_m"]) / 2)
                           for body in static_bodies]
        car_radius = math.hypot(self.length, self.width) / 2
        for index, first in enumerate(cars):
            first_corners = self._corners(first)
            for second in cars[index + 1:]:
                if _distance((first.x, first.y), (second.x, second.y)) > self.length + self.width:
                    continue
                pair = tuple(sorted((first.id, second.id)))
                if pair not in self._collision_pairs and self._overlap(first_corners, self._corners(second)):
                    self._collision_pairs.add(pair)
                    self._collisions += 1
                    self._event("collision", "A referenciaellenőrzés járműütközést észlelt", first.id,
                                other_id=second.id, collision_kind="vehicle_vehicle")
            for body, corners, radius in static_geometry:
                if _distance((first.x, first.y), (body["x_m"], body["y_m"])) > car_radius + radius:
                    continue
                pair = tuple(sorted((first.id, body["id"])))
                if pair not in self._collision_pairs and self._overlap(first_corners, corners):
                    self._collision_pairs.add(pair)
                    self._collisions += 1
                    self._event("collision", "A referenciaellenőrzés statikus akadállyal való ütközést észlelt",
                                first.id, other_id=body["id"],
                                collision_kind="vehicle_building" if body["kind"] == "building" else "vehicle_parked_proxy",
                                obstacle_kind=body["kind"], obstacle_label=body["label"],
                                obstacle_source=body["source"], reference_shape="oriented_rectangle")
            angle = math.radians(first.heading)
            for ped in self.pedestrians:
                dx, dy = ped["x"] - first.x, ped["y"] - first.y
                along = abs(dx * math.cos(angle) + dy * math.sin(angle))
                lateral = abs(-dx * math.sin(angle) + dy * math.cos(angle))
                pair = (first.id, ped["id"])
                if along < self.length / 2 + 0.25 and lateral < self.width / 2 + 0.25 and pair not in self._collision_pairs:
                    self._collision_pairs.add(pair)
                    self._collisions += 1
                    self._event("collision", "A referenciaellenőrzés gyalogosütközést észlelt", first.id,
                                other_id=ped["id"], collision_kind="vehicle_pedestrian")

    def step(self, dt: float, connected: bool = True) -> list[tuple[str, dict]]:
        if not math.isfinite(dt) or dt <= 0 or dt > 0.25:
            raise ValueError("A modell időlépése 0 és 0,25 s között legyen")
        self._update_sample_timing(dt)
        self.sim_time += dt
        self._connected = connected
        if self._sensor_fault_active and self.sim_time >= self.sensor_failed_until:
            self._sensor_fault_active = False
            self._event("sensor_recovered", "A virtuális érzékelők hibainjektálása véget ért; a mintavétel folytatódhat",
                        lifecycle="recovered", fault_id=f"sensor-{self._sensor_fault_counter}",
                        reason="injected_fault_elapsed", next_sample_due_s=round(self._next_sample, 6))
        for car_id, car in list(self.cars.items()):
            if car.parked_at is not None and car.occupancy_observed and connected and self.sim_time >= self.sensor_failed_until:
                if self.sim_time - car.parked_at >= self.parking_dwell:
                    self._event("departed", "Jármű kiadva: a kiadási út egyszerűsítetten nem modellezett", car.id,
                                slot_id=car.slot_id, reservation_id=car.reservation_id,
                                dispatch_model="abstract_removal")
                    del self.cars[car_id]
                    self._reported_states.pop(car_id, None)
                    self._motion_phases.pop(car_id, None)
        self._spawn()
        self._pedestrian_step(dt)
        sample_due = self.sim_time + 1e-9 >= self._next_sample
        if sample_due:
            self._sample_local()
        for car in list(self.cars.values()):
            self._car_step(car, dt)
        self._check_collisions()
        if sample_due:
            self._sample_messages()
            # Keep the established scheduler: a real sample is taken at the
            # first physics tick reaching this nominal deadline. period_s
            # describes its tick-quantized cadence or variable-step bound.
            self._next_sample = self.sim_time + self.nominal_sample_period
        messages, self._outbox = self._outbox, []
        # Initial physical inventory reaches the coordinator before allocation
        # requests.  Subsequent departure still needs a later inventory frame.
        if self._parking_seq == 1:
            messages.sort(key=lambda item: 0 if item[0] == "sensors/parking" else 1)
        return messages

    def snapshot(self) -> dict:
        occupied = {car.slot_id: car.id for car in self.cars.values() if car.state == "parked"}
        reserved = {car.slot_id: car.id for car in self.cars.values() if car.slot_id and car.state != "parked"}
        parking = []
        for slot in self.slots:
            slot_id = slot["id"]
            state = "occupied" if slot_id in self.initial_occupied or slot_id in occupied else "free"
            if state == "free" and slot_id in reserved:
                state = "reserved"
            if slot_id in self.blocked:
                state = "blocked"
            parking.append({"id": slot_id, "x": slot["x"], "y": slot["y"], "state": state,
                            "car_id": occupied.get(slot_id) or reserved.get(slot_id)})
        warnings = []
        if self.sim_time < self.sensor_failed_until:
            warnings.append("Virtuális érzékelők kiesése aktív; frissességi időkorlát után helyi megállás.")
        if self._collisions:
            warnings.append("A referenciaellenőrzés ütközést észlelt. A futás nem teljesíti az ütközésmentességi feltételt.")
        world = self.config.get("world", {})
        return {"run_id": self.run_id, "sim_time": round(self.sim_time, 3), "connected": self._connected,
                "map": {"width": world.get("width", 140), "height": world.get("height", 90),
                        "network": self.config.get("network"),
                        "parking_areas": self.config.get("parking_areas", world.get("parking_areas", [])),
                        "production_exits": self.config.get("production_exits", world.get("production_exits", [])),
                        "layout": self.config.get("layout", world.get("layout", {})),
                        "roads": world.get("roads", [[[14, 58], [122, 58], [122, 24], [34, 24]]]),
                        "buildings": world.get("buildings", [{"x": 2, "y": 66, "w": 30, "h": 18, "label": "Gyártósor"}]),
                        "crossings": self.crossings, "slots": [{"id": slot["id"], "x": slot["x"], "y": slot["y"],
                                                                  "area_id": slot.get("area_id"),
                                                                  "route": [list(point) for point in slot.get("route", [])]}
                                                                 for slot in self.slots],
                        "zones": self.zones, "vehicle_length": self.length, "vehicle_width": self.width},
                "cars": [{"id": car.id, "x": round(car.x, 4), "y": round(car.y, 4), "heading": car.heading,
                          "speed": round(car.speed, 4), "state": car.state, "slot_id": car.slot_id,
                          "elapsed_s": round((car.parked_at if car.parked_at is not None else self.sim_time) - car.created_at, 4),
                          "yard_elapsed_s": round((car.parked_at if car.parked_at is not None else self.sim_time) - car.admitted_at, 4),
                          "sensor_age_s": round(self._sensor_age(car), 4) if car.sensor_view is not None else None,
                          "sensor_ok": self._sensor_fresh(car),
                          "control": copy.deepcopy(car.control_view),
                          "sensors": self._sensor_bundle(car),
                          "request_id": car.request_id, "reservation_id": car.reservation_id,
                          "route_progress_m": round(car.progress, 4),
                          "remaining_route_m": round(car.lengths[-1] - car.progress, 4) if car.lengths else None,
                          "zone_grants": dict(car.zone_grants),
                          "next_zone_id": next((zone_id for zone_id, interval in sorted(car.zone_intervals.items(), key=lambda item: item[1][0])
                                                if zone_id not in car.zone_released), None),
                          "route": car.route, "wait_reason": car.wait_reason, "distance_m": round(car.distance, 4)}
                         for car in self.cars.values()],
                "pedestrians": [{key: ped[key] for key in ("id", "x", "y", "state", "crossing_id")} for ped in self.pedestrians],
                "parking": parking,
                "zones": [{"id": zone["id"], "owner": next((car.id for car in self.cars.values()
                                                              if zone["id"] in car.zone_grants), None)} for zone in self.zones],
                "metrics": {"completed": len(self._parking_times), "active": len(self.cars),
                            "offered": self._offered_cars, "admitted": self._car_counter,
                            "buffered": len(self._buffer), "unfinished": self._offered_cars - len(self._parking_times),
                            "avg_parking_time_s": sum(self._parking_times) / len(self._parking_times) if self._parking_times else 0,
                            "avg_yard_time_s": sum(self._yard_times) / len(self._yard_times) if self._yard_times else 0,
                            "avg_distance_m": sum(self._distances) / len(self._distances) if self._distances else 0,
                            "collisions": self._collisions, "pedestrian_stops": self._pedestrian_stops},
                "events": list(self._events[-30:]), "warnings": warnings}
