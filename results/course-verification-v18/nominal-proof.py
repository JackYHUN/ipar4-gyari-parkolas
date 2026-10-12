"""Supplement the course campaign with a separate fault-free actual MQTT run.

From projekt: python results/course-verification-v18/nominal-proof.py
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.course_verification import Campaign, save, sha, source_hashes, utc


def main():
    output = Path(__file__).resolve().parent
    if (output / "nominal").exists():
        raise SystemExit("A nominal könyvtár már létezik; a bizonyítékot nem írjuk felül.")
    started, hashes = time.monotonic(), source_hashes()
    helper_name = Path(__file__).resolve().relative_to(ROOT).as_posix()
    hashes[helper_name] = sha(Path(__file__))
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    run = Campaign(base, output / "nominal")
    run.speed = run.app.speed = 8
    try:
        run.start()
        finished = run.wait(lambda s: s.get("metrics", {}).get("completed") == 6, 70)
        assert finished["metrics"]["collisions"] == 0
        assert all(c["state"] == "parked" for c in finished["cars"])
        grants = run.recorder.matching("audit", "event_type", "parking_granted")
        assert len(grants) == 6
        assert all(r["payload"]["sensor_age_s"] <= base["mqtt"]["freshness_s"] for r in grants)
        case = run.mark("S01-normal-parkolas", "Hibainjektálás és gyalogosinjektálás nélkül hat autó tényleges MQTT-grant után beparkol a témalapi konfigurációban.",
            dict(metrics=finished["metrics"], observed_grants=len(grants), fault_injections=0,
                completed_car_ids=[c["id"] for c in finished["cars"]],
                all_observed_grant_sensor_ages_within_threshold=True), finished)
        events = run.archive()
        selected = [e for e in events if e["event_type"] in {"parking_granted", "parked"}]
        case["journal_record_ids"] = [e["id"] for e in selected]
        save(run.folder / "S01-normal-parkolas-evidence.json", dict(case=case, records=selected))
    finally:
        recording = run.close()
    after = source_hashes()
    after[helper_name] = sha(Path(__file__))
    assert hashes == after
    report = dict(status="PASS", executed_at_utc=utc(), elapsed_host_s=time.monotonic()-started,
        command="python results/course-verification-v18/nominal-proof.py", source_sha256=hashes,
        source_unchanged_during_campaign=True, run_id=run.app.run_id, seed=run.seed, speed=run.speed,
        config_sha256=sha(run.folder / "config.json"), final_metrics=finished["metrics"],
        transport="Actual MQTT3.1.1 loopback broker, independent simulator/coordinator/display/recorder and public HTTP",
        scope="Separate finite six-car nominal run on config/default.json geometry: 16 slots, two zebras, four initially occupied; no faults or pedestrians injected, automatic pedestrians disabled, parking dwell 1000 s.",
        case=case, recording=recording,
        artifact_sha256={p.relative_to(run.folder).as_posix(): sha(p) for p in sorted(run.folder.rglob("*")) if p.is_file()})
    save(run.folder / "summary.json", report)
    print(json.dumps(dict(status="PASS", run_id=run.app.run_id, metrics=finished["metrics"], frames=recording["frames"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
