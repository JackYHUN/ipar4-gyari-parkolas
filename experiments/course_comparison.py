"""Course experiment: preregistered paired six-vehicle cohorts, 600 s limit.

Uses the current World/Coordinator and compare.comparison/interval. It does not
edit the application, start MQTT, or measure network/production performance.
Automatic pedestrians are off; exogenous curb arrivals are generated before
either treatment. Only this runner replaces the world's bounded uniform noise
providers with a keyed common random field. Its key contains the physics tick,
receiver/object IDs and the original sampling call site: visibility, admission
and cooperative crossing starts remain endogenous and may differ by policy.
The original model's distribution and uncertainty bounds are retained, but its
state-dependent sequential random-number coupling is deliberately replaced.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import pathlib
import random
import statistics
import sys
import time
from datetime import datetime, timezone

from factory_yard import __version__
from factory_yard.coordinator import Coordinator
from factory_yard.model import World
from experiments.compare import comparison, interval

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_FILES = (ROOT / "config/course-reference.json", ROOT / "config/course-reference-high-load.json")
SCENARIOS = ("reference_9s", "high_load_3s")
HORIZON = 600.0
BOOTSTRAP = 10000
FIRST_SEED = 1001
SEEDS = 30


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8")).hexdigest()


def write_json(path: pathlib.Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("Cannot export an empty measurement table")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                             else value for key, value in row.items()})


def check_configuration(path: pathlib.Path, spawn_interval: float) -> dict:
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    expected = copy.deepcopy(base)
    expected["simulation"].update(spawn_interval=spawn_interval, production_limit=6,
                                   parking_dwell=HORIZON + 1, automatic_pedestrians=False)
    actual = json.loads(path.read_text(encoding="utf-8"))
    if actual != expected:
        raise ValueError(f"Configuration differs from the preregistered default-derived values: {path}")
    assert actual["simulation"]["max_active"] == 6
    assert len(actual["slots"]) == 16 and len(actual["crossings"]) == 2
    assert actual["simulation"]["dt"] == .05
    return actual


def pedestrian_schedule(seed: int, config: dict) -> list[dict]:
    """Independent uniform 7–14 s first arrivals, then 50–65 s gaps.

    Each deadline is ceiling-quantized onto a 0.05 s physics boundary. Generate
    the complete schedule first; no World state or policy enters this function.
    Longer gaps bound the risk of overlapping curb arrivals; the runner still
    asserts that every offered pedestrian was actually created in both runs.
    """
    dt = config["simulation"]["dt"]
    rng = random.Random(seed ^ 0xC0A718)
    result = []
    for crossing in config["crossings"]:
        tick = math.ceil(rng.uniform(7.0, 14.0) / dt)
        ordinal = 0
        while tick < round(HORIZON / dt):
            ordinal += 1
            result.append(dict(event_id=f"{crossing['id']}-{ordinal:03d}",
                               crossing_id=crossing["id"], tick=tick, offered_at_s=round(tick * dt, 6)))
            tick += math.ceil(rng.uniform(50.0, 65.0) / dt)
    return sorted(result, key=lambda event: (event["tick"], event["crossing_id"]))


class CommonUniformField:
    """Stable bounded uniform noise, attached only by this experimental runner.

    The original sampling function calls uniform without observation IDs. Its
    frame exposes them, so a narrow read-only hook obtains its existing car and
    other variables. Call-site line numbers distinguish x/y/heading samples and
    are protected by before/after model source hashes. No copied model logic or
    world-state mutation is needed. Unsupported call sites fail explicitly.
    """
    def __init__(self, seed: int, kind: str, world: World):
        self.seed, self.kind, self.world = seed, kind, world
        self.values: dict[tuple, float] = {}

    def value(self, key: tuple, low: float, high: float) -> float:
        raw = json.dumps((self.seed, self.kind, *key), separators=(",", ":")).encode("ascii")
        fraction = (int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") >> 11) / 2 ** 53
        return low + (high - low) * fraction

    def uniform(self, low: float, high: float) -> float:
        frame = sys._getframe(1)
        if frame.f_code.co_name != "_sample_local" or frame.f_locals.get("self") is not self.world:
            raise AssertionError("The current model has an unsupported noise sampling call site")
        receiver = frame.f_locals["car"].id
        observed = frame.f_locals["other"].id if self.kind == "perception" else receiver
        tick = round(self.world.sim_time / self.world.config["simulation"]["dt"])
        key = (tick, receiver, observed, frame.f_lineno, low, high)
        value = self.value(key, low, high)
        assert low <= value <= high
        if key in self.values and self.values[key] != value:
            raise AssertionError("Duplicate noise key changed value")
        self.values[key] = value
        return value


def check_noise_field(config: dict) -> dict:
    first = CommonUniformField(1001, "perception", World(config, "noise-check-a", 1001))
    second = CommonUniformField(1001, "perception", World(config, "noise-check-b", 1001))
    bound = first.world.position_uncertainty
    assert bound == .1  # implicit default of the current World, unchanged here
    values = [first.value((tick, "CAR001", "CAR002", 662, -bound, bound), -bound, bound)
              for tick in range(12000)]
    assert all(-bound <= value <= bound for value in values)
    assert all(value == second.value((tick, "CAR001", "CAR002", 662, -bound, bound), -bound, bound)
               for tick, value in enumerate(values))
    return dict(checked_keys=len(values), same_key_same_value=True, bounded=True,
                position_error_bound_m=bound, observed_min_m=min(values), observed_max_m=max(values),
                visibility_is_endogenous=True, sampling_hook="original _sample_local caller IDs and call-site line")


def invariant_check(world: World, coordinator: Coordinator) -> None:
    assigned = [car.slot_id for car in world.cars.values() if car.slot_id]
    assert len(assigned) == len(set(assigned)), "Two vehicles share a physical parking allocation"
    reservations = [slot for slot in coordinator.slots.values() if slot["car_id"]]
    owners = [slot["car_id"] for slot in reservations]
    identifiers = [slot["reservation_id"] for slot in reservations]
    assert len(owners) == len(set(owners)), "A vehicle owns multiple coordinator slots"
    assert len(identifiers) == len(set(identifiers)) and all(identifiers)
    for slot in reservations:
        assert slot["id"] not in world.initial_occupied and slot["id"] not in world.blocked
        car = world.cars[slot["car_id"]]
        assert (car.slot_id, car.reservation_id) == (slot["id"], slot["reservation_id"])
    for car in world.cars.values():
        if car.slot_id:
            slot = coordinator.slots[car.slot_id]
            assert (slot["car_id"], slot["reservation_id"]) == (car.id, car.reservation_id)
        for zone_id, grant in car.zone_grants.items():
            zone = coordinator.zones[zone_id]
            assert (zone["owner"], zone["grant_id"]) == (car.id, grant), "Unmatched exclusive zone grant"


def run(config: dict, scenario: str, seed: int, strategy: str, schedule: list[dict]) -> dict:
    started_utc, started = utc(), time.perf_counter()
    run_id = f"course-v18-{scenario}-{seed}-{strategy}"
    world, coordinator = World(config, run_id, seed), Coordinator(config, run_id, strategy)
    noise = CommonUniformField(seed, "perception", world)
    navigation_noise = CommonUniformField(seed, "observer_localization", world)
    world.sensor_rng, world.navigation_rng = noise, navigation_noise
    dt = config["simulation"]["dt"]
    expected = {f"CAR{index+1:03d}": round(index * config["simulation"]["spawn_interval"], 6) for index in range(6)}
    created, completed, offered, events, receipts, audit = {}, {}, {}, [], [], []
    schedule_index = 0
    executed_steps = 0
    for tick in range(round(HORIZON / dt) + 1):
        while schedule_index < len(schedule) and schedule[schedule_index]["tick"] == tick:
            event = schedule[schedule_index]
            previous = world._ped_counter
            world.control("pedestrian", {"crossing_id": event["crossing_id"]})
            accepted = world._ped_counter == previous + 1
            receipts.append(dict(**event, applied_at_s=round(world.sim_time, 6), created=accepted,
                                  pedestrian_id=f"PED{world._ped_counter:03d}" if accepted else None))
            schedule_index += 1
        # Include curb arrivals at the terminal boundary, then stop without
        # advancing already completed vehicles. No departure is possible under
        # parking_dwell=601 and no replenishment under production_limit=6.
        if len(completed) == 6 or tick == round(HORIZON / dt):
            events.extend(dict(data) for topic, data in world._outbox if topic == "events")
            break
        before_offered = world._offered_cars
        messages = world.step(dt, connected=True)
        executed_steps += 1
        for index in range(before_offered, world._offered_cars):
            car_id = f"CAR{index+1:03d}"
            # Evidence from the World queue or admitted Car, never an inferred
            # coordinator request time. Admission can differ between policies.
            value = world.cars[car_id].created_at if car_id in world.cars else dict(world._buffer)[car_id]
            offered[car_id] = round(value, 6)
        now_ns = round((tick + 1) * dt * 1e9)
        for topic, data in messages:
            if topic == "events":
                events.append(dict(data))
                if data["type"] == "production_exit":
                    created[data["car_id"]] = float(data["offered_at_s"])
                elif data["type"] == "parked":
                    completed[data["car_id"]] = dict(parking_time_s=float(data["parking_time_s"]),
                        yard_time_s=float(data["yard_time_s"]), distance_m=float(data["distance_m"]),
                        parked_at=float(data["sim_time"]), slot_id=data["slot_id"], reservation_id=data["reservation_id"])
            payload = dict(data, run_id=run_id, sent_ns=now_ns)
            for reply_topic, reply in coordinator.handle(topic, payload, now_ns=now_ns):
                world.handle_command(reply_topic, dict(reply, run_id=run_id, sent_ns=now_ns))
        audit.extend(coordinator.drain_audit())
        invariant_check(world, coordinator)
    snapshot = world.snapshot()
    observed_schedule = [item for item in schedule if item["offered_at_s"] <= snapshot["sim_time"] + 1e-9]
    all_ped_created = len(receipts) == len(observed_schedule) and all(item["created"] for item in receipts)
    unobserved = [dict(**event, observation="completed_cohort_after_run_end_not_observed")
                  for event in schedule[schedule_index:]]
    times = [car["parking_time_s"] for car in completed.values()]
    distances = [car["distance_m"] for car in completed.values()]
    vehicles = []
    for car_id, offered_at in expected.items():
        car = world.cars.get(car_id)
        result = dict(car_id=car_id, offered_at_s=offered.get(car_id), admitted_at_s=round(car.admitted_at, 6) if car else None,
            buffer_wait_s=round(car.admitted_at - offered_at, 6) if car else None,
            completed=car_id in completed, state_at_run_end=car.state if car else "not_admitted",
            elapsed_at_run_end_s=round(snapshot["sim_time"] - offered_at, 6), slot_id=car.slot_id if car else None,
            reservation_id=car.reservation_id if car else None,
            distance_at_run_end_m=round(car.distance, 6) if car else 0)
        result.update(completed.get(car_id, {}))
        vehicles.append(result)
    return dict(scenario=scenario, seed=seed, strategy=strategy, run_id=run_id, model_version=__version__,
        started_at_utc=started_utc, finished_at_utc=utc(), wall_time_s=time.perf_counter() - started,
        transport="deterministic_in_memory_protocol_not_mqtt", observation_horizon_s=HORIZON,
        simulation_end_s=snapshot["sim_time"], termination_reason="all_six_parked" if len(completed) == 6 else "600s_horizon",
        arrivals=snapshot["metrics"]["offered"], admitted=len(created),
        buffered=snapshot["metrics"]["buffered"], completed=len(completed), unfinished=6 - len(completed),
        completion_fraction=len(completed)/6, mean_parking_time_s=statistics.fmean(times) if times else None,
        mean_distance_m=statistics.fmean(distances) if distances else None, collisions=snapshot["metrics"]["collisions"],
        pedestrian_stops=snapshot["metrics"]["pedestrian_stops"], initial_occupied_slots=sorted(world.initial_occupied),
        actual_offered_arrival_times=offered, expected_offered_arrival_times=expected, offered_times_valid=offered == expected,
        schedule_sha256=canonical_sha(schedule), scheduled_pedestrian_arrivals=len(schedule),
        observed_pedestrian_arrivals=len(receipts), all_ped_arrivals_created_in_observation_window=all_ped_created,
        pedestrian_arrival_receipts=receipts, unobserved_pedestrian_arrivals=unobserved,
        invariant_checks=executed_steps, reservation_invariants_passed=True,
        created=created, cars=completed, vehicles=vehicles, events=events, coordinator_audit=audit,
        coordinator_inventory_at_run_end=[{key: slot[key] for key in ("id", "car_id", "reservation_id", "occupied")}
                                          for slot in coordinator.slots.values()],
        coordinator_zones_at_run_end=[{key: zone[key] for key in ("id", "owner", "grant_id")} for zone in coordinator.zones.values()],
        _noise=noise.values, _navigation_noise=navigation_noise.values)


def noise_pair(first: dict, second: dict) -> dict:
    common = first.keys() & second.keys()
    assert all(first[key] == second[key] for key in common), "Common random field differs within a pair"
    return dict(fixed_sampled_keys=len(first), nearest_sampled_keys=len(second), common_sampled_keys=len(common),
                fixed_only_keys=len(first.keys() - second.keys()), nearest_only_keys=len(second.keys() - first.keys()),
                all_common_keys_same_value=True)


def early_stop_reference_check(runs: list[dict], current_hashes: dict) -> dict:
    full_dir = ROOT / "results/course-comparison-v18/pilot"
    baseline = json.loads((full_dir / "summary.json").read_text(encoding="utf-8"))
    compared = []
    protected = [name for name in current_hashes if name.startswith("factory_yard/") or name.startswith("config/")
                 or name == "experiments/compare.py"]
    assert all(baseline["source_and_config_sha256_before"][name] == current_hashes[name] for name in protected)
    fields = ("parking_time_s", "yard_time_s", "distance_m", "parked_at", "slot_id", "reservation_id")
    for item in runs:
        full = json.loads((full_dir / "raw" / (item["run_id"] + ".json")).read_text(encoding="utf-8"))
        assert full["simulation_end_s"] == HORIZON and item["simulation_end_s"] < HORIZON
        assert full["completed"] == item["completed"] == 6
        assert full["collisions"] == item["collisions"] == 0
        assert full["actual_offered_arrival_times"] == item["actual_offered_arrival_times"]
        assert all(full["cars"][car_id][field] == values[field]
                   for car_id, values in item["cars"].items() for field in fields)
        assert not any(event["type"] == "departed" for event in full["events"])
        compared.append(dict(run_id=item["run_id"], full_end_s=HORIZON, early_end_s=item["simulation_end_s"],
                             completed=6, compared_fields=list(fields), all_target_metrics_exactly_equal=True,
                             no_departures_in_full_horizon=True))
    return dict(status="PASS", baseline="results/course-comparison-v18/pilot", model_and_configs_unchanged=True,
                comparisons=compared, full_horizon_wall_time_s=baseline["wall_time_s"])


def pair(fixed: dict, nearest: dict) -> dict:
    result = dict(scenario=fixed["scenario"], **comparison(fixed, nearest))
    common_end = min(fixed["simulation_end_s"], nearest["simulation_end_s"])
    common_receipts = [[item for item in run["pedestrian_arrival_receipts"] if item["offered_at_s"] <= common_end + 1e-9]
                       for run in (fixed, nearest)]
    result.update(same_offered_arrival_times=fixed["actual_offered_arrival_times"] == nearest["actual_offered_arrival_times"],
        offered_times_valid=fixed["offered_times_valid"] and nearest["offered_times_valid"],
        same_initial_occupancy=fixed["initial_occupied_slots"] == nearest["initial_occupied_slots"],
        same_pedestrian_schedule=fixed["schedule_sha256"] == nearest["schedule_sha256"],
        same_actual_pedestrian_arrivals_in_common_window=common_receipts[0] == common_receipts[1],
        common_pedestrian_window_end_s=common_end,
        all_ped_arrivals_created_in_observation_window=fixed["all_ped_arrivals_created_in_observation_window"] and nearest["all_ped_arrivals_created_in_observation_window"],
        fixed_observed_pedestrian_arrivals=fixed["observed_pedestrian_arrivals"], nearest_observed_pedestrian_arrivals=nearest["observed_pedestrian_arrivals"],
        fixed_unobserved_pedestrian_arrivals=len(fixed["unobserved_pedestrian_arrivals"]), nearest_unobserved_pedestrian_arrivals=len(nearest["unobserved_pedestrian_arrivals"]),
        fixed_simulation_end_s=fixed["simulation_end_s"], nearest_simulation_end_s=nearest["simulation_end_s"],
        perception_noise=noise_pair(fixed["_noise"], nearest["_noise"]),
        navigation_noise=noise_pair(fixed["_navigation_noise"], nearest["_navigation_noise"]),
        fixed_collisions=fixed["collisions"], nearest_collisions=nearest["collisions"],
        fixed_mean_parking_time_s=fixed["mean_parking_time_s"], nearest_mean_parking_time_s=nearest["mean_parking_time_s"],
        fixed_mean_distance_m=fixed["mean_distance_m"], nearest_mean_distance_m=nearest["mean_distance_m"],
        fixed_pedestrian_stops=fixed["pedestrian_stops"], nearest_pedestrian_stops=nearest["pedestrian_stops"],
        reservation_invariants_passed=fixed["reservation_invariants_passed"] and nearest["reservation_invariants_passed"])
    for field in ("same_offered_arrival_times", "offered_times_valid", "same_initial_occupancy", "same_pedestrian_schedule",
                  "same_actual_pedestrian_arrivals_in_common_window", "all_ped_arrivals_created_in_observation_window", "reservation_invariants_passed"):
        assert result[field], f"Pairing invariant failed: {field}, {fixed['scenario']}, seed {fixed['seed']}"
    return result


def summarize(pairs: list[dict], runs: list[dict], scenario: str) -> dict:
    selected = [item for item in pairs if item["scenario"] == scenario]
    selected_runs = [item for item in runs if item["scenario"] == scenario]
    result = dict(scenario=scenario, independent_pairs=len(selected), runs=len(selected_runs),
        matched_vehicle_pairs=sum(item["matched_completed"] for item in selected),
        total_collisions=sum(item["collisions"] for item in selected_runs),
        total_unfinished=sum(item["unfinished"] for item in selected_runs),
        all_offered_arrivals_matched=all(item["same_offered_arrival_times"] and item["offered_times_valid"] for item in selected),
        all_ped_arrivals_created_in_observation_window=all(item["all_ped_arrivals_created_in_observation_window"] for item in selected),
        all_ped_arrivals_matched_in_common_window=all(item["same_actual_pedestrian_arrivals_in_common_window"] for item in selected),
        observed_pedestrian_arrivals=sum(item["observed_pedestrian_arrivals"] for item in selected_runs),
        unobserved_pedestrian_arrivals_after_cohort=sum(len(item["unobserved_pedestrian_arrivals"]) for item in selected_runs),
        all_reservation_invariants_passed=all(item["reservation_invariants_passed"] for item in selected))
    for metric in ("parking_time_s", "distance_m"):
        values = [item[f"paired_{metric}_difference"] for item in selected if item[f"paired_{metric}_difference"] is not None]
        result[metric] = interval(values, BOOTSTRAP, seed=90210)
    for strategy in ("fixed", "nearest"):
        strategy_runs = [item for item in selected_runs if item["strategy"] == strategy]
        result[strategy] = dict(mean_parking_time_s=statistics.fmean(item["mean_parking_time_s"] for item in strategy_runs
                                   if item["mean_parking_time_s"] is not None),
            mean_distance_m=statistics.fmean(item["mean_distance_m"] for item in strategy_runs if item["mean_distance_m"] is not None),
            completed=sum(item["completed"] for item in strategy_runs), unfinished=sum(item["unfinished"] for item in strategy_runs),
            collisions=sum(item["collisions"] for item in strategy_runs), pedestrian_stops=sum(item["pedestrian_stops"] for item in strategy_runs))
    return result


def report(summary: dict) -> str:
    rows = []
    for scenario in summary["scenarios"]:
        title = "Referencia (9 s)" if scenario["scenario"] == "reference_9s" else "Nagyobb terhelés (3 s)"
        for metric, label in (("parking_time_s", "Parkolási idő (s/autó)"), ("distance_m", "Úthossz (m/autó)")):
            value = scenario[metric]
            ci = value["ci95"]
            ci_text = "nincs" if ci is None else f"[{ci[0]:.3f}; {ci[1]:.3f}]"
            mean = "nincs" if value["mean_difference"] is None else f"{value['mean_difference']:.3f}"
            rows.append(f"| {title} | {label} | {mean} | {ci_text} |")
    return f"""# Friss páros modellmérés – v0.18.0

A jelenlegi modell és koordinátor két parkolókiosztását 9 s, illetve 3 s felkínálási közzel hasonlítottuk össze. Konfigurációnként {summary['seeds_per_config']} független futáspár (seed {FIRST_SEED}–{FIRST_SEED + summary['seeds_per_config'] - 1}), páronként ugyanaz a hat autó, 16 parkoló és két zebra szerepel. A megfigyelési felső határ 600 s; mind a hat autó beparkolása után a számolás lezárul, mert a célmutatók ekkor már változatlanok. {'Ez csak futási időt becslő pilot, a végleges kampánytól külön kezeljük.' if summary['pilot'] else 'Ez a végleges kampány eredménye.'}

| Forgatókönyv | Mutató | nearest − fixed | Páros bootstrap 95%-os intervallum |
|---|---|---:|---:|
""" + "\n".join(rows) + f"""

A negatív érték a `nearest` szabály javára mutat. A parkolási idő a termelési felkínálástól indul, és az udvaron kívüli pufferben töltött időt is tartalmazza. Az újramintázási egység egy seedhez tartozó futáspár hat páros autókülönbségének átlaga; 10000 bootstrap-újramintázás készült, a telemetriapontok nem független minták.

Összesen {sum(item['matched_vehicle_pairs'] for item in summary['scenarios'])} párosan befejezett autó, {sum(item['total_unfinished'] for item in summary['scenarios'])} be nem fejezett autófutás és {sum(item['total_collisions'] for item in summary['scenarios'])} referenciaellenőrzéssel észlelt ütközés szerepel. Minden felkínálási idő egyezett, minden megfigyelési ablakba eső ütemezett gyalogos ténylegesen létrejött, a közös ablakban az érkezések egyeztek, és a foglalási invariánsok minden végrehajtott időlépésnél teljesültek. A későbbi, már befejezett kohorsz utáni gyalogosesemények külön „nem megfigyelt” jelölést kaptak. A forrás- és konfigurációhash-ek a kampány előtt és után egyeztek.

Ez determinisztikus, memórián belüli protokollkézbesítéssel végzett modellkísérlet. Az éles MQTT-futtató nem változott; hálózati késleltetést, veszteséget és valódi gyári teljesítményt ez a mérés nem igazol. A közös exogén gyalogosérkezések mellett a kooperatív átkelés kezdete és a járművek tényleges látótávolsági helyzete endogén. A runner azonos kulcsú virtuális mérési zajt alkalmaz ugyanazzal a ±0,1 m korláttal. A korábbi −7,992 s eredmény más kampányból származik, itt nem használjuk új mérésként.

Proveniencia és részletes összesítés: `summary.json`; nyers futások: `runs.jsonl` és `runs.csv`; párok: `pairs.json` és `pairs.csv`; autók: `vehicle_results.csv`; közös külső események: `exogenous_schedules.json`; részletes esemény- és döntési naplók: `raw/`. A pontos mérési határt a `docs/VEGLEGES_MERESI_TERV.md` rögzíti.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true", help="One pair/config, separate output; excluded from final campaign")
    args = parser.parse_args()
    output = ROOT / "results/course-comparison-v18"
    if args.pilot:
        output = output / "pilot-early-stop"
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw").mkdir(exist_ok=True)
    paths = sorted((ROOT / "factory_yard").glob("*.py")) + [ROOT / "experiments/compare.py", pathlib.Path(__file__),
        ROOT / "config/default.json", *CONFIG_FILES, ROOT / "docs/VEGLEGES_MERESI_TERV.md"]
    before = {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}
    configs = [check_configuration(path, spawn) for path, spawn in zip(CONFIG_FILES, (9., 3.))]
    if not args.pilot:
        validation_path = ROOT / "results/course-comparison-v18/pilot-early-stop/summary.json"
        validation_summary = json.loads(validation_path.read_text(encoding="utf-8"))
        early_stop_validation = validation_summary["early_stop_validation"]
        assert early_stop_validation["status"] == "PASS"
        assert before == validation_summary["source_and_config_sha256_after"], "Runner/config changed after early-stop verification"
    started_utc, started = utc(), time.perf_counter()
    runs, pairs, schedules, vehicle_rows = [], [], [], []
    noise_check = check_noise_field(configs[0])
    count = 1 if args.pilot else SEEDS
    for scenario, config in zip(SCENARIOS, configs):
        for seed in range(FIRST_SEED, FIRST_SEED + count):
            schedule = pedestrian_schedule(seed, config)
            schedules.append(dict(scenario=scenario, seed=seed, schedule_sha256=canonical_sha(schedule),
                schedule_rng_seed=seed ^ 0xC0A718, generation="independent RNG; first U(7,14)s, gaps U(50,65)s, ceil dt ticks", events=schedule))
            fixed = run(config, scenario, seed, "fixed", schedule)
            nearest = run(config, scenario, seed, "nearest", schedule)
            pair_result = pair(fixed, nearest)
            pairs.append(pair_result)
            for run_result in (fixed, nearest):
                del run_result["_noise"], run_result["_navigation_noise"]
                write_json(output / "raw" / (run_result["run_id"] + ".json"), run_result)
                vehicle_rows.extend(dict(scenario=scenario, seed=seed, strategy=run_result["strategy"],
                                          run_id=run_result["run_id"], **car) for car in run_result["vehicles"])
                runs.append({key: value for key, value in run_result.items() if key not in
                    ("events", "coordinator_audit", "vehicles", "pedestrian_arrival_receipts", "coordinator_inventory_at_run_end", "coordinator_zones_at_run_end")})
            print(json.dumps(dict(scenario=scenario, seed=seed, fixed_completed=fixed["completed"], nearest_completed=nearest["completed"],
                time_difference_s=pair_result["paired_parking_time_s_difference"], collisions=fixed["collisions"]+nearest["collisions"],
                all_ped_arrivals_created_in_observation_window=pair_result["all_ped_arrivals_created_in_observation_window"], elapsed_host_s=round(time.perf_counter()-started, 2))), flush=True)
    after = {path.relative_to(ROOT).as_posix(): sha(path) for path in paths}
    if args.pilot:
        early_stop_validation = early_stop_reference_check(runs, before)
    summary = dict(status="PASS", pilot=args.pilot, model_version=__version__, started_at_utc=started_utc,
        completed_at_utc=utc(), wall_time_s=time.perf_counter()-started, python_version=sys.version,
        python_executable=sys.executable, command="python -m experiments.course_comparison" + (" --pilot" if args.pilot else ""),
        source_and_config_sha256_before=before, source_and_config_sha256_after=after, hashes_unchanged=before == after,
        configs=[dict(scenario=scenario, path=path.relative_to(ROOT).as_posix(), sha256=sha(path), values=config)
                 for scenario, path, config in zip(SCENARIOS, CONFIG_FILES, configs)],
        first_seed=FIRST_SEED, seeds_per_config=count, bootstrap_resamples=BOOTSTRAP, bootstrap_seed=90210,
        independent_unit="one seed-level paired run per scenario, mean of matched vehicle differences",
        bootstrap_method="percentile nonparametric resampling of independent paired run means; 2.5%/97.5% order statistics",
        difference_direction="nearest minus fixed; negative favours nearest", units={"time":"s", "distance":"m", "speed":"m/s"},
        observation_horizon_s=HORIZON, horizon_policy="600 s upper limit; stop after all six offered vehicles park; no departures or replenishment",
        transport="deterministic_in_memory_protocol_not_mqtt", noise_field_check=noise_check,
        early_stop_validation=early_stop_validation,
        scenarios=[summarize(pairs, runs, scenario) for scenario in SCENARIOS],
        limitations=["Finite six-vehicle cohort, not sustained queueing or throughput capacity.",
            "Known polyline routes, cooperative pedestrians, bounded idealized virtual sensors.",
            "The experimental runner changes only stochastic coupling: same noise key, same bounded uniform error.",
            "Different visible objects and cooperative pedestrian crossing starts are endogenous outcomes.",
            "No broker, jitter, packet loss, real hardware or industrial safety validation.",
            "Between-seed bootstrap variation within this simulated scenario is not real factory uncertainty.",
            "Matched-completion estimands would be conditional if any vehicle were unfinished; all unfinished counts exported."])
    assert before == after, "Sources or configs changed during the campaign"
    if any(item["total_collisions"] for item in summary["scenarios"]):
        summary["status"] = "FAIL_COLLISION"
    with (output / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for item in runs:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_csv(output / "runs.csv", runs)
    write_csv(output / "pairs.csv", pairs)
    write_csv(output / "vehicle_results.csv", vehicle_rows)
    write_json(output / "pairs.json", pairs)
    write_json(output / "exogenous_schedules.json", schedules)
    write_json(output / "summary.json", summary)
    (output / "RESULTS.md").write_text(report(summary), encoding="utf-8")
    print(json.dumps(dict(status="PASS", pilot=args.pilot, wall_time_s=summary["wall_time_s"], scenarios=summary["scenarios"]), ensure_ascii=False), flush=True)
    if any(item["total_collisions"] for item in summary["scenarios"]):
        raise SystemExit("Collision invariant failed; these results do not establish collision-free operation")


if __name__ == "__main__":
    main()
