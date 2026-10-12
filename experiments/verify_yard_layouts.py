"""Reproducible four-layout capacity check; deterministic protocol, not MQTT."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from factory_yard import __version__
from factory_yard.scenario import compile_scenario
from tests.test_network_traffic import Wire

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/network-load-check-v12.json")
    args = parser.parse_args()
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    results = []
    for exit_id in ("EXIT80", "EXIT70"):
        for area in ("north", "south"):
            requested = dict(preset="gyor", car_count=30, parking_count=64,
                initial_car_count=30, initial_occupied_count=4, production_limit=30,
                production_exit_id=exit_id, parking_area_id=area, parking_dwell_s=600)
            normalized, config = compile_scenario(base, requested)
            config["simulation"]["automatic_pedestrians"] = False
            wire = Wire(config, seed=17)
            dt = config["simulation"]["dt"]
            for _ in range(round(900 / dt)):
                wire.step(dt)
                if wire.world.snapshot()["metrics"]["completed"] == 30:
                    break
            state = wire.world.snapshot()
            result = dict(exit=exit_id, area=area, seed=17, requested=requested,
                normalized=normalized, config=config, time_s=state["sim_time"],
                metrics=state["metrics"],
                collision_events=[event for event in wire.messages if event["type"] == "collision"])
            result["status"] = "PASS" if state["metrics"]["completed"] == 30 and state["metrics"]["collisions"] == 0 else "FAIL"
            results.append(result)
            print(f"{exit_id}/{area}: {result['status']}, {state['metrics']['completed']}/30, collisions={state['metrics']['collisions']}", flush=True)
    source_files = ("factory_yard/model.py", "factory_yard/coordinator.py", "factory_yard/proximity.py", "factory_yard/scenario.py")
    report = dict(status="PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
        executed_at_utc=datetime.now(timezone.utc).isoformat(), model_version=__version__,
        command="python -m experiments.verify_yard_layouts --output " + args.output,
        method="deterministic protocol-level model integration via tests.test_network_traffic.Wire; not MQTT",
        collision_scope="unique reference pairs: vehicles, crossing pedestrian association and physical static rectangles",
        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
        results=results)
    destination = (ROOT / args.output).resolve()
    if not destination.is_relative_to(ROOT / "results"):
        raise ValueError("Regression results must stay within the project results folder")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] != "PASS":
        raise SystemExit("Layout regression failed; inspect collision_events and completed counts.")


if __name__ == "__main__":
    main()
