"""Paired finite-cohort experiment using deterministic protocol delivery.

This runner exercises the same World and Coordinator as the MQTT application,
but DOES NOT start a broker or measure network timing. Only published protocol
messages reach the Coordinator; snapshots are used for final verification only.
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

from factory_yard.coordinator import Coordinator
from factory_yard.model import World


ROOT = pathlib.Path(__file__).resolve().parents[1]


def cohort_config(config: dict, duration: float) -> dict:
    """Keep every cohort member in the yard, preventing demand replenishment."""
    result = copy.deepcopy(config)
    result["simulation"]["parking_dwell"] = duration + 1.0
    result["simulation"]["production_limit"] = result["simulation"]["max_active"]
    return result


def run_model(config: dict, seed: int, strategy: str, duration: float = 600.0,
              faults: list[tuple[float, str, dict]] | None = None) -> dict:
    run_id = f"model-{seed}-{strategy}"
    world = World(config, run_id, seed)
    coordinator = Coordinator(config, run_id, strategy)
    created, completed = {}, {}
    events = []
    dt = float(config["simulation"]["dt"])
    scheduled = sorted(list(faults or []), key=lambda item: item[0])
    fault_index = 0
    disconnected_until = -1.0
    for step_index in range(math.ceil(duration / dt)):
        current_time = step_index * dt
        while fault_index < len(scheduled) and scheduled[fault_index][0] <= current_time:
            _, action, data = scheduled[fault_index]
            if action == "disconnect":
                disconnected_until = current_time + float(data["seconds"])
            else:
                world.control(action, data)
            fault_index += 1
        connected = current_time >= disconnected_until
        messages = world.step(dt, connected=connected)
        # Virtual monotonic time is deterministic and independent of wall time.
        now_ns = round((step_index + 1) * dt * 1e9)
        for topic, data in messages:
            if topic == "events":
                if data["type"] == "production_exit":
                    created[data["car_id"]] = float(data.get("offered_at_s", data["sim_time"]))
                elif data["type"] == "parked":
                    completed[data["car_id"]] = {
                        "parking_time_s": float(data["parking_time_s"]),
                        "distance_m": float(data["distance_m"]),
                        "parked_at": float(data["sim_time"]),
                        "slot_id": data["slot_id"],
                    }
                elif data["type"] in {"collision", "sensor_failure", "pedestrian_stop"}:
                    events.append(dict(data))
            if not connected:
                continue  # Transport cut discards outgoing samples, not local physics.
            payload = dict(data, run_id=run_id, sent_ns=now_ns)
            for reply_topic, reply in coordinator.handle(topic, payload, now_ns=now_ns):
                world.handle_command(reply_topic, dict(reply, run_id=run_id, sent_ns=now_ns))
        # This finite-cohort study can stop once every offered vehicle has parked.
        # No member departs before the configured observation horizon.
        if len(created) == config["simulation"]["max_active"] and len(completed) == len(created):
            break
    snapshot = world.snapshot()
    times = [result["parking_time_s"] for result in completed.values()]
    distances = [result["distance_m"] for result in completed.values()]
    return {
        "seed": seed, "strategy": strategy, "transport": "deterministic_in_memory_protocol",
        "observation_horizon_s": duration, "simulation_end_s": snapshot["sim_time"],
        "arrivals": snapshot["metrics"].get("offered", len(created)),
        "admitted": len(created), "buffered": snapshot["metrics"].get("buffered", 0),
        "completed": len(completed),
        "unfinished": snapshot["metrics"].get("offered", len(created)) - len(completed),
        "completion_fraction": len(completed) / snapshot["metrics"].get("offered", len(created)) if created else 0.0,
        "mean_parking_time_s": statistics.fmean(times) if times else None,
        "mean_distance_m": statistics.fmean(distances) if distances else None,
        "collisions": snapshot["metrics"]["collisions"],
        "pedestrian_stops": snapshot["metrics"]["pedestrian_stops"],
        "created": created, "cars": completed, "events": events,
    }


def interval(values: list[float], samples: int, seed: int = 90210) -> dict:
    if not values:
        return {"n_pairs": 0, "mean_difference": None, "ci95": None}
    if len(values) == 1:
        return {"n_pairs": 1, "mean_difference": values[0], "sample_sd": None, "ci95": None}
    rng = random.Random(seed)
    estimates = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(samples))
    low = estimates[max(0, math.floor(samples * 0.025))]
    high = estimates[min(samples - 1, math.ceil(samples * 0.975) - 1)]
    return {"n_pairs": len(values), "mean_difference": statistics.fmean(values),
            "sample_sd": statistics.stdev(values) if len(values) > 1 else 0.0,
            "ci95": [low, high]}


def comparison(fixed: dict, nearest: dict) -> dict:
    common = sorted(set(fixed["cars"]) & set(nearest["cars"]))
    result = {"seed": fixed["seed"], "matched_completed": len(common),
              "fixed_arrivals": fixed["arrivals"], "nearest_arrivals": nearest["arrivals"],
              "fixed_completed": fixed["completed"], "nearest_completed": nearest["completed"],
              "fixed_unfinished": fixed["unfinished"], "nearest_unfinished": nearest["unfinished"],
              "same_arrival_times": fixed["created"] == nearest["created"]}
    for name in ("parking_time_s", "distance_m"):
        result["paired_" + name + "_difference"] = statistics.fmean(
            nearest["cars"][car][name] - fixed["cars"][car][name] for car in common) if common else None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--bootstrap", type=int, default=10000)
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "config" / "default.json")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "results" / "model-comparison")
    args = parser.parse_args()
    if not 1 <= args.seeds <= 1000 or not 1 <= args.bootstrap <= 100000 or not 1 <= args.duration <= 3600:
        parser.error("seeds 1–1000, bootstrap 1–100000 és duration 1–3600 szükséges")
    raw_config = args.config.read_bytes()
    source_hashes = {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in [ROOT / "factory_yard" / "model.py", ROOT / "factory_yard" / "coordinator.py",
                                  pathlib.Path(__file__).resolve()]}
    config = cohort_config(json.loads(raw_config), args.duration)
    args.output.mkdir(parents=True, exist_ok=True)
    runs, pairs = [], []
    started = time.perf_counter()
    for seed in range(args.first_seed, args.first_seed + args.seeds):
        fixed = run_model(config, seed, "fixed", args.duration)
        nearest = run_model(config, seed, "nearest", args.duration)
        runs.extend((fixed, nearest))
        pairs.append(comparison(fixed, nearest))
        print(f"Seed {seed}: fixed {fixed['completed']}/{fixed['arrivals']}, nearest {nearest['completed']}/{nearest['arrivals']}, collisions {fixed['collisions']} + {nearest['collisions']}", flush=True)
    scalar_fields = [key for key in runs[0] if key not in ("created", "cars", "events")]
    with (args.output / "runs.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=scalar_fields)
        writer.writeheader()
        writer.writerows({key: run[key] for key in scalar_fields} for run in runs)
    with (args.output / "pairs.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "transport": "deterministic_in_memory_protocol_not_mqtt",
        "scope": "finite six-vehicle cohort; no outbound removal; conditional matched completions",
        "difference_direction": "nearest minus fixed; negative favours nearest",
        "config_file_sha256": hashlib.sha256(raw_config).hexdigest(),
        "source_file_sha256": source_hashes, "python_version": sys.version,
        "experiment_config": config, "seeds": args.seeds, "first_seed": args.first_seed,
        "observation_horizon_s": args.duration, "bootstrap_resamples": args.bootstrap,
        "bootstrap_unit": "seed-level mean of paired vehicle differences",
        "parking_time_s": interval([pair["paired_parking_time_s_difference"] for pair in pairs
                                    if pair["paired_parking_time_s_difference"] is not None], args.bootstrap),
        "distance_m": interval([pair["paired_distance_m_difference"] for pair in pairs
                                if pair["paired_distance_m_difference"] is not None], args.bootstrap),
        "total_collisions": sum(run["collisions"] for run in runs),
        "total_unfinished": sum(run["unfinished"] for run in runs),
        "matched_vehicle_pairs": sum(pair["matched_completed"] for pair in pairs),
        "all_arrival_times_matched": all(pair["same_arrival_times"] for pair in pairs),
        "wall_time_s": time.perf_counter() - started,
        "limitations": ["No broker, jitter or packet loss in this model experiment.",
                        "Known routes, cooperative pedestrians and bounded virtual perception.",
                        "No observation beyond the stated horizon; unfinished vehicles counted separately.",
                        "Confidence interval covers between-seed variation of this scenario, not real factory performance."],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "vehicle_results.json").write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    means = {strategy: statistics.fmean(run["mean_parking_time_s"] for run in runs
                                       if run["strategy"] == strategy and run["mean_parking_time_s"] is not None)
             for strategy in ("fixed", "nearest") if any(run["strategy"] == strategy and run["mean_parking_time_s"] is not None for run in runs)}
    time_result, distance_result = summary["parking_time_s"], summary["distance_m"]
    def formatted(value):
        return "nincs befejezett páros megfigyelés" if value is None else f"{value:.3f}"
    def ci(result):
        return "nem számítható" if result["ci95"] is None else "[" + "; ".join(formatted(value) for value in result["ci95"]) + "]"
    report = f"""# Páros modellkísérlet eredménye

Ez determinisztikus modellvizsgálat, közvetítő és hálózati késleltetés nélkül. {args.seeds} véletlenmag és két kiosztási szabály, magonként legfeljebb hat felkínált autó, {args.duration:g} s megfigyelési felső határ szerepel. A modell az összes kohorsztag parkolása után lezárja az adott futást.

| Mutató | nearest − fixed | Magonkénti páros bootstrap 95% intervallum |
|---|---:|---:|
| Parkolásig eltelt idő (s/autó) | {formatted(time_result['mean_difference'])} | {ci(time_result)} |
| Távolság (m/autó) | {formatted(distance_result['mean_difference'])} | {ci(distance_result)} |

A negatív különbség a `nearest` szabály javára mutat. A parkolásig eltelt idő a termelési felkínálástól indul, tehát az udvaron kívüli pufferben várást is tartalmazza. A befejezett autók futásátlagainak átlaga: `fixed` {formatted(means.get('fixed'))} s, `nearest` {formatted(means.get('nearest'))} s.

Azonos autóazonosítók páros befejezése: **{summary['matched_vehicle_pairs']}**. Be nem fejezett autók összege a két szabály valamennyi futásában: **{summary['total_unfinished']}**. Referenciaellenőrzéssel észlelt ütközések: **{summary['total_collisions']}**. A felkínált autók érkezési időpontjai minden párnál azonosak: **{'igen' if summary['all_arrival_times_matched'] else 'nem'}**.

A 95%-os intervallum {args.bootstrap} bootstrap-újramintázásból származik; az újramintázási egység a magonkénti páros átlag, nem az egyes autó. Az időeredmény a mindkét szabálynál befejezett azonos autókra feltételes. A modellt korlátozza az ismert útvonal, a kooperatív gyalogos és az egyszerűsített parkolás. Az eredmény nem valódi autógyári teljesítménymérés és nem a tényleges MQTT-demó futásának statisztikája.

A beállításokat és a konfiguráció SHA-256 azonosítóját a `summary.json`, a futásonkénti adatokat a `runs.csv`, az összehasonlításokat a `pairs.csv`, az autók adatait a `vehicle_results.json` tartalmazza.
"""
    (args.output / "RESULTS.md").write_text(report, encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("parking_time_s", "distance_m", "total_collisions", "total_unfinished")}, indent=2), flush=True)
    if summary["total_collisions"]:
        raise SystemExit("Collision invariant failed; these results do not verify a collision-free run.")


if __name__ == "__main__":
    main()
