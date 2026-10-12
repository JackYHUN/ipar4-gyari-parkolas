"""Parking and conflict-zone decisions using received messages only.

No World import and no access to its private ground truth. Leases never expire
into a free space: departure plus a later physical sensor sample is required.
"""
import math
import time
from collections import deque


def _project_route(route, x, y):
    """Project a received pose onto configured geometry, without World data."""
    length, best = 0.0, (float("inf"), 0.0)
    for a, b in zip(route, route[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        segment = math.hypot(dx, dy)
        fraction = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / max(segment * segment, 1e-12)))
        lateral = math.hypot(x - a[0] - fraction * dx, y - a[1] - fraction * dy)
        if lateral < best[0]:
            best = lateral, length + fraction * segment
        length += segment
    return best[1], best[0], length


def _zone_interval(route, zone):
    low_x, high_x = zone["x"] - zone["w"] / 2, zone["x"] + zone["w"] / 2
    low_y, high_y = zone["y"] - zone["h"] / 2, zone["y"] + zone["h"] / 2
    distance, hits = 0.0, []
    for a, b in zip(route, route[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        segment = math.hypot(dx, dy)
        low, high, valid = 0.0, 1.0, True
        for p, q in ((-dx, a[0] - low_x), (dx, high_x - a[0]), (-dy, a[1] - low_y), (dy, high_y - a[1])):
            if abs(p) < 1e-12:
                if q < 0:
                    valid = False
                    break
            elif p < 0:
                low = max(low, q / p)
            else:
                high = min(high, q / p)
        if valid and low <= high:
            hits.extend((distance + low * segment, distance + high * segment))
        distance += segment
    return (min(hits), max(hits)) if hits else None


class Coordinator:
    def __init__(self, config, run_id, strategy="nearest"):
        if strategy not in ("nearest", "fixed"):
            raise ValueError("Ismeretlen kiosztási szabály")
        self.config, self.run_id, self.strategy = config, run_id, strategy
        self.network_mode = bool(config.get("network"))
        self.slots = {s["id"]: dict(s, occupied=None, blocked=False,
                                 car_id=None, reservation_id=None) for s in config["slots"]}
        self.zones = {z["id"]: dict(z, owner=None, grant_id=None) for z in config["zones"]}
        self.telemetry, self.responses, self.zone_responses = {}, {}, {}
        self.zone_queues = {z["id"]: [] for z in config["zones"]}
        self._car_slots = {}
        self._route_zones = {slot_id: {zone_id: interval for zone_id, zone in self.zones.items()
                                      if (interval := _zone_interval(slot["route"], zone)) is not None}
                             for slot_id, slot in self.slots.items()} if self.network_mode else {}
        self.sensor_seq, self.sensor_received_ns = -1, None
        self.pending_departures, self.pending_exits = {}, {}
        self.serial, self.sim_time = 0, 0.0
        self._audit_buffer = deque(maxlen=1000)
        self._audit_dropped_since_drain = 0
        self._audit_waits = {}

    def _assigned_slot(self, car_id):
        slot = self.slots.get(self._car_slots.get(car_id))
        if slot and slot["car_id"] == car_id:
            return slot
        slot = next((slot for slot in self.slots.values() if slot["car_id"] == car_id), None)
        if slot:
            self._car_slots[car_id] = slot["id"]
        else:
            self._car_slots.pop(car_id, None)
        return slot

    def _assigned_route(self, car_id):
        slot = self._assigned_slot(car_id)
        return slot["route"] if slot else None

    def _network_eligibility(self, car_id, zone_id, now):
        """Check only received telemetry and the centrally assigned route."""
        sample = self.telemetry.get(car_id)
        if not sample or now - sample["received_ns"] > self.config["mqtt"]["freshness_s"] * 1e9:
            return False, "stale_vehicle_telemetry", {}
        slot = self._assigned_slot(car_id)
        if not slot:
            return False, "no_assigned_route", {}
        route = slot["route"]
        zone = self.zones[zone_id]
        intervals = self._route_zones[slot["id"]]
        interval = intervals.get(zone_id)
        if interval is None:
            return False, "zone_not_on_assigned_route", {}
        progress, lateral, _ = _project_route(route, sample["x"], sample["y"])
        half = self.config["vehicle"]["length"] / 2
        margin = self.config["vehicle"]["margin"]
        evidence = dict(route_progress_m=progress, route_lateral_distance_m=lateral,
                        zone_enter_progress_m=interval[0], zone_leave_progress_m=interval[1])
        if lateral > .5:
            return False, "pose_off_assigned_route", evidence
        if progress >= interval[1] + half + margin:
            return False, "zone_already_passed", evidence
        for earlier_id, earlier in self.zones.items():
            earlier_interval = intervals.get(earlier_id)
            if (earlier_id != zone_id and earlier_interval and earlier_interval[0] < interval[0]
                    and progress < earlier_interval[1] + half + margin):
                return False, "earlier_route_zone_not_cleared", dict(evidence, earlier_zone_id=earlier_id)
        holding = next((zid for zid, item in self.zones.items() if zid != zone_id and item["owner"] == car_id), None)
        if holding:
            return False, "other_zone_still_owned", dict(evidence, earlier_zone_id=holding)
        lookahead = (self.config["vehicle"].get("sensor_range", 18.0) +
                     self.config["vehicle"]["max_speed"] ** 2 / (2 * self.config["vehicle"]["braking"]))
        if interval[0] - half - margin - progress >= lookahead:
            return False, "zone_not_yet_near", evidence
        # A car behind another car on the same directed approach cannot lock a
        # junction needed by that physical leader. Different incoming branches
        # are arbitrated by their eligible request order below.
        for other_id, other in self.telemetry.items():
            if other_id == car_id or other.get("state") == "parked":
                continue
            other_slot = self._assigned_slot(other_id)
            if not other_slot or zone_id not in self._route_zones[other_slot["id"]]:
                continue
            other_progress, other_lateral, _ = _project_route(route, other["x"], other["y"])
            if (other_lateral <= self.config["vehicle"]["width"] + .2
                    and progress + .1 < other_progress <= interval[1] + half + margin):
                return False, "physical_leader_ahead", dict(evidence, physical_leader_id=other_id,
                                                           leader_telemetry_age_s=(now - other["received_ns"]) / 1e9)
        return True, None, evidence

    def drain_audit(self):
        """Consume decision/transport evidence without affecting decisions.

        Identical waiting decisions are aggregated over five simulation seconds;
        ``repeats`` counts this record and suppressed equal decisions since the
        previous emitted record. Raw MQTT traffic remains the transport evidence.
        """
        records = list(self._audit_buffer)
        self._audit_buffer.clear()
        if self._audit_dropped_since_drain:
            records.insert(0, dict(category="transport", level="warning", event_type="audit_records_dropped",
                                   message="A döntésnapló fogyasztója lemaradt; a legrégebbi diagnosztikai rekordok kikerültek",
                                   sim_time=self.sim_time, car_id=None, request_id=None, reservation_id=None, zone_id=None,
                                   reason="bounded_audit_buffer", dropped_records=self._audit_dropped_since_drain,
                                   capacity=1000))
            self._audit_dropped_since_drain = 0
        return records

    def _audit(self, category, event_type, message, payload=None, level="info",
               reason=None, throttle=False, **evidence):
        p = payload if isinstance(payload, dict) else {}
        record = dict(category=category, level=level, event_type=event_type,
                      message=message, sim_time=p.get("sim_time", self.sim_time),
                      car_id=p.get("car_id"), request_id=p.get("request_id"),
                      reservation_id=p.get("reservation_id"), zone_id=p.get("zone_id"),
                      reason=reason, **evidence)
        if throttle:
            key = (event_type, record["car_id"], record["request_id"], reason)
            previous = self._audit_waits.get(key)
            if previous and self.sim_time - previous["sim_time"] < 5.0:
                previous["suppressed"] += 1
                return
            record["repeats"] = 1 + (previous["suppressed"] if previous else 0)
            record["aggregation_interval_s"] = 5.0
            if key not in self._audit_waits and len(self._audit_waits) >= 2048:
                self._audit_waits.pop(next(iter(self._audit_waits)))
            self._audit_waits[key] = dict(sim_time=self.sim_time, suppressed=0)
        if len(self._audit_buffer) == self._audit_buffer.maxlen:
            self._audit_dropped_since_drain += 1
        self._audit_buffer.append(record)

    def _reject(self, topic, payload, reason, message, **evidence):
        self._audit("transport", "message_rejected", message, payload,
                    level="warning", reason=reason, topic=topic, **evidence)
        return []

    @staticmethod
    def _valid_id(value):
        return isinstance(value, str) and bool(value)

    @staticmethod
    def _finite_number(value):
        # JSON booleans are not physical measurements. In Python ``bool`` is
        # an ``int`` subclass, so an isinstance-only check admits True as 1 m.
        # Reject very large integers that cannot become a finite float too.
        if type(value) not in (int, float):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    def _id(self, kind):
        self.serial += 1
        return f"{self.run_id}-{kind}-{self.serial}"

    def _fresh(self, now):
        return self.sensor_received_ns is not None and (
            now - self.sensor_received_ns) / 1e9 <= self.config["mqtt"]["freshness_s"]

    @staticmethod
    def _reply(car_id, kind, payload):
        return [(f"commands/{car_id}", dict(kind=kind, car_id=car_id, **payload))]

    def _clear_exits(self):
        # A release event alone is insufficient. A current owner sample must
        # place the entire vehicle outside the protected rectangle.
        half = self.config["vehicle"]["length"] / 2
        for zid, event in list(self.pending_exits.items()):
            z = self.zones[zid]
            t = self.telemetry.get(z["owner"], {})
            if t.get("sim_time", -1) < event["sim_time"]:
                continue
            route_progress = None
            if self.network_mode:
                slot = self._assigned_slot(z["owner"])
                route = slot["route"] if slot else None
                interval = self._route_zones[slot["id"]].get(zid) if slot else None
                if not interval or "x" not in t:
                    continue
                route_progress, lateral, _ = _project_route(route, t["x"], t["y"])
                if (lateral > .5 or route_progress + 1e-5 < interval[1] + half + self.config["vehicle"]["margin"]):
                    continue
            if "x" in t and (abs(t["x"] - z["x"]) > z["w"] / 2 + half or
                             abs(t["y"] - z["y"]) > z["h"] / 2 + half):
                self._audit("decision", "zone_released", "Konfliktuszóna felszabadítva fizikai kilépési bizonyítékkal", event,
                            reason="matching_exit_and_whole_vehicle_outside", released_owner=z["owner"],
                            grant_id=z["grant_id"], telemetry_seq=t["seq"], telemetry_sim_time=t["sim_time"],
                            exit_event_sim_time=event["sim_time"], position_m=[t["x"], t["y"]],
                            zone_bounds_m={"x": z["x"], "y": z["y"], "w": z["w"], "h": z["h"]},
                            vehicle_half_length_m=half, whole_vehicle_outside=True,
                            route_progress_m=route_progress, exit_direction_verified=self.network_mode)
                z.update(owner=None, grant_id=None)
                self.pending_exits.pop(zid)

    def handle(self, topic, payload, now_ns=None):
        now = time.perf_counter_ns() if now_ns is None else now_ns
        if not isinstance(payload, dict):
            return self._reject(topic, {}, "invalid_payload", "Az MQTT-üzenet tartalma nem objektum")
        if payload.get("run_id", self.run_id) != self.run_id:
            return self._reject(topic, payload, "wrong_run_id", "Másik futáshoz tartozó üzenet elutasítva",
                                received_run_id=payload.get("run_id"), expected_run_id=self.run_id)
        is_telemetry = isinstance(topic, str) and topic.startswith("telemetry/")
        sent = payload.get("sent_ns", None if is_telemetry else now)
        # The publisher uses perf_counter_ns(), an integer clock shared by
        # these local processes. Do not coerce strings, floats or booleans.
        if type(sent) is not int or sent < 0 or not self._finite_number(sent):
            return self._reject(topic, payload, "invalid_timestamp", "Hibás küldési időbélyeg")
        if sent > now + 1_000_000_000 or now - sent > self.config["mqtt"]["freshness_s"] * 1e9:
            return self._reject(topic, payload, "future_timestamp" if sent > now else "stale_message",
                                "Az üzenet időbélyege nem teljesíti a frissességi feltételt",
                                message_age_s=(now - sent) / 1e9, freshness_limit_s=self.config["mqtt"]["freshness_s"])
        sim_time = payload.get("sim_time", None if is_telemetry else 0)
        if not self._finite_number(sim_time) or sim_time < 0:
            return self._reject(topic, payload, "invalid_sim_time", "Hibás vagy nem véges szimulációs időbélyeg")
        if is_telemetry:
            car, seq = payload.get("car_id"), payload.get("seq")
            # Validate the entire decision-relevant frame before advancing
            # any clock, accepted sequence, freshness or pending zone release.
            # The received payload is retained unchanged after acceptance.
            if (not self._valid_id(car) or type(seq) is not int or seq < 0 or
                    any(not self._finite_number(payload.get(axis)) for axis in ("x", "y"))):
                return self._reject(topic, payload, "invalid_telemetry", "Hibás járműtelemetria")
            if topic != f"telemetry/{car}":
                return self._reject(topic, payload, "telemetry_topic_mismatch", "A járműazonosító nem egyezik a telemetriatémával",
                                    expected_topic=f"telemetry/{car}")
            old = self.telemetry.get(car, {})
            if seq <= old.get("seq", -1):
                return self._reject(topic, payload, "non_increasing_sequence", "Régi vagy ismételt járműtelemetria elutasítva",
                                    received_seq=seq, accepted_seq=old.get("seq", -1))
            self.sim_time = max(self.sim_time, sim_time)
            self.telemetry[car] = dict(payload, received_ns=now)
            self._clear_exits()
            return []
        if topic == "sensors/parking":
            seq = payload.get("seq", -1)
            if type(seq) is not int or not isinstance(payload.get("slots"), list):
                return self._reject(topic, payload, "invalid_sensor_frame", "Hibás parkolóérzékelő-keret")
            if seq <= self.sensor_seq:
                return self._reject(topic, payload, "non_increasing_sequence", "Régi vagy ismételt parkolóérzékelő-keret elutasítva",
                                    received_seq=seq, accepted_seq=self.sensor_seq)
            # This topic carries a complete inventory snapshot, not deltas.
            # Validate every item before advancing its clock or applying any
            # state/release. A malformed tail must not certify an earlier
            # empty reading or refresh unchanged, previously stale spaces.
            readings = payload["slots"]
            observed_ids = set()
            for reading in readings:
                if (not isinstance(reading, dict) or not self._valid_id(reading.get("id"))
                        or type(reading.get("occupied")) is not bool
                        or type(reading.get("blocked", False)) is not bool):
                    return self._reject(topic, payload, "invalid_slot_reading", "Hibás parkolóhely-mérési elem; a teljes keret elutasítva",
                                        sensor_seq=seq)
                slot_id = reading["id"]
                if slot_id not in self.slots:
                    return self._reject(topic, payload, "unknown_slot_id", "Ismeretlen parkolóhely érzékelési adata; a teljes keret elutasítva",
                                        slot_id=slot_id, sensor_seq=seq)
                if slot_id in observed_ids:
                    return self._reject(topic, payload, "duplicate_slot_id", "Ismételt parkolóhely a pillanatképben; a teljes keret elutasítva",
                                        slot_id=slot_id, sensor_seq=seq)
                observed_ids.add(slot_id)
            if observed_ids != self.slots.keys():
                return self._reject(topic, payload, "incomplete_sensor_frame", "Hiányos parkolóérzékelő-pillanatkép; a teljes keret elutasítva",
                                    sensor_seq=seq, expected_slot_count=len(self.slots),
                                    observed_slot_count=len(observed_ids), missing_slot_ids=sorted(self.slots.keys() - observed_ids))
            self.sim_time = max(self.sim_time, sim_time)
            previous_received = self.sensor_received_ns
            changes = []
            self.sensor_seq, self.sensor_received_ns = seq, now
            for reading in readings:
                s = self.slots[reading["id"]]
                before = {"occupied": s["occupied"], "blocked": s["blocked"]}
                s.update(occupied=reading["occupied"], blocked=reading.get("blocked", False))
                if before != {"occupied": s["occupied"], "blocked": s["blocked"]}:
                    changes.append(dict(slot_id=s["id"], previous=before, occupied=s["occupied"], blocked=s["blocked"]))
                dep = self.pending_departures.get(s["id"])
                if dep and seq > dep["after_seq"] and not s["occupied"]:
                    self._audit("decision", "parking_released", "Parkolófoglalás felszabadítva későbbi üres érzékelőkeret alapján",
                                dict(payload, car_id=s["car_id"], reservation_id=s["reservation_id"]),
                                reason="matching_departure_and_later_empty_sample", slot_id=s["id"],
                                after_sensor_seq=dep["after_seq"], confirming_sensor_seq=seq, occupied=False)
                    s.update(car_id=None, reservation_id=None)
                    self.pending_departures.pop(s["id"])
            if changes:
                self._audit("transport", "parking_observation_changed", "A virtuális parkolóérzékelő állapotváltozást jelentett",
                            payload, reason="fresh_increasing_sensor_frame", sensor_seq=seq, changes=changes)
            if previous_received is None or (now - previous_received) / 1e9 > self.config["mqtt"]["freshness_s"]:
                self._audit("transport", "parking_sensor_fresh", "Friss parkolóérzékelés áll rendelkezésre", payload,
                            reason="first_frame" if previous_received is None else "freshness_recovered", sensor_seq=seq)
            return []
        self.sim_time = max(self.sim_time, sim_time)
        if topic == "requests/parking":
            if not self._valid_id(payload.get("car_id")) or not self._valid_id(payload.get("request_id")):
                return self._reject(topic, payload, "invalid_request_id", "Hiányzó vagy hibás parkolókérési azonosító")
            car, request = payload["car_id"], payload["request_id"]
            key = (car, request)
            if key in self.responses:
                previous = self.responses[key]
                current = self.slots[previous["slot_id"]]
                if current["car_id"] == car and current["reservation_id"] == previous["reservation_id"]:
                    self._audit("decision", "parking_grant_replayed", "Ismételt kérésre ugyanaz a parkolóengedély került visszaküldésre",
                                dict(payload, reservation_id=previous["reservation_id"]), reason="idempotent_request",
                                slot_id=previous["slot_id"], throttle=True)
                    return self._reply(car, "parking_grant", previous)
                self._audit("decision", "parking_wait", "A lezárt foglalás engedélye nem játszható vissza", payload,
                            reason="reservation_closed", throttle=True, slot_id=previous["slot_id"])
                return self._reply(car, "parking_wait", dict(request_id=request,
                    reason="A korábbi foglalás már lezárult", sim_time=self.sim_time))
            # A new request identifier cannot obtain a second space for a car.
            existing = next((s for s in self.slots.values() if s["car_id"] == car), None)
            if existing:
                self._audit("decision", "parking_wait", "Új kérési azonosítóval sem adható második foglalás ugyanannak a járműnek",
                            dict(payload, reservation_id=existing["reservation_id"]), reason="car_already_reserved",
                            throttle=True, slot_id=existing["id"])
                return self._reply(car, "parking_wait", dict(request_id=request,
                    reason="A jármű már rendelkezik foglalással", sim_time=self.sim_time))
            available = [s for s in self.slots.values() if s["occupied"] is False
                         and not s["blocked"] and s["car_id"] is None]
            origin = payload.get("origin", self.config.get("world", {}).get("production_exit",
                                      self.config.get("world", {}).get("origin", [14.0, 58.0])))
            if self.network_mode:
                if (not isinstance(origin, (list, tuple)) or len(origin) != 2 or
                        any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in origin)):
                    return self._reject(topic, payload, "invalid_vehicle_origin", "Hibás kezdeti járműkoordináták")
                available = [s for s in available if _project_route(s["route"], *origin)[1] <= .1]
            if not self._fresh(now) or not available:
                reason = "Elavult parkolóérzékelés" if not self._fresh(now) else "Nincs szabad parkolóhely"
                self._audit("decision", "parking_wait", "Parkolóengedély nem adható: " + reason, payload,
                            level="warning" if not self._fresh(now) else "info",
                            reason="stale_parking_sensor" if not self._fresh(now) else "no_available_slot", throttle=True,
                            available_count=len(available), sensor_seq=self.sensor_seq,
                            sensor_age_s=(now - self.sensor_received_ns) / 1e9 if self.sensor_received_ns is not None else None)
                return self._reply(car, "parking_wait", dict(request_id=request, reason=reason, sim_time=self.sim_time))
            def distance(s):
                if self.network_mode:
                    progress, lateral, total = _project_route(s["route"], *origin)
                    return total - progress
                return sum(math.dist(a, b) for a, b in zip(s["route"], s["route"][1:]))
            selected = min(available, key=lambda s: (distance(s), s["id"])) if self.strategy == "nearest" else min(available, key=lambda s: s["id"])
            reservation = self._id("park")
            selected.update(car_id=car, reservation_id=reservation)
            reply = dict(request_id=request, slot_id=selected["id"], reservation_id=reservation,
                         route=selected["route"], sim_time=self.sim_time)
            self.responses[key] = reply
            self._audit("decision", "parking_granted", f"Parkoló kijelölve: {selected['id']}",
                        dict(payload, reservation_id=reservation),
                        reason="shortest_available_route_then_id" if self.strategy == "nearest" else "lowest_available_slot_id",
                        strategy=self.strategy, slot_id=selected["id"], selected_route_length_m=distance(selected),
                        candidate_routes=[dict(slot_id=s["id"], route_length_m=distance(s)) for s in available],
                        sensor_seq=self.sensor_seq, sensor_age_s=(now - self.sensor_received_ns) / 1e9)
            return self._reply(car, "parking_grant", reply)
        if topic == "requests/zone":
            if any(not self._valid_id(payload.get(key)) for key in ("car_id", "request_id", "zone_id")):
                return self._reject(topic, payload, "invalid_zone_request", "Hiányzó vagy hibás zónakérési azonosító")
            car, request, zid = payload["car_id"], payload["request_id"], payload["zone_id"]
            z = self.zones.get(zid)
            if z is None:
                return self._reject(topic, payload, "unknown_zone_id", "Ismeretlen konfliktuszóna")
            old = self.zone_responses.get((car, request))
            if old and z["owner"] == car and z["grant_id"] == old["grant_id"]:
                self._audit("decision", "zone_grant_replayed", "Ismételt kérésre ugyanaz a zónaengedély került visszaküldésre",
                            payload, reason="idempotent_request", grant_id=old["grant_id"], throttle=True)
                return self._reply(car, "zone_grant", old)
            if old:
                self._audit("decision", "zone_wait", "A lezárt zónaengedély nem játszható vissza", payload,
                            reason="zone_grant_closed", grant_id=old["grant_id"], throttle=True)
                return self._reply(car, "zone_wait", dict(request_id=request, zone_id=zid,
                    reason="A korábbi zónaengedély már lezárult", sim_time=self.sim_time))
            t = self.telemetry.get(car)
            fresh = t and now - t["received_ns"] <= self.config["mqtt"]["freshness_s"] * 1e9
            pending = self.zone_queues[zid]
            if car not in pending:
                pending.append(car)
            # This map has one eastbound approach to C1. A following car may
            # not lock the corner while an ungranted car blocks its approach.
            # Only received pose measurements are inspected, never World.
            ahead = t and any(other != car and sample.get("state") != "parked"
                             and abs(sample["y"] - z["y"]) <= self.config["vehicle"]["width"]
                             and t["x"] < sample["x"] <= z["x"]
                             and abs(t["y"] - z["y"]) <= self.config["vehicle"]["width"]
                             for other, sample in self.telemetry.items())
            network_reason, network_evidence = None, {}
            if self.network_mode:
                eligible, network_reason, network_evidence = self._network_eligibility(car, zid, now)
                ahead = network_reason == "physical_leader_ahead"
                if eligible:
                    first_eligible = next((waiting for waiting in pending
                                           if self._network_eligibility(waiting, zid, now)[0]), car)
                    if first_eligible != car:
                        network_reason = "earlier_eligible_request"
                        network_evidence["earlier_waiting_car_id"] = first_eligible
            if not fresh or z["owner"] not in (None, car) or ahead:
                codes = ([] if fresh else ["stale_vehicle_telemetry"]) + ([] if z["owner"] in (None, car) else ["zone_owned"]) + (["physical_leader_ahead"] if ahead else [])
                self._audit("decision", "zone_wait", "Zónaengedély visszatartva: a frissességi vagy kizárási feltétel nem teljesül",
                            payload, level="warning" if not fresh else "info", reason=",".join(codes), reason_codes=codes, throttle=True,
                            owner=z["owner"], telemetry_seq=t.get("seq") if t else None,
                            telemetry_age_s=(now - t["received_ns"]) / 1e9 if t else None,
                            physical_leader_ahead=bool(ahead), **network_evidence)
                return self._reply(car, "zone_wait", dict(request_id=request, zone_id=zid,
                    reason="Foglalt zóna, előrébb váró autó vagy elavult járműadat", sim_time=self.sim_time))
            if self.network_mode and network_reason:
                self._audit("decision", "zone_wait", "Zónaengedély visszatartva az irányított úthálózat bizonyítékai alapján",
                            payload, reason=network_reason, throttle=True, **network_evidence)
                return self._reply(car, "zone_wait", dict(request_id=request, zone_id=zid,
                    reason={"zone_not_yet_near": "A zóna még túl távol van",
                            "physical_leader_ahead": "Előrébb váró jármű a megközelítő ágon",
                            "earlier_eligible_request": "Korábbi jogosult kérés a kereszteződésnél",
                            "earlier_route_zone_not_cleared": "Előbb az előző útvonalzónát kell elhagyni",
                            "other_zone_still_owned": "Az előző zóna felszabadításának igazolása még hiányzik",
                            "zone_already_passed": "A jármű már elhagyta ezt a zónát",
                            "zone_not_on_assigned_route": "A zóna nem része a kijelölt útvonalnak",
                            "pose_off_assigned_route": "A telemetria nincs a kijelölt útvonalon",
                            "no_assigned_route": "Nincs központilag kiosztott parkolóútvonal"}.get(network_reason, "Zónakérés nem jogosult"),
                    reason_code=network_reason, sim_time=self.sim_time))
            grant = z["grant_id"] or self._id("zone")
            z.update(owner=car, grant_id=grant)
            pending.remove(car)
            reply = dict(request_id=request, zone_id=zid, grant_id=grant, sim_time=self.sim_time)
            self.zone_responses[(car, request)] = reply
            self._audit("decision", "zone_granted", f"Konfliktuszóna engedélyezve: {zid}", payload,
                        reason="fresh_pose_free_zone_no_physical_leader", grant_id=grant,
                        telemetry_seq=t["seq"], telemetry_age_s=(now - t["received_ns"]) / 1e9,
                        position_m=[t["x"], t["y"]], physical_leader_ahead=False, **network_evidence)
            return self._reply(car, "zone_grant", reply)
        if topic == "events":
            kind, car = payload.get("type"), payload.get("car_id")
            if kind == "departed":
                s = self.slots.get(payload.get("slot_id")) if self._valid_id(payload.get("slot_id")) else None
                if s and s["car_id"] == car and s["reservation_id"] == payload.get("reservation_id"):
                    self.pending_departures[s["id"]] = dict(after_seq=self.sensor_seq)
                    self._audit("decision", "parking_release_pending", "Kiadási esemény elfogadva; a foglalás későbbi üres érzékelőkeretre vár",
                                payload, reason="matching_departure_requires_sensor_confirmation", slot_id=s["id"], after_sensor_seq=self.sensor_seq)
                else:
                    self._reject(topic, payload, "departure_identity_mismatch", "A kiadási esemény foglalási azonosítója nem egyezik", slot_id=payload.get("slot_id"))
            elif kind == "zone_exit":
                z = self.zones.get(payload.get("zone_id")) if self._valid_id(payload.get("zone_id")) else None
                if z and z["owner"] == car and z["grant_id"] == payload.get("grant_id"):
                    self.pending_exits[z["id"]] = payload
                    self._audit("decision", "zone_release_pending", "Kilépési esemény elfogadva; a teljes jármű kívülhelyzete ellenőrzésre kerül",
                                payload, reason="matching_exit_requires_pose_confirmation", grant_id=payload.get("grant_id"))
                    self._clear_exits()
                else:
                    self._reject(topic, payload, "zone_exit_identity_mismatch", "A kilépési esemény zónaengedélye nem egyezik", grant_id=payload.get("grant_id"))
        return []

    def snapshot(self):
        fresh = self._fresh(time.perf_counter_ns())
        slots = []
        for s in self.slots.values():
            state = "unknown" if not fresh or s["occupied"] is None else (
                "blocked" if s["blocked"] else "occupied" if s["occupied"] else
                "reserved" if s["car_id"] else "free")
            slots.append({k: s[k] for k in ("id", "x", "y", "car_id", "reservation_id")} | {"state": state})
        return dict(slots=slots, zones=[{k: z[k] for k in ("id", "owner", "grant_id")} for z in self.zones.values()],
                    strategy=self.strategy, warnings=[] if fresh else ["A parkolóérzékelés még nem friss; új foglalás nem adható."])
