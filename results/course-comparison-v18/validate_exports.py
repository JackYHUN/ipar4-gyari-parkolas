r"""Independent standard-library recomputation of the final measurement exports.

Run from projekt with .venv\Scripts\python.exe results/course-comparison-v18/validate_exports.py.
Does not import the runner or its shared statistics helper.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import pathlib
import random
import statistics
from datetime import datetime, timezone

DIRECTORY = pathlib.Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9), (actual, expected)


def main():
    summary = json.loads((DIRECTORY / "summary.json").read_text(encoding="utf-8"))
    pairs = json.loads((DIRECTORY / "pairs.json").read_text(encoding="utf-8"))
    schedules = json.loads((DIRECTORY / "exogenous_schedules.json").read_text(encoding="utf-8"))
    runs = [json.loads(line) for line in (DIRECTORY / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    with (DIRECTORY / "vehicle_results.csv").open(newline="", encoding="utf-8") as handle:
        csv_vehicles = list(csv.DictReader(handle))
    assert summary["status"] == "PASS" and not summary["pilot"]
    assert len(runs) == 120 and len(pairs) == len(schedules) == 60 and len(csv_vehicles) == 720
    run_keys = [(run["scenario"], run["seed"], run["strategy"]) for run in runs]
    assert len(set(run_keys)) == len(run_keys)
    vehicle_keys = [(row["scenario"], int(row["seed"]), row["strategy"], row["car_id"]) for row in csv_vehicles]
    assert len(set(vehicle_keys)) == len(vehicle_keys)
    assert summary["hashes_unchanged"]
    assert summary["source_and_config_sha256_before"] == summary["source_and_config_sha256_after"]
    for name, expected in summary["source_and_config_sha256_after"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    checks = []
    actual_pedestrian_count = 0
    raw_by_key = {}
    for run in runs:
        raw = json.loads((DIRECTORY / "raw" / (run["run_id"] + ".json")).read_text(encoding="utf-8"))
        raw_by_key[(run["scenario"], run["seed"], run["strategy"])] = {key: raw[key] for key in
            ("cars", "schedule_sha256", "pedestrian_arrival_receipts", "simulation_end_s")}
        assert raw["completed"] == raw["arrivals"] == 6 and raw["unfinished"] == raw["collisions"] == 0
        assert 0 < raw["simulation_end_s"] <= 600
        assert raw["termination_reason"] == "all_six_parked"
        assert not any(event["type"] == "departed" for event in raw["events"])
        spawn = 9 if raw["scenario"] == "reference_9s" else 3
        expected_offers = {f"CAR{index+1:03d}": index * spawn for index in range(6)}
        assert raw["actual_offered_arrival_times"] == expected_offers
        assert len(raw["vehicles"]) == len(raw["cars"]) == 6
        parked = [event for event in raw["events"] if event["type"] == "parked"]
        assert len(parked) == 6
        for event in parked:
            car = raw["cars"][event["car_id"]]
            close(car["parking_time_s"], event["sim_time"] - expected_offers[event["car_id"]])
            close(car["distance_m"], event["distance_m"])
        close(raw["mean_parking_time_s"], sum(event["parking_time_s"] for event in parked)/6)
        close(raw["mean_distance_m"], sum(event["distance_m"] for event in parked)/6)
        receipts = raw["pedestrian_arrival_receipts"]
        assert raw["all_ped_arrivals_created_in_observation_window"]
        assert all(receipt["created"] and receipt["offered_at_s"] <= raw["simulation_end_s"] for receipt in receipts)
        assert all(event["offered_at_s"] > raw["simulation_end_s"] for event in raw["unobserved_pedestrian_arrivals"])
        assert len(receipts) + len(raw["unobserved_pedestrian_arrivals"]) == raw["scheduled_pedestrian_arrivals"]
        assert len([event for event in raw["events"] if event["type"] == "pedestrian_waiting"]) == len(receipts)
        actual_pedestrian_count += len(receipts)
        inventory = [slot for slot in raw["coordinator_inventory_at_run_end"] if slot["car_id"]]
        assert len(inventory) == len({slot["car_id"] for slot in inventory}) == len({slot["reservation_id"] for slot in inventory}) == 6
        assert all(slot["id"] not in raw["initial_occupied_slots"] for slot in inventory)
        for slot in inventory:
            car = raw["cars"][slot["car_id"]]
            assert (car["slot_id"], car["reservation_id"]) == (slot["id"], slot["reservation_id"])
    for row in csv_vehicles:
        raw = raw_by_key[(row["scenario"], int(row["seed"]), row["strategy"])]
        car = raw["cars"][row["car_id"]]
        for metric in ("parking_time_s", "yard_time_s", "distance_m", "parked_at"):
            close(float(row[metric]), car[metric])
        assert row["slot_id"] == car["slot_id"] and row["reservation_id"] == car["reservation_id"]
    for schedule in schedules:
        calculated = hashlib.sha256(json.dumps(schedule["events"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        assert calculated == schedule["schedule_sha256"]
        for strategy in ("fixed", "nearest"):
            raw = raw_by_key[(schedule["scenario"], schedule["seed"], strategy)]
            assert raw["schedule_sha256"] == calculated
            assert len(raw["pedestrian_arrival_receipts"]) == len([event for event in schedule["events"]
                                                                 if event["offered_at_s"] <= raw["simulation_end_s"]])
    for pair in pairs:
        fixed = raw_by_key[(pair["scenario"], pair["seed"], "fixed")]
        nearest = raw_by_key[(pair["scenario"], pair["seed"], "nearest")]
        for metric in ("parking_time_s", "distance_m"):
            differences = [nearest["cars"][car][metric] - fixed["cars"][car][metric] for car in fixed["cars"]]
            close(pair[f"paired_{metric}_difference"], sum(differences)/6)
        for key in ("same_offered_arrival_times", "same_initial_occupancy", "same_pedestrian_schedule",
                    "same_actual_pedestrian_arrivals_in_common_window", "all_ped_arrivals_created_in_observation_window",
                    "reservation_invariants_passed"):
            assert pair[key]
        assert pair["perception_noise"]["all_common_keys_same_value"]
        assert pair["navigation_noise"]["all_common_keys_same_value"]
    for scenario in summary["scenarios"]:
        selected = [pair for pair in pairs if pair["scenario"] == scenario["scenario"]]
        assert len(selected) == 30 and sorted(pair["seed"] for pair in selected) == list(range(1001, 1031))
        for metric in ("parking_time_s", "distance_m"):
            values = [pair[f"paired_{metric}_difference"] for pair in selected]
            rng = random.Random(90210)
            estimates = sorted(sum(values[int(rng.random() * 30)] for _ in range(30))/30 for _ in range(10000))
            result = scenario[metric]
            close(result["mean_difference"], sum(values)/30)
            close(result["sample_sd"], statistics.stdev(values))
            close(result["ci95"][0], estimates[250])
            close(result["ci95"][1], estimates[9749])
            checks.append(dict(scenario=scenario["scenario"], metric=metric, n_independent_pairs=30,
                               independently_recomputed_mean=sum(values)/30,
                               independently_recomputed_ci95=[estimates[250], estimates[9749]]))
    validation = dict(status="PASS", validated_at_utc=datetime.now(timezone.utc).isoformat(),
        scope="120 raw runs, 60 run pairs, 720 vehicle records, all observed exogenous arrivals, terminal reservations; independent bootstrap recomputation",
        source_hashes_still_match=True, raw_runs=len(runs), independent_pairs=len(pairs), vehicle_records=len(csv_vehicles),
        validation_runner_sha256=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        observed_pedestrian_arrivals=actual_pedestrian_count, total_unfinished=0, total_collisions=0, computations=checks,
        limitations=["Noise equality and tickwise invariants are assertions of the frozen runner; exports preserve their outcomes, not all telemetry/noise values.",
                     "This check validates model exports, not the actual MQTT application or industrial safety."])
    (DIRECTORY / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
