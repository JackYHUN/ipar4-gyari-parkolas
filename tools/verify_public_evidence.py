"""Verify this compact public snapshot without claiming absent raw validation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pathlib
import random
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9), (actual, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-only", action="store_true", help="Source identity after generating new local results")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    checked = []
    for entry in manifest["files"]:
        if args.sources_only and entry["kind"] not in ("source", "documentation") and not entry["path"].startswith("tools/"):
            continue
        assert sha(ROOT / entry["path"]) == entry["sha256"], "Snapshot file changed: " + entry["path"]
        checked.append(entry["path"])
    if args.sources_only:
        print(json.dumps(dict(status="PASS_SOURCE_SNAPSHOT", checked_files=len(checked), raw_validation="NOT_RUN")))
        return
    measurement = ROOT / "results/course-comparison-v18"
    summary = json.loads((measurement / "summary.json").read_text(encoding="utf-8"))
    pairs = json.loads((measurement / "pairs.json").read_text(encoding="utf-8"))
    with (measurement / "vehicle_results.csv").open(newline="", encoding="utf-8") as handle:
        vehicles = list(csv.DictReader(handle))
    assert len(pairs) == 60 and len(vehicles) == 720
    assert summary["hashes_unchanged"]
    assert summary["source_and_config_sha256_before"] == summary["source_and_config_sha256_after"]
    for name, expected in summary["source_and_config_sha256_after"].items():
        assert sha(ROOT / name) == expected, "Measured source identity changed: " + name
    computations = []
    for scenario in summary["scenarios"]:
        selected = [pair for pair in pairs if pair["scenario"] == scenario["scenario"]]
        assert len(selected) == 30
        assert sorted(pair["seed"] for pair in selected) == list(range(1001, 1031))
        assert scenario["total_unfinished"] == scenario["total_collisions"] == 0
        for metric in ("parking_time_s", "distance_m"):
            values = [pair[f"paired_{metric}_difference"] for pair in selected]
            rng = random.Random(90210)
            estimates = sorted(sum(values[int(rng.random() * 30)] for _ in range(30))/30 for _ in range(10000))
            result = scenario[metric]
            close(sum(values)/30, result["mean_difference"])
            close(statistics.stdev(values), result["sample_sd"])
            close(estimates[250], result["ci95"][0])
            close(estimates[9749], result["ci95"][1])
            computations.append(dict(scenario=scenario["scenario"], metric=metric, n_independent_pairs=30,
                                     mean=result["mean_difference"], ci95=result["ci95"]))
    baseline = json.loads((measurement / "pilot/summary.json").read_text(encoding="utf-8"))
    full_pilot = sorted((measurement / "pilot/raw").glob("*.json"))
    assert len(full_pilot) == 4 and baseline["pilot"]
    for path in full_pilot:
        run = json.loads(path.read_text(encoding="utf-8"))
        assert run["simulation_end_s"] == 600 and run["completed"] == 6 and run["collisions"] == 0
    verification = ROOT / "results/course-verification-v18"
    bundle = json.loads((verification / "summary.json").read_text(encoding="utf-8"))
    assert bundle["actual_mqtt_runs"] == 3 and bundle["scenario_count"] == 6
    source_entries = artifacts_present = artifacts_missing = 0
    for report_entry in bundle["reports"]:
        report_path = verification / report_entry["path"]
        assert sha(report_path) == report_entry["sha256"]
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for name, expected in report["source_sha256"].items():
            assert sha(ROOT / name) == expected, "Actual-MQTT measured source changed: " + name
            source_entries += 1
        for name, expected in report["artifact_sha256"].items():
            path = report_path.parent / name
            if path.is_file():
                assert sha(path) == expected
                artifacts_present += 1
            else:
                artifacts_missing += 1
    raw_inputs_present = len(list((measurement / "raw").glob("*.json"))) == 120 and (measurement / "runs.jsonl").is_file()
    result = dict(status="PASS_COMPACT_SCOPE", scope="Snapshot file identity, measured source identity, compact paired bootstrap, full-pilot availability and present MQTT artifacts; no full archive replay",
        checked_snapshot_files=len(checked), vehicle_records=len(vehicles), scenario_pairs=[30, 30], computations=computations,
        full_horizon_pilot_runs=4, original_full_validation_receipt="PASS, preserved historical receipt",
        full_campaign_raw_inputs_present=raw_inputs_present,
        full_campaign_raw_validation="NOT_RUN; use validate_exports.py after regenerating or restoring all raw inputs",
        actual_mqtt_runs=3, actual_mqtt_source_entries_verified=source_entries,
        original_mqtt_artifacts_present_and_verified=artifacts_present, original_mqtt_artifacts_absent=artifacts_missing,
        full_mqtt_archive_validation="NOT_RUN; absent recordings and run-export archives remain separate local evidence")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
