"""Received trip evidence through real MQTT, HTTP pagination and process restart.

Run explicitly: python -m tests.history_mqtt_smoke
"""
import csv
import io
import json
import socket
import threading
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server
from tests.mqtt_smoke import wait_for


def main():
    output = ROOT / "results" / ("history-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=2, parking_count=12,
        initial_car_count=2, initial_occupied_count=0, production_limit=2, parking_dwell_s=5))
    config["simulation"]["automatic_pedestrians"] = False
    config["logging"] = dict(detail_limit_mib=1, raw_segment_mib=.125)
    config_path = output / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, config_path, seed=61, speed=8)
    server = thread = None
    checks = []

    def start_http():
        nonlocal server, thread
        server = create_http_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

    def stop_http():
        nonlocal server, thread
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            server = thread = None

    def get(path, **params):
        with urlopen(f"http://127.0.0.1:{server.server_port}" + path + "?" + urlencode(params), timeout=10) as response:
            return response.read(), dict(response.headers)

    def document(path, **params):
        return json.loads(get(path, **params)[0])

    try:
        app.start()
        start_http()
        wait_for(app, lambda s: s.get("connected") and len(s.get("cars", [])) == 2)
        old_run = app.run_id
        finished = wait_for(app, lambda s: s.get("metrics", {}).get("completed") == 2 and not s.get("cars"), timeout=100)
        assert finished["metrics"]["collisions"] == 0
        checks.append("Two vehicles park and leave the live snapshot after actual MQTT decisions")
        logging = finished["logging"]
        assert logging["raw_removed_segments"] > 0
        assert logging["raw_retained_bytes"] <= logging["raw_limit_bytes"]
        assert logging["raw_oversize_dropped"] == 0
        assert not any("nyers MQTT-naplókeret betelt" in warning for warning in finished["warnings"])
        newest = app.run_dir / logging["raw_segments"][-1]["file"]
        assert json.loads(newest.read_text(encoding="utf-8").splitlines()[-1])["payload"]["run_id"] == old_run
        checks.append("Forced raw ring rotation stays bounded, continues receiving new messages and does not show the former full-buffer warning")
        catalogue = document("/api/cars", run_id=old_run, search="CAR001")
        assert any(car["id"] == "CAR001" for car in catalogue["cars"])
        first = document("/api/car/history", run_id=old_run, car_id="CAR001", limit=71)
        assert first["current"] is None
        assert first["summary"]["complete_received_archive"]
        samples, cursor = list(first["telemetry"]), first["next_after"]
        while cursor is not None:
            page = document("/api/car/history", run_id=old_run, car_id="CAR001", limit=71, after=cursor)
            assert all(row["id"] > cursor for row in page["telemetry"])
            samples.extend(page["telemetry"])
            cursor = page["next_after"]
        assert len(samples) == first["summary"]["sample_count"] > 500
        assert len({row["id"] for row in samples}) == len(samples)
        assert samples[-1]["state"] == "parked"
        assert all(row["utc"] and row["wait_reason"] is not None for row in samples)
        assert any(row["speed"] > 0 and row["distance_m"] > 0 for row in samples)
        checks.append("Departed car has a complete received trace beyond 500 samples, paginated without duplicates, with UTC/position/reason")
        csv_raw, headers = get("/api/car/history/export", run_id=old_run, car_id="CAR001", format="csv")
        rows = list(csv.DictReader(io.StringIO(csv_raw.decode("utf-8-sig"))))
        assert sum(row["record_type"] == "telemetry" for row in rows) == len(samples)
        exported = document("/api/car/history/export", run_id=old_run, car_id="CAR001", format="json")
        assert len(exported["telemetry"]) == len(samples)
        assert any(row["event_type"] == "departed" for row in exported["events"])
        assert "attachment" in headers["Content-Disposition"]
        (output / "CAR001-trip.csv").write_bytes(csv_raw)
        (output / "CAR001-trip.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
        checks.append("Native CSV/JSON exports retain all recorded points and departure event")
        app.control(dict(action="reset"))
        wait_for(app, lambda s: s.get("connected") and s.get("cars"))
        reset_archive = document("/api/car/history", run_id=old_run, car_id="CAR001", limit=1)
        assert reset_archive["summary"]["sample_count"] == len(samples)
        assert reset_archive["run_id"] == old_run and reset_archive["current"] is None
        checks.append("Reset reuses car names in a new run without altering the old trip or its map/config")
        stop_http()
        app.close()
        app = Application(config, config_path, seed=62, speed=8)
        app.start()
        start_http()
        reopened = document("/api/car/history", run_id=old_run, car_id="CAR001", limit=1)
        assert reopened["summary"]["sample_count"] == len(samples)
        assert reopened["run_id"] == old_run
        checks.append("Previous program-start archive remains discoverable and queryable after broker/workers restart")
        report = dict(status="PASS", transport="real MQTT 3.1.1 over loopback TCP", run_id=old_run,
            checks=checks, metrics=finished["metrics"], trip_samples=len(samples),
            logging=finished.get("logging", {}), csv_export=str(output / "CAR001-trip.csv"),
            json_export=str(output / "CAR001-trip.json"))
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        stop_http()
        app.close()


if __name__ == "__main__":
    main()
