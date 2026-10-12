"""Actual MQTT/HTTP durable diagnostic archive campaign, with a 120-point memory rollover.

The archive contains host-clock observations, not a maximum-throughput estimate.
The controlled observer hold is experiment-only and bounded to ten seconds.
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
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

from factory_yard import __version__
from factory_yard.journal import Journal
from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server


def validate_rates(points):
    verified = 0
    for index, point in enumerate(points):
        window = point["window_s"]
        if window is None:
            assert point["arrival_rate_hz"] is None and point["dequeue_rate_hz"] is None
            continue
        anchors = [old for old in points[:index] if old["segment"] == point["segment"] and
            math.isclose(point["elapsed_s"] - old["elapsed_s"], window, abs_tol=1e-8)]
        assert len(anchors) == 1 and 1 <= window <= 5
        anchor = anchors[0]
        for counter, rate in (("received_total", "arrival_rate_hz"), ("dequeued_total", "dequeue_rate_hz")):
            assert math.isclose(point[rate], (point[counter] - anchor[counter]) / window,
                rel_tol=1e-9, abs_tol=1e-8)
        if point["queue_growth_hz"] is not None:
            assert math.isclose(point["queue_growth_hz"],
                (point["queue_messages"] - anchor["queue_messages"]) / window, rel_tol=1e-9, abs_tol=1e-8)
        verified += 1
    return verified


def main():
    names = [p.relative_to(ROOT).as_posix() for p in (ROOT / "factory_yard").glob("*.py")]
    names.extend(("experiments/verify_transport_archive_v14.py", "config/default.json"))
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    output = ROOT / "results" / ("transport-archive-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    proof = ROOT / "results/transport-proof-v14"
    proof.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=4, parking_count=16,
        initial_car_count=4, initial_occupied_count=0, production_limit=4, parking_dwell_s=600))
    config["simulation"]["automatic_pedestrians"] = False
    config["logging"] = dict(detail_limit_mib=2, raw_segment_mib=.5)
    path = output / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, path, seed=94, speed=2)
    hold, holding, release = threading.Event(), threading.Event(), threading.Event()
    original_batch = app._observe_batch
    def controlled_batch():
        if hold.is_set():
            holding.set()
            if not release.wait(10):
                raise RuntimeError("Experimental observer hold exceeded ten seconds")
        return original_batch()
    app._observe_batch = controlled_batch
    server = http = None
    app_closed = False
    checks, observations, request_durations = [], {}, []
    started = time.monotonic()

    def request(endpoint, parameters=None, data=None, *, raw=False):
        url = f"http://127.0.0.1:{server.server_port}" + endpoint
        if parameters:
            url += "?" + urlencode(parameters)
        req = Request(url, data=None if data is None else json.dumps(data).encode(),
            headers={} if data is None else {"Content-Type": "application/json"})
        before = time.monotonic()
        with urlopen(req, timeout=30) as response:
            assert response.status == 200
            body = response.read()
        request_durations.append((time.monotonic() - before) * 1000)
        return body if raw else json.loads(body)

    def capture():
        state = request("/api/state")
        assert state["mqtt"]["capacity"]["clock"] == "host_perf_counter"
        return state

    def wait(predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = capture()
            if predicate(state):
                return state
            time.sleep(.25)
        raise AssertionError("Expected real MQTT state was not observed within campaign bound")

    try:
        app.start()
        server = create_http_server(app, 0)
        http = threading.Thread(target=server.serve_forever, daemon=True)
        http.start()
        state = wait(lambda s: s.get("connected") and len(s.get("cars", [])) == 4, 30)
        original_run = state["run_id"]
        car_id = state["cars"][0]["id"]
        # Wait for genuine 1-second host samples rather than accelerating the diagnostic clock.
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            state = capture()
            history = request("/api/transport/history", dict(run_id=original_run, limit=1))
            if history["total_count"] >= 125:
                break
            time.sleep(.7)
        assert history["total_count"] >= 125
        assert len(state["mqtt"]["capacity"]["history"]) == 120
        assert state["mqtt"]["capacity"]["history"][0]["elapsed_s"] > 0
        assert history["points"][0]["elapsed_s"] == 0
        observations["rollover"] = dict(durable_sample_count=history["total_count"],
            live_memory_sample_count=len(state["mqtt"]["capacity"]["history"]),
            first_durable_elapsed_s=history["points"][0]["elapsed_s"],
            first_memory_elapsed_s=state["mqtt"]["capacity"]["history"][0]["elapsed_s"])
        checks.append("Actual host-clock MQTT samples survive the 120-point live memory rollover in SQLite")

        hold.set()
        assert holding.wait(3)
        for _ in range(6):
            time.sleep(1.05)
            held = capture()
        measured = held["mqtt"]["capacity"]
        assert measured["arrival_rate_hz"] > 0 and measured["dequeue_rate_hz"] == 0
        assert measured["queue_growth_hz"] > 0 and measured["queue_messages"] > 0
        held_archive = request("/api/transport/history", dict(run_id=original_run, limit=500))
        recorded = held_archive["points"][-1]
        assert recorded["queue_messages"] == measured["history"][-1]["queue_messages"]
        assert recorded["snapshot_age_s"] > 5
        observations["held_diagnostic"] = recorded
        checks.append("A bounded held observer persists fresh HTTP callback/queue diagnostics and stale processed-state age")
        hold.clear()
        release.set()
        recovered = wait(lambda s: s.get("connected") and s["mqtt"]["capacity"]["queue_messages"] == 0, 20)
        observations["recovered_diagnostic"] = recovered["mqtt"]["capacity"]["history"][-1]

        first = request("/api/transport/history", dict(run_id=original_run, limit=17))
        ceiling = first["until_id"]
        summary = first["summary"]
        time.sleep(1.2)
        capture()
        latest = request("/api/transport/history", dict(run_id=original_run, limit=1))
        assert latest["until_id"] > ceiling
        points = list(first["points"])
        page = first
        while page["has_more"]:
            page = request("/api/transport/history", dict(run_id=original_run, after=page["next_after"],
                limit=17, until_id=ceiling))
            assert page["until_id"] == ceiling and page["summary"] == summary
            points.extend(page["points"])
        assert len(points) == summary["sample_count"] and len({p["id"] for p in points}) == len(points)
        verified_rates = validate_rates(points)
        assert verified_rates >= 120
        observations["frozen_prefix"] = dict(until_id=ceiling, summary=summary,
            independently_recomputed_rate_count=verified_rates, later_live_until_id=latest["until_id"])
        checks.append("Frozen 17-row HTTP pages retain every exact recorded sample without duplicates or later-prefix growth")

        parameters = dict(run_id=original_run, until_id=ceiling)
        raw_json = request("/api/transport/export", dict(parameters, format="json"), raw=True)
        raw_csv = request("/api/transport/export", dict(parameters, format="csv"), raw=True)
        exported = json.loads(raw_json)
        csv_rows = list(csv.DictReader(io.StringIO(raw_csv.decode("utf-8-sig"))))
        assert exported["points"] == points and exported["summary"] == summary
        assert len(csv_rows) == len(points)
        for csv_row, point in zip(csv_rows, points):
            assert csv_row["run_id"] == original_run
            assert all(csv_row[key] == ("" if value is None else str(value)) for key, value in point.items())
        (proof / "transport.csv").write_bytes(raw_csv)
        (proof / "transport.json").write_bytes(raw_json)
        checks.append("Actual HTTP CSV and JSON downloads share the identical frozen prefix, including null versus zero")

        chosen = points[-1]
        trip = request("/api/car/history/page", dict(run_id=original_run, car_id=car_id,
            until_id=chosen["telemetry_until_id"], events_until_id=chosen["events_until_id"], limit=17))
        # The per-car API clamps the global receipt ceiling to that car's own
        # last receipt. It must never include a receipt newer than the diagnostic.
        assert trip["until_id"] <= chosen["telemetry_until_id"]
        assert trip["events_until_id"] <= chosen["events_until_id"]
        assert all(p["id"] <= chosen["telemetry_until_id"] for p in trip["telemetry"])
        assert all(e["id"] <= chosen["events_until_id"] for e in trip["events"])
        observations["correlated_receipt_prefix"] = dict(car_id=car_id, diagnostic_id=chosen["id"],
            last_processed_snapshot_sim_time=chosen["sim_time"], telemetry_until_id=chosen["telemetry_until_id"],
            events_until_id=chosen["events_until_id"], resolved_car_until_id=trip["until_id"],
            resolved_car_events_until_id=trip["events_until_id"],
            returned_pose_ids=[p["id"] for p in trip["telemetry"]])
        checks.append("Recorded diagnostic receipt ceilings resolve the same car-trip and semantic-event prefix through real HTTP")

        raw_zip = request("/api/export", dict(format="zip"), raw=True)
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
            assert archive.testzip() is None
            zipped = json.loads(archive.read("transport.json"))
            zipped_csv = list(csv.DictReader(io.StringIO(archive.read("transport.csv").decode("utf-8-sig"))))
            metadata = json.loads(archive.read("metadata.json"))
            assert zipped["run_id"] == original_run and len(zipped_csv) == len(zipped["points"])
            assert zipped["points"][:len(points)] == points
            assert metadata["export_snapshot"]["transport_until_id"] == zipped["until_id"]
            assert metadata["transport_history_schema"] == 1
        (proof / "journal-export.zip").write_bytes(raw_zip)
        checks.append("Real streamed log ZIP has valid CRC and CSV/JSON diagnostic members from its declared SQLite snapshot")

        reset = request("/api/control", data=dict(action="reset", seed=94))
        assert reset["run_id"] != original_run
        fresh = wait(lambda s: s.get("connected") and s["run_id"] == reset["run_id"], 30)
        current = request("/api/transport/history", dict(run_id=fresh["run_id"], limit=500))
        assert current["points"][0]["segment"] == 0 and current["points"][0]["elapsed_s"] == 0
        historical = json.loads(request("/api/transport/export", dict(parameters, format="json"), raw=True))
        assert historical == exported
        observations["run_reset"] = dict(original_run_id=original_run, new_run_id=fresh["run_id"],
            new_archive_count=current["total_count"], first_new_point=current["points"][0])
        checks.append("Actual HTTP run reset keeps frozen old diagnostics retrievable and starts a distinct segment-zero origin")

        run_path = app.journal.path
        original_path = run_path.with_name(f"{original_run}-journal.sqlite3")
        app.close()
        app_closed = True
        reopened = Application(config, path)
        try:
            saved = reopened.transport_history(original_run, limit=500, until_id=ceiling)
            assert saved["points"] == points and saved["summary"] == summary
        finally:
            reopened.close()
        with_reader = Journal.open_readonly(original_path, original_run, snapshot=True)
        try:
            assert with_reader.transport_history(limit=500, until_id=ceiling)["points"] == points
        finally:
            with_reader.close()
        checks.append("A fresh Application and independent readonly SQLite reopen recover the same diagnostics after all original workers stop")

        assert hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
        artifacts = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in proof.iterdir() if p.is_file()}
        ordered = sorted(request_durations)
        report = dict(status="PASS", model_version=__version__, executed_at_utc=datetime.now(timezone.utc).isoformat(),
            command="python -m experiments.verify_transport_archive_v14", run_id=original_run,
            transport="actual MQTT3.1.1 loopback broker, independent simulator/coordinator/display, HTTP and SQLite reopen",
            scope="Finite four-car host-clock campaign at2x including genuine 120-point memory rollover and a bounded experiment-only observer hold; validates durable recorded diagnostics, not sustainable throughput or production timing guarantees.",
            elapsed_host_s=time.monotonic() - started, checks=checks, observations=observations,
            sample_count=len(points), http_observations=dict(requests=len(request_durations),
                elapsed_ms_median=ordered[len(ordered)//2], elapsed_ms_p95=ordered[int((len(ordered)-1)*.95)],
                definition="actual finite campaign calls, including archive exports/reset; not a latency guarantee"),
            config=config, source_sha256=hashes, source_unchanged_during_campaign=True, artifact_sha256=artifacts)
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "results/transport-archive-check-v14.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(status="PASS", checks=len(checks), samples=len(points), run_id=original_run,
            artifact="results/transport-archive-check-v14.json")))
    finally:
        hold.clear()
        release.set()
        if server:
            server.shutdown()
            server.server_close()
            http.join(3)
        if not app_closed:
            app.close()


if __name__ == "__main__":
    main()
