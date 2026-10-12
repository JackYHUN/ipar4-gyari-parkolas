"""Real MQTT/HTTP evidence correlation against independent received-prefix SQL.

The bounded observer hold is experiment-only. The campaign proves which saved
records were available at a diagnostic sample, not physical simultaneity or a
maximum throughput claim. No synthetic journal data is inserted.
"""
from datetime import datetime, timezone
import hashlib
import json
import socket
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from factory_yard import __version__
from factory_yard.journal import Journal
from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server


VEHICLE_COLUMNS = (
    "id", "car_id", "utc", "sim_time", "x", "y", "speed", "state", "heading",
    "slot_id", "wait_reason", "seq", "gap_before", "time_gap_before_s",
)


def sql_reference(path, sample_id):
    """Read the database directly, without calling the production journal API."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        sample = dict(connection.execute(
            "SELECT * FROM transport_samples WHERE id=?", (sample_id,)).fetchone())
        rows = connection.execute("""SELECT t.* FROM trip_samples t JOIN
            (SELECT car_id,MAX(id) last_id FROM trip_samples WHERE id<=? GROUP BY car_id) latest
            ON t.id=latest.last_id ORDER BY t.car_id""", (sample["telemetry_until_id"],)).fetchall()
        vehicles = []
        for row in rows:
            item = {key: row[key] for key in VEHICLE_COLUMNS}
            item["position_source"] = json.loads(row["details"]).get("position_source")
            vehicles.append(item)
        events = []
        for row in connection.execute("SELECT * FROM records WHERE id<=? ORDER BY id DESC",
                (sample["events_until_id"],)):
            item = dict(row)
            item["details"] = json.loads(item["details"])
            events.append(item)
        return dict(sample=sample, vehicles=vehicles, events=events)
    finally:
        connection.close()


def assert_page(page, reference, run_id):
    assert page["schema_version"] == 1
    assert page["scope"] == "received_evidence_at_transport_sample"
    assert page["run_id"] == run_id and page["sample"] == reference["sample"]
    assert page["telemetry_until_id"] == reference["sample"]["telemetry_until_id"]
    assert page["events_until_id"] == reference["sample"]["events_until_id"]
    assert page["vehicles"]["source"] == "trip_samples"
    assert page["vehicles"]["complete_received_archive"] is True
    assert page["vehicles"]["prior_missing_samples_global"] == 0
    assert page["vehicles"]["total_count"] == len(reference["vehicles"])
    assert page["events"]["total"] == len(reference["events"])
    expected_vehicles = {item["car_id"]: item for item in reference["vehicles"]}
    expected_events = {item["id"]: item for item in reference["events"]}
    for item in page["vehicles"]["items"]:
        assert item == expected_vehicles[item["car_id"]]
    for item in page["events"]["items"]:
        assert item == expected_events[item["id"]]


def frozen_trip_fields(trip):
    # The existing API intentionally also supplies current live state and
    # all-time archive counters. Resolved metadata ceilings may also move from
    # a car's former maximum to the requested global bound as new receipts
    # arrive outside the prefix. Compare saved rows and prefix calculations.
    result = {key: trip[key] for key in ("run_id", "car_id", "telemetry", "events", "target_id", "window")}
    for key in ("overview", "mission"):
        result[key] = {name: value for name, value in trip[key].items()
            if name not in ("until_id", "events_until_id")}
    return result


def assert_trip_boundaries(trip, frozen, car):
    assert car["id"] <= trip["until_id"] <= frozen["telemetry_until_id"]
    assert trip["events_until_id"] <= frozen["events_until_id"]
    assert trip["target_id"] == car["id"]
    assert trip["telemetry"][-1]["id"] == car["id"]
    assert all(item["id"] <= frozen["telemetry_until_id"] for item in trip["telemetry"])
    assert all(item["id"] <= frozen["events_until_id"] for item in trip["events"])


def main():
    names = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "factory_yard").glob("*.py"))
    names.extend(("experiments/verify_transport_context_v15.py", "config/default.json"))
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    output = ROOT / "results" / ("transport-context-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    proof = ROOT / "results/transport-context-proof-v15"
    proof.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=6, parking_count=16,
        initial_car_count=2, initial_occupied_count=0, production_limit=6,
        spawn_interval_s=8, parking_dwell_s=600))
    config["simulation"]["automatic_pedestrians"] = False
    config["logging"] = dict(detail_limit_mib=2, raw_segment_mib=.5)
    path = output / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, path, seed=95, speed=2)
    hold, holding, release = threading.Event(), threading.Event(), threading.Event()
    original_batch = app._observe_batch

    def controlled_batch():
        if hold.is_set():
            holding.set()
            if not release.wait(8):
                raise RuntimeError("Experimental observer hold exceeded eight seconds")
        return original_batch()

    app._observe_batch = controlled_batch
    server = http = None
    app_closed = False
    checks, observations, durations = [], {}, []
    started = time.monotonic()

    def save(name, document):
        (proof / name).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    def request(endpoint, parameters=None, data=None, *, expected_status=200):
        url = f"http://127.0.0.1:{server.server_port}" + endpoint
        if parameters:
            url += "?" + urlencode(parameters)
        req = Request(url, data=None if data is None else json.dumps(data).encode(),
            headers={} if data is None else {"Content-Type": "application/json"})
        before = time.monotonic()
        try:
            with urlopen(req, timeout=30) as response:
                status, body = response.status, response.read()
        except HTTPError as error:
            status, body = error.code, error.read()
        durations.append((time.monotonic() - before) * 1000)
        assert status == expected_status, (endpoint, expected_status, status, body[:300])
        return json.loads(body)

    def capture():
        return request("/api/state")

    def wait(predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = capture()
            if predicate(state):
                return state
            time.sleep(.25)
        raise AssertionError("Expected real MQTT observation was not received within campaign bound")

    try:
        app.start()
        server = create_http_server(app, 0)
        http = threading.Thread(target=server.serve_forever, daemon=True)
        http.start()
        state = wait(lambda s: s.get("connected") and len(s.get("cars", [])) >= 2, 20)
        run_id = state["run_id"]
        journal_path = app.journal.path
        # Select an actual early sample, before production admits a later car.
        deadline = time.monotonic() + 4
        frozen = None
        while time.monotonic() < deadline:
            capture()
            history = request("/api/transport/history", dict(run_id=run_id, limit=25))
            for sample in history["points"]:
                if sample["telemetry_until_id"] <= 0:
                    continue
                candidate = request("/api/transport/context", dict(run_id=run_id,
                    sample_id=sample["id"], cars_limit=1, events_limit=3))
                if candidate["vehicles"]["total_count"] == 2:
                    frozen = candidate
                    break
            if frozen:
                break
            time.sleep(.2)
        assert frozen is not None, "No genuine two-car diagnostic sample was observed"
        sample_id = frozen["sample"]["id"]
        reference = sql_reference(journal_path, sample_id)
        assert_page(frozen, reference, run_id)
        assert frozen["vehicles"]["has_more"] is True
        save("context.json", frozen)
        checks.append("Actual early MQTT diagnostic resolves the exact direct-SQL sample and scalar car/event prefixes")

        vehicle_pages = [frozen]
        vehicle_items = list(frozen["vehicles"]["items"])
        page = frozen
        while page["vehicles"]["has_more"]:
            assert len(vehicle_pages) < len(reference["vehicles"])
            page = request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
                cars_after=page["vehicles"]["next_after"], cars_limit=1, events_limit=3))
            assert_page(page, reference, run_id)
            assert len(page["vehicles"]["items"]) == 1
            assert page["vehicles"]["items"][0]["car_id"] > vehicle_items[-1]["car_id"]
            vehicle_pages.append(page)
            vehicle_items.extend(page["vehicles"]["items"])
        assert vehicle_items == reference["vehicles"]
        assert len({item["car_id"] for item in vehicle_items}) == len(vehicle_items)
        save("cars-pages.json", vehicle_pages)
        event_pages = [frozen]
        event_items = list(frozen["events"]["items"])
        page = frozen
        while page["events"]["next_before"] is not None:
            assert len(event_pages) <= len(reference["events"])
            page = request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
                cars_limit=1, events_before=page["events"]["next_before"], events_limit=3))
            assert_page(page, reference, run_id)
            assert 1 <= len(page["events"]["items"]) <= 3
            assert page["events"]["items"][0]["id"] < event_items[-1]["id"]
            event_pages.append(page)
            event_items.extend(page["events"]["items"])
        assert event_items == reference["events"]
        assert len({item["id"] for item in event_items}) == len(event_items)
        save("events-pages.json", event_pages)
        checks.append("Bounded one-car and three-event HTTP pages exhaust exactly the same frozen SQL prefixes without duplicates")

        car = reference["vehicles"][0]
        parameters = dict(run_id=run_id, car_id=car["car_id"], direction="end", limit=7,
            until_id=frozen["telemetry_until_id"], events_until_id=frozen["events_until_id"],
            include_overview=1, include_mission=1)
        trip = request("/api/car/history/page", parameters)
        assert trip["run_id"] == run_id and trip["car_id"] == car["car_id"]
        assert_trip_boundaries(trip, frozen, car)
        for key in ("sim_time", "x", "y", "speed", "state", "heading", "slot_id", "wait_reason"):
            assert trip["telemetry"][-1][key] == car[key]
        save("trip-page.json", trip)
        observations["frozen_sample"] = dict(id=sample_id, utc=frozen["sample"]["utc"],
            elapsed_s=frozen["sample"]["elapsed_s"], sim_time=frozen["sample"]["sim_time"],
            telemetry_until_id=frozen["telemetry_until_id"], events_until_id=frozen["events_until_id"],
            car_ids=[item["car_id"] for item in vehicle_items], car_last_receipt_ids=[item["id"] for item in vehicle_items],
            event_ids=[item["id"] for item in event_items], car_pages=len(vehicle_pages), event_pages=len(event_pages),
            replay_car_id=car["car_id"], replay_target_id=trip["target_id"], replay_events_until_id=trip["events_until_id"])
        checks.append("Real trip replay endpoint opens the chosen saved last car receipt under both global diagnostic ceilings")

        later = wait(lambda s: len(s.get("cars", [])) > len(reference["vehicles"]), 12)
        later_ids = {item["id"] for item in later["cars"]}
        early_ids = {item["car_id"] for item in reference["vehicles"]}
        future_car = sorted(later_ids - early_ids)[0]
        capture()
        later_history = request("/api/transport/history", dict(run_id=run_id, limit=25))
        unchanged = request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
            cars_limit=1, events_limit=3))
        assert unchanged == frozen
        later_trip = request("/api/car/history/page", parameters)
        assert_trip_boundaries(later_trip, frozen, car)
        assert frozen_trip_fields(later_trip) == frozen_trip_fields(trip)
        assert sql_reference(journal_path, sample_id) == reference
        assert later_history["until_id"] > sample_id
        future_event_id = later_history["points"][-1]["events_until_id"]
        if future_event_id <= frozen["events_until_id"]:
            later = wait(lambda s: s["sim_time"] >= later["sim_time"] + 2, 5)
            later_history = request("/api/transport/history", dict(run_id=run_id, limit=25))
            future_event_id = later_history["points"][-1]["events_until_id"]
        assert future_event_id > frozen["events_until_id"]
        request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
            cars_after=future_car), expected_status=400)
        request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
            events_before=future_event_id), expected_status=400)
        request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id + 100000), expected_status=400)
        observations["later_receipts_excluded"] = dict(live_car_ids=sorted(later_ids),
            excluded_future_car_id=future_car, later_diagnostic_id=later_history["until_id"],
            excluded_future_event_id=future_event_id)
        checks.append("Later genuinely produced cars/events never enter the frozen context or replay, and future cursors fail with HTTP400")

        # Hold outside app.lock, so callbacks and HTTP sampling remain active.
        hold.set()
        assert holding.wait(3)
        for _ in range(3):
            time.sleep(1.05)
            held_state = capture()
        held_history = request("/api/transport/history", dict(run_id=run_id, limit=100))
        held_point = held_history["points"][-1]
        assert held_point["queue_messages"] > 0 and held_point["snapshot_age_s"] > 2
        held_context = request("/api/transport/context", dict(run_id=run_id,
            sample_id=held_point["id"], cars_limit=100, events_limit=100))
        held_reference = sql_reference(journal_path, held_point["id"])
        assert_page(held_context, held_reference, run_id)
        assert held_context["vehicles"]["items"] == held_reference["vehicles"]
        hold.clear()
        release.set()
        recovered = wait(lambda s: s.get("connected") and s["mqtt"]["capacity"]["queue_messages"] == 0, 12)
        assert request("/api/transport/context", dict(run_id=run_id,
            sample_id=held_point["id"], cars_limit=100, events_limit=100)) == held_context
        save("lagged-context.json", held_context)
        observations["lagged_sample"] = dict(id=held_point["id"], utc=held_point["utc"],
            queue_messages=held_point["queue_messages"], snapshot_age_s=held_point["snapshot_age_s"],
            sim_time=held_point["sim_time"], telemetry_until_id=held_point["telemetry_until_id"],
            events_until_id=held_point["events_until_id"], received_total=held_point["received_total"],
            dequeued_total=held_point["dequeued_total"], recovered_sim_time=recovered["sim_time"],
            recovered_queue_messages=recovered["mqtt"]["capacity"]["queue_messages"])
        checks.append("An actual bounded observer backlog resolves only already processed receipts and remains unchanged after recovery")

        reset = request("/api/control", data=dict(action="reset", seed=95))
        assert reset["run_id"] != run_id
        fresh = wait(lambda s: s.get("connected") and s["run_id"] == reset["run_id"], 20)
        assert request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
            cars_limit=1, events_limit=3)) == frozen
        reset_trip = request("/api/car/history/page", parameters)
        assert_trip_boundaries(reset_trip, frozen, car)
        assert frozen_trip_fields(reset_trip) == frozen_trip_fields(trip)
        observations["reset"] = dict(original_run_id=run_id, new_run_id=fresh["run_id"],
            original_context_sample_id=sample_id)
        checks.append("A real HTTP reset preserves the explicitly selected old-run context and frozen trip independently of live data")

        app.close()
        app_closed = True
        reopened = Application(config, path)
        try:
            assert reopened.transport_context(run_id, sample_id=sample_id, cars_limit=1, events_limit=3) == frozen
        finally:
            reopened.close()
        reader = Journal.open_readonly(journal_path, run_id, snapshot=True)
        try:
            reloaded = reader.transport_context(sample_id, cars_limit=1, events_limit=3)
            assert_page(reloaded, reference, run_id)
            assert reloaded == {key: value for key, value in frozen.items() if key not in ("map", "config", "metadata")}
        finally:
            reader.close()
        assert sql_reference(journal_path, sample_id) == reference
        save("sqlite-reference.json", reference)
        checks.append("A fresh Application, independent readonly Journal, and direct SQLite reopen recover the identical original evidence")

        assert hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
        artifact_names = ("context.json", "cars-pages.json", "events-pages.json", "trip-page.json",
            "lagged-context.json", "sqlite-reference.json")
        artifacts = {str((proof / name).relative_to(ROOT)).replace("\\", "/"):
            hashlib.sha256((proof / name).read_bytes()).hexdigest() for name in artifact_names}
        ordered = sorted(durations)
        report = dict(status="PASS", model_version=__version__, executed_at_utc=datetime.now(timezone.utc).isoformat(),
            command="python -m experiments.verify_transport_context_v15", run_id=run_id,
            transport="actual MQTT3.1.1 loopback broker, independent simulator/coordinator/display, real HTTP and readonly SQLite",
            scope="Finite six-car scenario with two initially present cars at2x, later production, a bounded experiment-only observer hold, reset and reopen; receipt evidence correlation only, without physical simultaneity, causal attribution or maximum-throughput guarantees.",
            elapsed_host_s=time.monotonic() - started, checks=checks, observations=observations,
            sample_id=sample_id, independent_sql_vehicle_count=len(reference["vehicles"]),
            independent_sql_event_count=len(reference["events"]),
            http_observations=dict(requests=len(durations), elapsed_ms_median=ordered[len(ordered)//2],
                elapsed_ms_p95=ordered[int((len(ordered)-1)*.95)],
                definition="observed durations of this finite local campaign including reset; not a latency guarantee"),
            config=config, source_sha256=hashes, source_unchanged_during_campaign=True, artifact_sha256=artifacts)
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "results/transport-context-check-v15.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(status="PASS", checks=len(checks), run_id=run_id, sample_id=sample_id,
            artifact="results/transport-context-check-v15.json")))
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
