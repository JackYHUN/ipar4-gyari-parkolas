"""Actual MQTT/HTTP recovery proof with deliberately held display ingestion.

This isolates observer lag from a broker failure. It is not a sustained load
capacity measurement. No MQTT packet is dropped or reordered by the hold.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from factory_yard import __version__
from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server


def main():
    source_names = [p.relative_to(ROOT).as_posix() for p in (ROOT / "factory_yard").glob("*.py")]
    source_names.extend(("experiments/verify_transport_v12.py", "config/default.json"))
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_names}
    output = ROOT / "results" / ("transport-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=12, parking_count=40,
        initial_car_count=12, initial_occupied_count=0, production_limit=12, parking_dwell_s=600))
    config["simulation"]["automatic_pedestrians"] = False
    config["logging"] = dict(detail_limit_mib=2, raw_segment_mib=.5)
    path = output / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, path, seed=93, speed=2)
    requested_hold, holding, release = threading.Event(), threading.Event(), threading.Event()
    original_batch = app._observe_batch
    def controlled_batch():
        if requested_hold.is_set():
            holding.set()
            if not release.wait(20):
                raise RuntimeError("The bounded experimental observer hold timed out")
        return original_batch()
    app._observe_batch = controlled_batch
    server = http = None
    checks, observations = [], {}

    def request(endpoint, data=None):
        req = Request(f"http://127.0.0.1:{server.server_port}" + endpoint,
            data=None if data is None else json.dumps(data).encode("utf-8"),
            headers={} if data is None else {"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def wait(predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, state = request("/api/state")
            if predicate(state):
                return state
            time.sleep(.05)
        raise AssertionError("Expected MQTT/HTTP condition not observed within timeout")

    def stage(state):
        return dict({key: state.get(key) for key in ("run_id", "connected", "paused", "speed", "sim_time", "control_recovery", "mqtt")},
                    active_car_count=len(state.get("cars", [])))

    try:
        app.start()
        server = create_http_server(app, 0)
        http = threading.Thread(target=server.serve_forever, daemon=True)
        http.start()
        initial = wait(lambda s: s.get("connected") and len(s.get("cars", [])) == 12)
        observations["before_hold"] = stage(initial)
        checks.append("Twelve actual simulated cars and MQTT-connected display state observed at 2x speed")
        requested_hold.set()
        assert holding.wait(3)
        held = wait(lambda s: not s.get("connected") and s.get("control_recovery", {}).get("pause_available"))
        observations["while_display_ingestion_held"] = stage(held)
        assert held["mqtt"]["transport"]["broker_connected"] is True
        assert held["mqtt"]["last_message_age_s"] >= .75
        assert held["mqtt"]["transport"]["last_snapshot_age_s"] <= .75
        checks.append("Held FIFO ingestion makes display stale while independent callback snapshot arrivals remain fresh")
        for action in (dict(action="resume"), dict(action="speed", value=2)):
            status, result = request("/api/control", action)
            assert status == 400 and result.get("error")
        checks.append("Stale-state resume and higher speed rejected by actual HTTP control API")
        ids = []
        for action in (dict(action="speed", value=1), dict(action="pause")):
            status, result = request("/api/control", action)
            assert status == 200 and result.get("control_id")
            ids.append(result["control_id"])
        _, held_log = request("/api/logs?category=control&limit=50")
        published = [row for row in held_log["items"] if row["event_type"] == "control_published" and row["details"].get("control_id") in ids]
        assert len(published) == 2
        assert all(row["details"]["recovery_request"]["clock"] == "host_perf_counter_receipt" for row in published)
        assert not any(row["event_type"] == "control_applied" and row["details"].get("control_id") in ids for row in held_log["items"])
        observations["control_ids"] = ids
        observations["published_receipts_while_held"] = published
        checks.append("Actual QoS1 broker publication has correlated admission context; display has not yet observed application receipts")
        requested_hold.clear()
        release.set()
        recovered = wait(lambda s: s.get("connected") and s.get("paused") is True and s.get("speed") == 1)
        observations["after_release"] = stage(recovered)
        _, log = request("/api/logs?category=control&limit=50")
        applied = [row for row in log["items"] if row["event_type"] == "control_applied" and row["details"].get("control_id") in ids]
        assert len(applied) == 2
        assert all(row["details"]["recovery_request"]["clock"] == "host_perf_counter_receipt" for row in applied)
        observations["applied_receipts_after_release"] = applied
        assert recovered["mqtt"]["observer"]["last_queue_wait_ms"] is not None
        assert recovered["mqtt"]["observer"]["queue_wait_sample_count"] > 0
        checks.append("After FIFO release the actual simulator reports both applied controls, paused at1x, with preserved admission context")
        assert hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_names}
        report = dict(status="PASS", model_version=__version__, executed_at_utc=datetime.now(timezone.utc).isoformat(),
            command="python -m experiments.verify_transport_v12", run_id=app.run_id,
            transport="actual MQTT3.1.1 loopback TCP broker and independent simulator/coordinator/display; actual HTTP",
            scope="Bounded observer hold injected by experiment only. Twelve initial cars at2x; this proves stage separation and recovery, not sustainable throughput.",
            checks=checks, observations=observations, config=config, source_sha256=hashes)
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "results/transport-mqtt-check-v12.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(status=report["status"], checks=len(checks), run_id=app.run_id, artifact="results/transport-mqtt-check-v12.json")))
    finally:
        requested_hold.clear()
        release.set()
        if server:
            server.shutdown()
            server.server_close()
            http.join(3)
        app.close()


if __name__ == "__main__":
    main()
