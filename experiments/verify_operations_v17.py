"""Real MQTT operational episodes, acknowledgments and received-prefix evidence.

Fault injection uses the same public HTTP controls as the demo. The short observer
hold is an experiment-only instrument outside the application lock; callbacks,
the MQTT processes and HTTP sampling continue. No incident or journal row is
inserted by the experiment.
"""
from datetime import datetime, timezone
from collections import Counter
import csv
import hashlib
import io
import json
import socket
import sqlite3
import threading
import time
import zipfile
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from factory_yard import __version__
from factory_yard.journal import Journal
from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server
from .verify_transport_context_v15 import assert_page, sql_reference


def direct_operations(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        items = [json.loads(row["payload"]) for row in connection.execute(
            "SELECT payload FROM operations_incidents ORDER BY id DESC")]
        events = []
        for row in connection.execute("SELECT * FROM records WHERE category='operations' ORDER BY id"):
            item = dict(row)
            item["details"] = json.loads(item["details"])
            events.append(item)
        control_count = connection.execute("SELECT COUNT(*) FROM records WHERE category='control'").fetchone()[0]
        return dict(items=items, events=events, control_count=control_count)
    finally:
        connection.close()


def incident(page, rule, car_id=None, *, active=None, after=0):
    return next((item for item in page["items"] if item["rule"] == rule
        and item["car_id"] == car_id and item["id"] > after
        and (active is None or item["active"] is active)), None)


def lifecycle(page):
    # Health describes live freshness, while the saved episode objects are the
    # independent evidence used for archived and reopened equality checks.
    return {key: page[key] for key in ("schema_version", "run_id", "available", "items", "until_id", "next_before")}


def reference_report(items):
    """Independent arithmetic over independently queried persisted SQL rows."""
    def interval(start, end):
        try:
            a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
            if a.tzinfo is None or b.tzinfo is None:
                return None
            seconds = (b - a).total_seconds()
            return seconds if seconds >= 0 else None
        except (TypeError, ValueError):
            return None
    counts = dict(total=len(items), active=sum(x['active'] for x in items),
        resolved=sum(not x['active'] for x in items),
        acknowledged=sum(x['acknowledged'] for x in items),
        unacknowledged=sum(not x['acknowledged'] for x in items))
    durations = {}
    for name in ('confirmation', 'resolved_active', 'acknowledgment'):
        eligible = [x for x in items if name == 'confirmation' or
                    (not x['active'] if name == 'resolved_active' else x['acknowledged'])]
        values = [interval(x['opening']['utc'] if name == 'confirmation' else x['opened_utc'],
            x['opened_utc'] if name == 'confirmation' else x['resolved_utc'] if name == 'resolved_active' else x['acknowledged_utc']) for x in eligible]
        valid = [x for x in values if x is not None]
        durations[name] = dict(valid_count=len(valid), excluded_count=len(values)-len(valid),
            min_s=min(valid) if valid else None, max_s=max(valid) if valid else None,
            mean_s=sum(valid)/len(valid) if valid else None)
    repetitions = Counter((x['rule'], x['subject']) for x in items)
    return dict(counts=counts, recurrent_episodes=sum(n-1 for n in repetitions.values()), durations=durations)


def assert_report(report, items, run_id, ceiling):
    assert report['schema_version'] == 1 and report['run_id'] == run_id
    assert report['available'] is True and report['until_id'] == ceiling
    assert report['scope'] == 'episode_prefix_current_lifecycle_snapshot'
    assert datetime.fromisoformat(report['captured_at_utc']).tzinfo is not None
    reference = reference_report(items)
    assert report['counts'] == reference['counts']
    assert report['recurrent_episodes'] == reference['recurrent_episodes']
    for name, expected in reference['durations'].items():
        for key, value in expected.items():
            observed = report['durations'][name][key]
            assert observed is None if value is None else abs(observed-value) < 1e-8, (name,key,observed,value)
    for row in report['rules']:
        matching = [x for x in items if x['rule'] == row['rule']]
        assert {key:row[key] for key in reference['counts']} == reference_report(matching)['counts']
    assert sum(row['total'] for row in report['rules']) == len(items)
    return reference


def main():
    names = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "factory_yard").glob("*.py"))
    names.extend(("experiments/verify_operations_v17.py", "experiments/verify_transport_context_v15.py", "config/default.json"))
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    output = ROOT / "results" / ("operations-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    proof = ROOT / "results/operations-proof-v17"
    proof.mkdir(parents=True, exist_ok=True)
    artifact_names = []
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=2, parking_count=16,
        initial_car_count=2, initial_occupied_count=0, production_limit=2, parking_dwell_s=600))
    config["simulation"]["automatic_pedestrians"] = False
    config["logging"] = dict(detail_limit_mib=2, raw_segment_mib=.5)
    path = output / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, path, seed=96, speed=1)
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
    run_id = None

    def save(name, document):
        (proof / name).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact_names.append(name)

    def request(endpoint, parameters=None, data=None, *, expected_status=200, return_json=True):
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
        return json.loads(body) if return_json else body

    def capture():
        return request("/api/state")

    def operations(**parameters):
        return request("/api/operations", dict(run_id=run_id, limit=100, **parameters))

    def wait(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = dict(state=capture(), operations=operations())
            if predicate(last):
                return last
            time.sleep(.2)
        raise AssertionError("Expected real MQTT observation was not received within campaign bound: "
            + json.dumps(last, ensure_ascii=False, default=str)[-2000:])

    def selected(state):
        return next((car for car in state.get("cars", []) if car["id"] == "CAR001"), {})

    try:
        app.start()
        server = create_http_server(app, 0)
        http = threading.Thread(target=server.serve_forever, daemon=True)
        http.start()
        run_id = app.run_id
        journal_path = app.journal.path
        healthy = wait(lambda pair: pair["state"].get("connected")
            and selected(pair["state"]).get("sensors", {}).get("fresh")
            and pair["operations"]["summary"]["known_rules"] == 4, 20)
        assert healthy["operations"]["thresholds"]["fault_observations"] == 2
        assert healthy["operations"]["thresholds"]["healthy_observations"] == 2
        assert healthy["operations"]["summary"]["active"] == 0
        request("/api/control", data=dict(action="pause"))
        paused = wait(lambda pair: pair["state"].get("paused") is True)
        pause_observations = []
        for _ in range(2):
            time.sleep(1.1)
            observed = dict(state=capture(), operations=operations())
            assert observed["state"].get("paused") is True
            assert observed["operations"]["summary"]["active"] == 0
            pause_observations.append(dict(sim_time=observed["state"]["sim_time"],
                operations=observed["operations"], transport=observed["state"]["mqtt"]["transport"]))
        request("/api/control", data=dict(action="resume"))
        wait(lambda pair: pair["state"].get("paused") is False and pair["state"].get("connected"))
        save("healthy-paused.json", pause_observations)
        checks.append("Actual fresh MQTT observations remain free of active incidents during a paused model; HTTP polling does not create fault evidence")

        request("/api/control", data=dict(action="sensor_failure", seconds=8))
        fault = wait(lambda pair: incident(pair["operations"], "sensor_unavailable", "CAR001", active=True)
            and selected(pair["state"]).get("state") == "sensor_stop"
            and selected(pair["state"]).get("speed") == 0
            and selected(pair["state"]).get("control", {}).get("target_speed_m_s") == 0)
        first = incident(fault["operations"], "sensor_unavailable", "CAR001", active=True)
        assert first["revision"] == 1 and first["acknowledged"] is False
        assert selected(fault["state"])["speed"] == 0
        assert selected(fault["state"])["control"]["target_speed_m_s"] == 0
        assert first["opening"]["transport_sample_id"] < first["latest"]["transport_sample_id"]
        save("sensor-opened.json", fault)
        observations["sensor_first"] = dict(id=first["id"], opening=first["opening"],
            confirmed_at=first["opened_utc"], active=True, acknowledged=False, car_state="sensor_stop")
        checks.append("A real MQTT sensor outage opens an unacknowledged episode after two saved observations and the received vehicle really stops")

        before_ack = direct_operations(journal_path)
        ack_data = dict(run_id=run_id, id=first["id"], expected_revision=first["revision"],
            note="V16 rendszerkampány: az érzékelőhiba ellenőrzése folyamatban.")
        acknowledged = request("/api/operations/acknowledge", data=ack_data)
        assert acknowledged["ok"] is True and acknowledged["already_acknowledged"] is False
        assert acknowledged["item"]["active"] is True and acknowledged["item"]["acknowledged"] is True
        assert acknowledged["item"]["revision"] == first["revision"] + 1
        duplicate = request("/api/operations/acknowledge", data=ack_data)
        assert duplicate["already_acknowledged"] is True
        for key in ("revision", "acknowledged_utc", "note", "active", "acknowledged"):
            assert duplicate["item"][key] == acknowledged["item"][key]
        request("/api/operations/acknowledge", data=dict(ack_data, expected_revision=999999), expected_status=409)
        request("/api/operations/acknowledge", data=dict(ack_data, run_id="not_current_run"), expected_status=409)
        still_fault = wait(lambda pair: (current := incident(pair["operations"], "sensor_unavailable", "CAR001", active=True))
            and current["acknowledged"] is True and selected(pair["state"]).get("state") == "sensor_stop", 3)
        after_ack = direct_operations(journal_path)
        assert after_ack["control_count"] == before_ack["control_count"]
        ack_events = [event for event in after_ack["events"] if event["event_type"] == "incident_acknowledged"
            and event["details"]["incident_id"] == first["id"]]
        assert len(ack_events) == 1 and ack_events[0]["details"]["note"] == ack_data["note"]
        save("sensor-acknowledged.json", dict(response=acknowledged, duplicate=duplicate,
            audit=ack_events, control_count_before=before_ack["control_count"], control_count_after=after_ack["control_count"],
            received_after=still_fault))
        checks.append("Explicit-run revision-checked acknowledgment is idempotent, audited once, rejects stale/wrong-run requests and leaves the active stopped-car fault unchanged without a movement-control record")

        sample_id = first["opening"]["transport_sample_id"]
        opening = request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
            cars_limit=100, events_limit=100))
        reference = sql_reference(journal_path, sample_id)
        assert_page(opening, reference, run_id)
        assert opening["vehicles"]["items"] == reference["vehicles"]
        for key in ("telemetry_until_id", "events_until_id", "sim_time", "utc"):
            assert opening["sample"][key] == first["opening"][key]
        opened_event = next(event for event in after_ack["events"] if event["event_type"] == "incident_opened"
            and event["details"]["incident_id"] == first["id"])
        assert opened_event["id"] > opening["events_until_id"]
        car = next(item for item in opening["vehicles"]["items"] if item["car_id"] == "CAR001")
        trip = request("/api/car/history/page", dict(run_id=run_id, car_id="CAR001", direction="end", limit=3,
            until_id=opening["telemetry_until_id"], events_until_id=opening["events_until_id"],
            include_overview=1, include_mission=1))
        assert trip["target_id"] == car["id"] == trip["telemetry"][-1]["id"]
        assert trip["telemetry"][-1]["sensors"]["sample_sim_time_s"] is not None
        save("opening-context.json", opening)
        save("opening-sql-reference.json", reference)
        save("opening-trip.json", trip)
        checks.append("The incident opening links to the exact independent SQLite received-prefix context and stored car/sensor receipt, excluding its later lifecycle audit event")

        recovered = wait(lambda pair: (current := incident(pair["operations"], "sensor_unavailable", "CAR001", after=0))
            and current["id"] == first["id"] and current["active"] is False
            and current["acknowledged"] is True and selected(pair["state"]).get("sensors", {}).get("fresh"), 16)
        resolved_first = incident(recovered["operations"], "sensor_unavailable", "CAR001")
        assert resolved_first["resolved_utc"] is not None
        assert resolved_first["revision"] == acknowledged["item"]["revision"] + 1
        assert request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
            cars_limit=100, events_limit=100)) == opening
        request("/api/control", data=dict(action="sensor_failure", seconds=8))
        recurrence = wait(lambda pair: incident(pair["operations"], "sensor_unavailable", "CAR001", active=True, after=first["id"]))
        second = incident(recurrence["operations"], "sensor_unavailable", "CAR001", active=True, after=first["id"])
        assert second["acknowledged"] is False and second["revision"] == 1
        assert second["opening"]["transport_sample_id"] > first["latest"]["transport_sample_id"]
        save("sensor-resolved-recurrent.json", dict(resolved=resolved_first, recurrent=second))
        observations["sensor_recurrence"] = dict(resolved_id=resolved_first["id"], resolved_revision=resolved_first["revision"],
            new_id=second["id"], acknowledged=False, revision=second["revision"])
        checks.append("Two genuinely healthy observations resolve the acknowledged sensor fault; its actual recurrence creates a new unacknowledged episode while the original opening context stays fixed")

        hold.set()
        assert holding.wait(3)
        held_observations = []
        for _ in range(5):
            time.sleep(1.1)
            held_observations.append(dict(state=capture(), operations=operations()))
        held = held_observations[-1]
        lag = incident(held["operations"], "processing_lag", active=True)
        assert lag is not None
        assert lag["measurements"]["callback_snapshot_age_s"] <= .75
        assert lag["measurements"]["processed_snapshot_age_s"] > 2
        assert incident(held["operations"], "communication_unavailable", active=True) is None
        preserved = incident(held["operations"], "sensor_unavailable", "CAR001", active=True, after=first["id"])
        assert preserved is not None and preserved["id"] == second["id"] and preserved["acknowledged"] is False
        held_point_id = lag["latest"]["transport_sample_id"]
        held_context = request("/api/transport/context", dict(run_id=run_id, sample_id=held_point_id,
            cars_limit=100, events_limit=100))
        assert_page(held_context, sql_reference(journal_path, held_point_id), run_id)
        hold.clear()
        release.set()
        healthy_after_hold = wait(lambda pair: pair["state"].get("connected")
            and pair["state"]["mqtt"]["capacity"]["queue_messages"] == 0
            and (current := incident(pair["operations"], "processing_lag")) and current["active"] is False
            and (sensor := incident(pair["operations"], "sensor_unavailable", "CAR001", after=first["id"]))
            and sensor["active"] is False, 18)
        assert request("/api/transport/context", dict(run_id=run_id, sample_id=held_point_id,
            cars_limit=100, events_limit=100)) == held_context
        save("processing-lag.json", dict(observations=held_observations, context=held_context,
            recovered=healthy_after_hold["operations"]))
        observations["processing_lag"] = dict(id=lag["id"], opening=lag["opening"], latest=lag["latest"],
            callback_snapshot_age_s=lag["measurements"]["callback_snapshot_age_s"],
            processed_snapshot_age_s=lag["measurements"]["processed_snapshot_age_s"],
            queue_messages=held_context["sample"]["queue_messages"], preserved_sensor_id=preserved["id"],
            recovered_queue_messages=healthy_after_hold["state"]["mqtt"]["capacity"]["queue_messages"])
        checks.append("An actual bounded observer backlog opens processing_lag with fresh MQTT callbacks, preserves an unknown sensor fault, excludes communication loss, then resolves only after real healthy processing observations")

        request("/api/control", data=dict(action="disconnect", seconds=8))
        disconnected = wait(lambda pair: incident(pair["operations"], "communication_unavailable", active=True), 12)
        communication = incident(disconnected["operations"], "communication_unavailable", active=True)
        assert incident(disconnected["operations"], "processing_lag", active=True) is None
        communicating = wait(lambda pair: pair["state"].get("connected")
            and (current := incident(pair["operations"], "communication_unavailable")) and current["active"] is False, 18)
        save("communication-outage.json", dict(opened=disconnected["operations"], recovered=communicating["operations"]))
        observations["communication"] = dict(id=communication["id"], opening=communication["opening"],
            opened_utc=communication["opened_utc"], resolved_utc=incident(communicating["operations"], "communication_unavailable")["resolved_utc"])
        checks.append("A separately injected real simulator communication outage opens communication_unavailable rather than processing_lag, and actual returned snapshots establish recovery")

        final_live = operations()
        direct = direct_operations(journal_path)
        assert final_live["items"] == direct["items"]
        pages, all_items, before = [], [], None
        while True:
            parameters = dict(run_id=run_id, limit=1, until_id=final_live["until_id"])
            if before is not None:
                parameters["before"] = before
            page = request("/api/operations", parameters)
            pages.append(page)
            all_items.extend(page["items"])
            if page["next_before"] is None:
                break
            before = page["next_before"]
            assert len(pages) < len(final_live["items"])
        assert all_items == direct["items"] and len({item["id"] for item in all_items}) == len(all_items)
        request("/api/operations", dict(run_id=run_id, before=final_live["until_id"] + 10000), expected_status=400)
        save("episodes-pages.json", pages)
        save("episodes-sql-reference.json", direct)
        observations["archive"] = dict(episode_count=len(direct["items"]), audit_event_count=len(direct["events"]),
            single_episode_pages=len(pages), active=final_live["summary"]["active"],
            unacknowledged=final_live["summary"]["unacknowledged"])
        checks.append("Bounded one-episode HTTP pages reproduce the independent durable SQLite lifecycle list without duplicates and reject a future cursor")

        full_report = request('/api/operations/report', dict(run_id=run_id, until_id=final_live['until_id']))
        independent_report = assert_report(full_report, direct['items'], run_id, final_live['until_id'])
        prefix_items = [item for item in direct['items'] if item['id'] <= 2]
        prefix_report = request('/api/operations/report', dict(run_id=run_id, until_id=2))
        assert_report(prefix_report, prefix_items, run_id, 2)
        assert full_report['counts']['active'] == 0 and full_report['recurrent_episodes'] == 2
        assert prefix_report['counts']['total'] == 2 and prefix_report['recurrent_episodes'] == 0
        save('incident-report.json', full_report)
        save('incident-report-prefix.json', prefix_report)
        save('incident-report-sql-reference.json', independent_report)
        checks.append('The actual HTTP complete and two-episode reports match independent SQL lifecycle counts, recurrence groups, and confirmation/resolution/acknowledgment UTC arithmetic')

        incident_json = request('/api/operations/export', dict(run_id=run_id, until_id=final_live['until_id'], format='json'))
        assert incident_json['items'] == direct['items']
        assert_report(incident_json['report'], direct['items'], run_id, final_live['until_id'])
        assert incident_json['run_id'] == run_id and incident_json['until_id'] == final_live['until_id']
        csv_bytes = request('/api/operations/export', dict(run_id=run_id, until_id=final_live['until_id'], format='csv'), return_json=False)
        assert csv_bytes.startswith(b'\xef\xbb\xbf') and csv_bytes.count(b'\xef\xbb\xbf') == 1
        rows = list(csv.DictReader(io.StringIO(csv_bytes.decode('utf-8-sig'))))
        assert len(rows) == len(direct['items'])
        for row, item in zip(rows, direct['items']):
            assert int(row['id']) == item['id'] and row['run_id'] == run_id
            assert row['rule'] == item['rule'] and row['subject'] == item['subject']
            assert row['opened_utc'] == item['opened_utc'] and row['note'] == (item['note'] or '')
            for prefix in ('opening', 'latest'):
                for key in ('transport_sample_id', 'telemetry_until_id', 'events_until_id'):
                    assert int(row[prefix+'_'+key]) == item[prefix][key]
                assert row[prefix+'_utc'] == item[prefix]['utc']
            expected = reference_report([item])['durations']
            for column, name in (('confirmation_s','confirmation'),('resolved_active_s','resolved_active'),('acknowledgment_s','acknowledgment')):
                value = expected[name]['mean_s']
                assert row[column] == '' if value is None else abs(float(row[column])-value) < 1e-8
        save('incidents-export.json', incident_json)
        (proof/'incidents-export.csv').write_bytes(csv_bytes)
        artifact_names.append('incidents-export.csv')
        prefix_json = request('/api/operations/export', dict(run_id=run_id, until_id=2, format='json'))
        assert prefix_json['items'] == prefix_items
        assert_report(prefix_json['report'], prefix_items, run_id, 2)
        observations['incident_report'] = dict(episode_count=len(rows), counts=full_report['counts'],
            recurrent_episodes=full_report['recurrent_episodes'], durations=full_report['durations'],
            prefix_episode_count=len(prefix_items), csv_size_bytes=len(csv_bytes))
        checks.append('Dedicated JSON and UTF8-BOM CSV downloads preserve every SQL episode, original note and receipt evidence; derived interval columns and a separately bounded prefix agree with the independent reference')
        for endpoint in ('/api/operations/report', '/api/operations/export'):
            request(endpoint, dict(run_id=run_id, until_id=final_live['until_id']+10000), expected_status=400)
            request(endpoint, expected_status=400)
            request(endpoint, dict(run_id='not-a-known-run'), expected_status=400)
        request('/api/operations/export', dict(run_id=run_id, format='pdf'), expected_status=400)
        checks.append('Report/export HTTP endpoints reject unknown or absent run identities, unsupported formats and future episode boundaries before emitting a successful download')

        exported_bytes = request("/api/export", dict(format="zip"), return_json=False)
        (proof / "run-export.zip").write_bytes(exported_bytes)
        artifact_names.append("run-export.zip")
        with zipfile.ZipFile(io.BytesIO(exported_bytes)) as archive:
            assert archive.testzip() is None
            metadata = json.loads(archive.read("metadata.json"))
            exported_catalog = json.loads(archive.read("incidents.json"))
            exported_events = [json.loads(line) for line in archive.read("events.jsonl").decode("utf-8").splitlines()]
            assert json.loads(archive.read("config.json")) == config
        boundaries = metadata["export_snapshot"]
        assert metadata["model_version"] == __version__ and metadata["run_id"] == run_id
        assert metadata["operations_history_schema"] == 1
        assert boundaries["operations_schema_version"] == 1 and boundaries["operations_available"] is True
        assert boundaries["operations_member"] == "incidents.json"
        assert exported_catalog["schema_version"] == 1 and exported_catalog["available"] is True
        assert exported_catalog["run_id"] == run_id
        assert exported_catalog["until_id"] == boundaries["operations_until_id"] == final_live["until_id"]
        assert exported_catalog["items"] == direct["items"]
        assert exported_catalog["summary"]["total"] == len(direct["items"])
        assert exported_catalog["summary"]["active"] == 0
        assert len(exported_events) == boundaries["records"]
        assert all(event["id"] <= boundaries["records_until_id"] for event in exported_events)
        assert all(event["run_id"] == run_id for event in exported_events)
        for item in exported_catalog["items"]:
            assert item["id"] <= boundaries["operations_until_id"]
            for evidence in (item["opening"], item["latest"]):
                assert evidence["transport_sample_id"] <= boundaries["transport_until_id"]
                assert evidence["telemetry_until_id"] <= boundaries["trips_until_id"]
                assert evidence["events_until_id"] <= boundaries["records_until_id"]
        exported_ack = [event for event in exported_events if event["event_type"] == "incident_acknowledged"]
        assert len(exported_ack) == len(ack_events) == 1
        # The per-run SQLite table stores the audit columns; export adds the
        # enclosing run identity. Keep every SQL field and nested detail exact.
        assert exported_ack == [dict(run_id=run_id, **event) for event in ack_events]
        save("run-export-operations.json", dict(metadata=metadata, incidents=exported_catalog,
            acknowledgment_audit=exported_ack, zip_size_bytes=len(exported_bytes),
            zip_sha256=hashlib.sha256(exported_bytes).hexdigest(), crc_status="PASS"))
        observations["run_export"] = dict(zip_size_bytes=len(exported_bytes), episode_count=len(exported_catalog["items"]),
            operations_until_id=boundaries["operations_until_id"], records_until_id=boundaries["records_until_id"],
            transport_until_id=boundaries["transport_until_id"], trips_until_id=boundaries["trips_until_id"],
            semantic_ack_count=len(exported_ack))
        checks.append("The actual HTTP run ZIP has valid CRC, a schema1 incidents catalog equal to independent SQL, consistent frozen metadata/receipt boundaries and the single original acknowledgment audit")

        reset = request("/api/control", data=dict(action="reset", seed=96))
        assert reset["run_id"] != run_id
        archived_report = request('/api/operations/report', dict(run_id=run_id, until_id=final_live['until_id']))
        assert_report(archived_report, direct['items'], run_id, final_live['until_id'])
        archived_export = request('/api/operations/export', dict(run_id=run_id, until_id=final_live['until_id'], format='json'))
        assert archived_export['items'] == incident_json['items']
        new_report = request('/api/operations/report', dict(run_id=reset['run_id']))
        assert_report(new_report, [], reset['run_id'], 0)
        assert new_report['counts']['total'] == 0 and new_report['durations']['acknowledgment']['mean_s'] is None
        save('incident-reports-after-reset.json', dict(archived=archived_report, current=new_report))
        checks.append('A real run reset leaves selected archived reports/downloads tied to their original episodes while the new current run has empty counts and no invented duration')
        archived = operations()
        assert archived["current_live"] is False
        assert lifecycle(archived) == lifecycle(final_live)
        request("/api/operations/acknowledge", data=ack_data, expected_status=409)
        assert request("/api/transport/context", dict(run_id=run_id, sample_id=sample_id,
            cars_limit=100, events_limit=100)) == opening
        app.close()
        app_closed = True
        reopened = Application(config, path)
        try:
            reopened_page = reopened.operations(run_id)
            assert lifecycle(reopened_page) == lifecycle(final_live)
            assert reopened_page["current_live"] is False
        finally:
            reopened.close()
        reader = Journal.open_readonly(journal_path, run_id, snapshot=True)
        try:
            reloaded = reader.operations(limit=100)
            assert lifecycle(reloaded) == lifecycle(final_live)
        finally:
            reader.close()
        assert direct_operations(journal_path)["items"] == direct["items"]
        save("archived-reopened.json", dict(reset=reset, archived=archived, reopened=reopened_page, readonly=reloaded))
        observations["reset"] = dict(original_run_id=run_id, new_run_id=reset["run_id"], archived_episodes=len(archived["items"]))
        checks.append("A real reset preserves the old-run episodes and exact opening evidence, rejects archived acknowledgment, and fresh Application/read-only Journal/direct SQLite reopen retain identical lifecycle objects")

        assert source_hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
        artifacts = {str((proof / name).relative_to(ROOT)).replace("\\", "/"):
            hashlib.sha256((proof / name).read_bytes()).hexdigest() for name in artifact_names}
        ordered = sorted(durations)
        report = dict(status="PASS", model_version=__version__, executed_at_utc=datetime.now(timezone.utc).isoformat(),
            command="python -m experiments.verify_operations_v17", run_id=run_id,
            transport="actual MQTT3.1.1 loopback broker, independent simulator/coordinator/display, real HTTP and readonly SQLite",
            scope="Finite two-car Győr-inspired simulation at1x, real public fault controls, acknowledgment, bounded experiment-only observer hold, reset and reopen; operational received-data episodes, without physical safety, standard compliance or maximum-capacity guarantees.",
            elapsed_host_s=time.monotonic() - started, checks=checks, observations=observations,
            http_observations=dict(requests=len(durations), elapsed_ms_median=ordered[len(ordered)//2],
                elapsed_ms_p95=ordered[int((len(ordered)-1)*.95)],
                definition="observed local campaign request durations including reset; not a response-time guarantee"),
            config=config, source_sha256=source_hashes, source_unchanged_during_campaign=True, artifact_sha256=artifacts)
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "results/operations-check-v17.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(status="PASS", checks=len(checks), run_id=run_id,
            artifact="results/operations-check-v17.json")))
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
