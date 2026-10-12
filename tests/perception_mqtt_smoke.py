"""Verify continuous received observations, diagnostic outages and trip replay.

Run explicitly: python -m tests.perception_mqtt_smoke
"""
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import socket
import threading
import time
import zipfile
from urllib.parse import urlencode
from urllib.request import urlopen

from factory_yard import __version__
from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server
from tests.mqtt_smoke import wait_for


def main():
    sources = sorted(path for path in (ROOT / "factory_yard").rglob("*.py")
                     if "__pycache__" not in path.parts)
    sources += [ROOT / "tests/perception_mqtt_smoke.py", ROOT / "config/default.json"]
    def source_hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sources}
    tested_sources = source_hashes()
    output = ROOT / "results" / ("perception-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=2, parking_count=12,
        initial_car_count=2, initial_occupied_count=0, production_limit=2, parking_dwell_s=5))
    config["simulation"]["automatic_pedestrians"] = False
    config["logging"] = dict(detail_limit_mib=1, raw_segment_mib=.125)
    path = output / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, path, seed=71, speed=4)
    server = thread = None
    checks = []
    car_id = "CAR001"

    def selected(state):
        return next((car for car in state.get("cars", []) if car["id"] == car_id), {})

    def raw(endpoint, **parameters):
        with urlopen(f"http://127.0.0.1:{server.server_port}{endpoint}?{urlencode(parameters)}", timeout=15) as response:
            return response.read()

    def doc(endpoint, **parameters):
        return json.loads(raw(endpoint, **parameters))

    try:
        app.start()
        server = create_http_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fresh_state = wait_for(app, lambda s: selected(s).get("sensors", {}).get("fresh")
                              and selected(s).get("control", {}).get("source") == "ideal_route_controller")
        proximity = selected(fresh_state)["sensors"]["proximity"]
        assert proximity["source"] == "virtual_bumper_ray_fan"
        assert len(proximity["channels"]) == 8
        assert proximity["sample_sim_time_s"] == selected(fresh_state)["sensors"]["sample_sim_time_s"]
        assert any(channel["distance_m"] is not None for channel in proximity["channels"])
        assert all(channel["distance_m"] is None or 0 <= channel["distance_m"] <= proximity["range_m"]
                   for channel in proximity["channels"])
        assert b"YardPerception" in raw("/perception.js")
        assert len(raw("/perception.css")) > 100
        assert b"YardPerceptionAnalysis" in raw("/perception-analysis.js")
        checks.append("Eight bumper-mounted virtual fan sensors arrive over MQTT with physical surface clearances and their original sample clock; renderer assets are served over HTTP")
        run_id = app.run_id
        app.control(dict(action="sensor_failure", seconds=6))
        fault = wait_for(app, lambda s: selected(s).get("sensors", {}).get("fault_active"))
        held = selected(fault)["sensors"]
        stopped = wait_for(app, lambda s: selected(s).get("state") == "sensor_stop"
                           and selected(s).get("speed") == 0)
        stale = selected(stopped)["sensors"]
        assert stale["pose"] == held["pose"] and stale["environment"] == held["environment"]
        assert stale["sample_sim_time_s"] == held["sample_sim_time_s"]
        assert stale["proximity"] == held["proximity"]
        assert selected(stopped)["control"]["target_speed_m_s"] == 0
        assert selected(stopped)["control"]["sensor_age_s"] > stale["timeout_s"]
        wait_for(app, lambda s: selected(s).get("sensors", {}).get("fresh")
                 and not selected(s)["sensors"].get("fault_active")
                 and selected(s)["sensors"]["sample_sim_time_s"] > held["sample_sim_time_s"])
        checks.append("Sensor outage preserves the old observation and records the actual braking decision on a separate MQTT diagnostic topic")
        app.control(dict(action="speed", value=8))
        finished = wait_for(app, lambda s: s.get("metrics", {}).get("completed") == 2 and not s.get("cars"), timeout=100)
        assert finished["metrics"]["collisions"] == 0
        assert finished["logging"]["raw_removed_segments"] > 0
        samples, after, ceiling, event_ceiling, summary = [], 0, None, None, None
        while True:
            query = dict(run_id=run_id, car_id=car_id, after=after, limit=173)
            if ceiling is not None:
                query["until_id"] = ceiling
                query["events_until_id"] = event_ceiling
            page = doc("/api/car/history", **query)
            ceiling = page["until_id"]
            if event_ceiling is None:
                event_ceiling = page["events_until_id"]
            assert page["events_until_id"] == event_ceiling
            summary = page["summary"]
            samples.extend(page["telemetry"])
            if page["next_after"] is None:
                break
            after = page["next_after"]
        assert len(samples) == summary["sample_count"] > 500
        assert summary["perception_samples"] == len(samples)
        assert summary["perception_coverage"] == 1
        assert summary["perception_distinct_samples"] < len(samples)
        assert summary["perception_stale_frames"] > 0
        assert summary["camera_recording"] is False
        assert all(isinstance(row["sensors"], dict) and isinstance(row["control"], dict) for row in samples)
        assert all(row["sensors"]["proximity"]["sample_sim_time_s"] == row["sensors"]["sample_sim_time_s"]
                   and len(row["sensors"]["proximity"]["channels"]) == 8 for row in samples)
        assert all(row["sensors"]["nominal_period_s"] == config["simulation"]["telemetry_period"]
                   and row["sensors"]["sampling"]["interpolated_samples"] is False
                   and row["details"]["period_s"] == row["sensors"]["period_s"]
                   and row["details"]["nominal_period_s"] == row["sensors"]["nominal_period_s"] for row in samples)
        diagnostic = [row for row in samples if row["details"].get("source") == "simulation_controller_diagnostics"]
        assert len(diagnostic) > 20
        assert all(row["details"]["mqtt"]["topic"] == "diagnostics/perception/CAR001" for row in diagnostic)
        assert all(row["details"]["position_source"] == "post_step_simulation_reference" for row in diagnostic)
        assert all(row["sensors"]["pose"] == held["pose"] and row["sensors"]["environment"] == held["environment"] for row in diagnostic)
        assert all(row["sensors"]["proximity"] == held["proximity"] for row in diagnostic)
        assert len({row["sensors"]["sample_sim_time_s"] for row in diagnostic}) == 1
        assert len({row["control"]["decision_sim_time_s"] for row in diagnostic}) > 20
        assert diagnostic[-1]["sensors"]["age_s"] > diagnostic[0]["sensors"]["age_s"]
        assert any(row["control"]["target_state"] == "sensor_stop" for row in diagnostic)
        checks.append("All received sensor bundles survive raw log rotation with nominal/effective sample cadence; cached outage frames retain the old sample time while controller time advances")
        navigation_rows, cursor = [], None
        while True:
            parameters = dict(run_id=run_id, car_id=car_id, limit=173, until_id=ceiling,
                              events_until_id=event_ceiling, include_overview="1" if cursor is None else "0",
                              include_mission="1" if cursor is None else "0",
                              direction="start" if cursor is None else "after")
            if cursor is not None:
                parameters["cursor"] = cursor
            navigation = doc("/api/car/history/page", **parameters)
            assert navigation["until_id"] == ceiling
            assert navigation["events_until_id"] == event_ceiling
            assert all(event["id"] <= event_ceiling for event in navigation["events"])
            if cursor is None:
                mission = navigation["mission"]
                assert mission["until_id"] == ceiling and mission["events_until_id"] == event_ceiling
                assert mission["receipt"]["sample_count"] == len(samples)
                for label, sample in (("first", samples[0]), ("last", samples[-1])):
                    endpoint = mission["receipt"][label]
                    for key in ("id", "utc", "sim_time", "x", "y", "state", "distance_m", "seq"):
                        assert endpoint[key] == sample[key], key
                assert mission["receipt"]["complete_received_archive"] is True
                assert mission["perception"]["received_frames"] == len(samples)
                assert mission["perception"]["stale_frames"] == summary["perception_stale_frames"]
                assert mission["perception"]["unique_sample_clocks"] == summary["perception_distinct_samples"]
                assert mission["perception"]["missing_frames"] == 0
                ages = [row["sim_time"] - row["sensors"]["sample_sim_time_s"] for row in samples]
                assert math.isclose(mission["perception"]["max_age_s"], max(ages), abs_tol=1e-8)
                assert mission["distance"]["available"] is True
                assert math.isclose(mission["distance"]["value_m"], samples[-1]["distance_m"] - samples[0]["distance_m"], abs_tol=1e-8)
                assert mission["proximity_minimum"]["available"] is False
                timing = mission["timing"]
                assert timing["interval_count"] == len(samples) - 1
                assert timing["valid_interval_count"] + timing["excluded_interval_count"] == len(samples) - 1
                assert timing["unknown_positive_duration_s"] > 0  # fault observer rows lack publisher sequence
                assert math.isclose(timing["span_s"], timing["valid_estimated_duration_s"] + timing["unknown_positive_duration_s"], abs_tol=1e-7)
                for rows_key, other_key in (("states", "states_other"), ("wait_reasons", "wait_reasons_other")):
                    assert math.isclose(sum(row["estimated_s"] for row in mission[rows_key]) + mission["aggregation"][other_key]["estimated_s"], timing["valid_estimated_duration_s"], abs_tol=1e-7)
                    assert sum(row["sample_count"] for row in mission[rows_key]) + mission["aggregation"][other_key]["sample_count"] == len(samples)
                overview = navigation["overview"]
                assert overview["total_count"] == len(samples)
                assert overview["until_id"] == ceiling
                assert overview["points"][0]["id"] == samples[0]["id"]
                assert overview["points"][-1]["id"] == samples[-1]["id"]
                lookup = {row["id"]: row for row in samples}
                assert len(overview["points"]) <= 4000
                for point in overview["points"]:
                    assert (point["x"], point["y"], point["sim_time"]) == (lookup[point["id"]]["x"], lookup[point["id"]]["y"], lookup[point["id"]]["sim_time"])
            assert navigation["window"]["order"] == "receipt_id_ascending"
            assert navigation["window"]["start_ordinal"] == len(navigation_rows) + 1
            navigation_rows.extend(navigation["telemetry"])
            assert navigation["window"]["end_ordinal"] == len(navigation_rows)
            assert navigation["window"]["total_count"] == len(samples)
            cursor = navigation["window"]["next_after_id"]
            if cursor is None:
                break
        assert navigation_rows == samples
        sought_time = diagnostic[len(diagnostic) // 2]["sim_time"] + .023
        expected = max((row for row in samples if row["sim_time"] <= sought_time),
                       key=lambda row: (row["sim_time"], row["id"]))
        sought = doc("/api/car/history/page", run_id=run_id, car_id=car_id,
                     direction="time", sim_time=sought_time, until_id=ceiling, limit=17)
        assert sought["target_id"] == expected["id"]
        assert sought["telemetry"][0] == expected
        assert sought["seek"]["predecessor_found"] is True
        last_window = doc("/api/car/history/page", run_id=run_id, car_id=car_id,
                          direction="end", until_id=ceiling, limit=17)
        assert last_window["target_id"] == samples[-1]["id"]
        assert last_window["telemetry"] == samples[-17:]
        assert last_window["window"]["next_after_id"] is None
        assert doc("/api/car/history/mission", run_id=run_id, car_id=car_id, until_id=ceiling, events_until_id=event_ceiling) == mission
        checks.append("V11 scalar full-prefix mission matches exact MQTT receipts, reported odometry, sensor freshness/age and complete exposure accounting; absent scalar proximity minimum remains unavailable")
        checks.append("Bounded receipt-order HTTP windows reproduce every MQTT frame, frozen sample/event ceilings, exact fault-time predecessor, last received sample and exact-vertex full-trip overview")
        exported = doc("/api/car/history/export", run_id=run_id, car_id=car_id, format="json")
        assert exported["telemetry"] == samples
        assert exported["config"] == config
        assert exported["metadata"]["run_id"] == run_id
        assert exported["metadata"]["seed"] == 71
        assert exported["map"]["slots"] == config["slots"]
        csv_raw = raw("/api/car/history/export", run_id=run_id, car_id=car_id, format="csv")
        rows = [row for row in csv.DictReader(io.StringIO(csv_raw.decode("utf-8-sig"))) if row["record_type"] == "telemetry"]
        assert len(rows) == len(samples)
        for row, sample in zip(rows, samples):
            assert json.loads(row["sensors"]) == sample["sensors"]
            assert json.loads(row["control"]) == sample["control"]
        checks.append("Paginated HTTP history and full CSV/JSON export contain identical observations, decisions and separate sample/receipt clocks")
        zip_raw = raw("/api/export", format="zip")
        with zipfile.ZipFile(io.BytesIO(zip_raw)) as archive:
            assert archive.testzip() is None
            metadata = json.loads(archive.read("metadata.json"))
            assert metadata["run_id"] == run_id
            frozen = metadata["export_snapshot"]
            complete = [json.loads(line) for line in archive.read("complete_trips.jsonl").splitlines()]
            assert len(complete) == frozen["complete_trip_samples"]
            car_rows = [row for row in complete if row["car_id"] == car_id]
            assert len(car_rows) == len(samples)
            for stored, received in zip(car_rows, samples):
                for key in ("id", "utc", "sim_time", "x", "y", "sensors", "control"):
                    assert stored[key] == received[key], key
            assert json.loads(archive.read("config.json")) == config
        (output / "journal-export.zip").write_bytes(zip_raw)
        checks.append("HTTP ZIP export has a frozen complete trip prefix, valid CRC and exact received sensor/control bundles plus saved run configuration")
        app.control(dict(action="reset"))
        wait_for(app, lambda s: s.get("connected") and s.get("cars"))
        archived = doc("/api/car/history", run_id=run_id, car_id=car_id, limit=1)
        assert archived["current"] is None
        assert archived["summary"]["perception_samples"] == len(samples)
        assert archived["telemetry"][0]["sensors"] == samples[0]["sensors"]
        archived_page = doc("/api/car/history/page", run_id=run_id, car_id=car_id,
                            direction="time", sim_time=sought_time, until_id=ceiling, events_until_id=event_ceiling, include_mission="1", limit=17)
        assert archived_page["telemetry"] == sought["telemetry"]
        assert archived_page["mission"] == mission
        checks.append("Reset keeps departed vehicle sensor history readable under the old run identity")
        (output / "CAR001-perception.json").write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")
        (output / "CAR001-perception.csv").write_bytes(csv_raw)
        report = dict(status="PASS", model_version=__version__, executed_at_utc=datetime.now(timezone.utc).isoformat(), run_id=run_id,
            transport="actual MQTT 3.1.1 loopback TCP plus HTTP history/export", checks=checks,
            metrics=finished["metrics"], trip_samples=len(samples), diagnostic_frames=len(diagnostic),
            proximity_channels=8, proximity_source=proximity["source"],
            perception_summary=summary, logging=finished["logging"],
            sample_ceiling=ceiling, events_until_id=event_ceiling,
            overview={key: value for key, value in overview.items() if key != "points"},
            mission=mission,
            json_export=str(output / "CAR001-perception.json"), csv_export=str(output / "CAR001-perception.csv"))
        report["journal_export"] = str(output / "journal-export.zip")
        report["run_export_snapshot"] = frozen
        assert tested_sources == source_hashes(), "Sources changed during the MQTT sensor campaign"
        report["source_sha256"] = tested_sources
        report["sources_unchanged_during_run"] = True
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("status", "run_id", "checks", "trip_samples", "diagnostic_frames")}, ensure_ascii=False, indent=2))
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        app.close()


if __name__ == "__main__":
    main()
