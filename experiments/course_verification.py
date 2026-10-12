"""Course proof campaign: real loopback MQTT/HTTP plus named model-level tests.

Run from projekt: python -m experiments.course_verification
No database rows or application internals are patched by this campaign. Public
HTTP controls inject faults; an ordinary MQTT client repeats CAR001's original
parking request. Full snapshots are recorded as received, without interpolation.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

from factory_yard import __version__
from factory_yard.mqtt_bus import Bus
from factory_yard.server import Application, ROOT, create_http_server


TEST_IDS = [
    "tests.test_coordinator.CoordinatorTests.test_two_close_requests_never_share_a_space",
    "tests.test_coordinator.CoordinatorTests.test_duplicate_request_reuses_grant_without_second_reservation",
    "tests.test_coordinator.CoordinatorTests.test_new_request_id_cannot_reserve_twice_for_same_car",
    "tests.test_coordinator.CoordinatorTests.test_no_parking_grant_before_first_sensor_sample",
    "tests.test_coordinator.CoordinatorTests.test_reordered_sensor_sample_cannot_overwrite_or_refresh_state",
    "tests.test_coordinator.CoordinatorTests.test_stale_payload_and_foreign_run_do_not_update_sensor",
    "tests.test_coordinator.CoordinatorTests.test_timeout_never_releases_logical_reservation",
    "tests.test_coordinator.CoordinatorTests.test_departure_requires_matching_id_and_later_empty_sensor",
    "tests.test_coordinator.CoordinatorTests.test_late_duplicate_request_cannot_replay_closed_reservation",
    "tests.test_coordinator.CoordinatorTests.test_zone_timeout_does_not_free_occupied_area",
    "tests.test_coordinator.CoordinatorTests.test_zone_exit_needs_matching_grant_and_whole_vehicle_outside",
    "tests.test_coordinator_audit.CoordinatorAuditTests.test_selected_candidate_cost_and_replay_are_correlated_without_new_reservation",
    "tests.test_coordinator_audit.CoordinatorAuditTests.test_rejected_frame_reasons_are_visible_and_do_not_refresh_physical_evidence",
    "tests.test_coordinator_audit.CoordinatorAuditTests.test_parking_release_records_later_empty_frame_certificate",
]


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = sorted((ROOT / "factory_yard").rglob("*.py"))
    paths += [ROOT / name for name in ("config/default.json", "experiments/course_verification.py",
        "tests/test_coordinator.py", "tests/test_coordinator_audit.py")]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in paths if "__pycache__" not in p.parts}


class ReceivedRecorder:
    """Separate actual MQTT subscriber; never fills missing/outage frames."""
    def __init__(self, config, run_id, output):
        self.bus = Bus(config["mqtt"], run_id, "course-recorder", ["snapshot", "coordinator", "audit",
            "events", "requests/parking", "commands/+", "sensors/parking"])
        self.output, self.run_id = output, run_id
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.receipts, self.errors, self.owners = [], [], {}
        self.receipt_count = self.frame_count = self.central_count = 0
        self.first_sim_time = self.last_sim_time = None
        self.central, self.central_ns = None, None

    def start(self):
        self.bus.start()
        self.thread = threading.Thread(target=self._record, daemon=True)
        self.thread.start()

    def _record(self):
        with (self.output / "full-visual-frames.jsonl").open("w", encoding="utf-8") as frames, \
                (self.output / "mqtt-correlation.jsonl").open("w", encoding="utf-8") as packets:
            while not self.stop.is_set() or not self.bus.inbox.empty():
                batch = list(self.bus.drain())
                if not batch:
                    self.stop.wait(.015)
                for topic, payload, received_ns in batch:
                    self.receipt_count += 1
                    row = dict(receipt_id=self.receipt_count, received_ns=received_ns,
                        received_utc=payload.get("received_utc"), topic=topic, payload=payload)
                    if topic == "coordinator":
                        self.central, self.central_ns = payload, received_ns
                        self.central_count += 1
                        slots = payload["slots"]
                        owners = [s["car_id"] for s in slots if s["car_id"]]
                        ids = [s["reservation_id"] for s in slots if s["reservation_id"]]
                        if len(owners) != len(set(owners)) or len(ids) != len(set(ids)):
                            self.errors.append(dict(receipt_id=self.receipt_count, reason="duplicate_logical_owner", slots=slots))
                        # Neither finite campaign has departures (dwell = 1000 s).
                        # Every already observed reservation must therefore persist.
                        for slot in slots:
                            current = (slot["car_id"], slot["reservation_id"])
                            if slot["id"] in self.owners and current != self.owners[slot["id"]]:
                                self.errors.append(dict(receipt_id=self.receipt_count, reason="reservation_changed_without_departure",
                                    slot_id=slot["id"], previous=self.owners[slot["id"]], observed=current))
                            if slot["car_id"]:
                                self.owners[slot["id"]] = current
                    elif topic == "snapshot":
                        self.frame_count += 1
                        sim_time = payload["sim_time"]
                        if self.last_sim_time is not None and sim_time < self.last_sim_time:
                            self.errors.append(dict(receipt_id=self.receipt_count, reason="snapshot_clock_reversed"))
                        self.first_sim_time = sim_time if self.first_sim_time is None else self.first_sim_time
                        self.last_sim_time = sim_time
                        frames.write(json.dumps(dict(schema_version=1, frame_index=self.frame_count,
                            **row, sim_time=sim_time, snapshot=payload,
                            central_overlay=self.central, central_overlay_received_ns=self.central_ns),
                            ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
                    else:
                        packets.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
                        with self.lock:
                            self.receipts.append(row)

    def matching(self, topic, field, value):
        with self.lock:
            return [r for r in self.receipts if r["topic"] == topic and r["payload"].get(field) == value]

    def close(self):
        self.bus.close()
        self.stop.set()
        self.thread.join(5)
        assert not self.thread.is_alive(), "A snapshot-rögzítő nem fejeződött be"
        assert not self.errors, self.errors
        return dict(run_id=self.run_id, actual_mqtt=True, frames=self.frame_count,
            coordinator_frames=self.central_count, received_messages=self.receipt_count,
            first_sim_time=self.first_sim_time, last_sim_time=self.last_sim_time,
            logical_reservations_observed=len(self.owners), invariant_violations=self.errors,
            interpolation=False, outage_frames_filled=False,
            frame_definition="Every received simulator snapshot, with the last independently received coordinator overlay and its own receipt clock; this is model reconstruction, not camera/UI capture")


class Campaign:
    def __init__(self, base, folder, *, full=False):
        self.folder = folder
        folder.mkdir()
        self.config = json.loads(json.dumps(base))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.config["mqtt"]["port"] = probe.getsockname()[1]
        self.config["simulation"].update(production_limit=6, parking_dwell=1000,
            automatic_pedestrians=False, initial_occupied_count=16 if full else 4)
        self.seed, self.speed = 123, 8 if full else 4
        save(folder / "config.json", self.config)
        self.app = Application(self.config, folder / "config.json", seed=self.seed, speed=self.speed)
        self.server = self.http = self.recorder = None
        self.http_records, self.cases = [], []

    def start(self):
        self.app.start()
        self.recorder = ReceivedRecorder(self.config, self.app.run_id, self.folder)
        self.recorder.start()
        self.server = create_http_server(self.app, 0)
        self.http = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.http.start()
        return self.wait(lambda s: s.get("connected") and s.get("cars"), 15)

    def request(self, endpoint="/api/state", parameters=None, data=None, *, raw=False):
        url = f"http://127.0.0.1:{self.server.server_port}{endpoint}"
        if parameters:
            url += "?" + urlencode(parameters)
        request = Request(url, data=json.dumps(data).encode("utf-8") if data is not None else None,
            headers={"Content-Type": "application/json"} if data is not None else {})
        started = time.monotonic()
        with urlopen(request, timeout=30) as response:
            result = response.read()
            status = response.status
        record = dict(utc=utc(), method="POST" if data is not None else "GET", endpoint=endpoint,
            parameters=parameters, data=data, status=status, elapsed_host_ms=(time.monotonic()-started)*1000)
        document = None if raw else json.loads(result)
        if data is not None:
            record["response"] = document
        self.http_records.append(record)
        return result if raw else document

    def wait(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            last = self.request()
            if predicate(last):
                return last
            time.sleep(.04)
        save(self.folder / "timeout-state.json", last)
        raise AssertionError(f"HTTP-várakozás időtúllépés: {last.get('sim_time')}, {last.get('metrics')}, {last.get('warnings')}")

    def wait_receipt(self, topic, field, value, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.recorder.matching(topic, field, value)
            if found:
                return found[-1]
            self.request()
            time.sleep(.04)
        raise AssertionError(f"Nem érkezett MQTT-bizonyíték: {topic}/{field}={value}")

    def control(self, action, **values):
        return self.request("/api/control", data=dict(action=action, **values))

    def mark(self, case_id, expected, observed, state=None):
        if state is not None:
            save(self.folder / f"{case_id}-state.json", state)
        case = dict(id=case_id, expected=expected, observed=observed, status="PASS", run_id=self.app.run_id)
        self.cases.append(case)
        print(f"{case_id}: PASS", flush=True)
        return case

    def archive(self):
        self.control("pause")
        self.wait(lambda s: s.get("paused"))
        events, before = [], None
        while True:
            page = self.request("/api/logs", dict(limit=500, **({"before": before} if before else {})))
            events.extend(page["items"])
            before = page["next_before"]
            if before is None:
                break
        events.sort(key=lambda r: r["id"])
        save(self.folder / "events.json", events)
        data = self.request("/api/export", dict(format="zip"), raw=True)
        (self.folder / "run-export.zip").write_bytes(data)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert archive.testzip() is None
            metadata = json.loads(archive.read("metadata.json"))
            assert metadata["run_id"] == self.app.run_id
            assert json.loads(archive.read("config.json")) == self.config
        save(self.folder / "export-metadata.json", metadata)
        save(self.folder / "http-requests.json", self.http_records)
        return events

    def close(self):
        recording = self.recorder.close() if self.recorder else None
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.http.join(3)
        self.app.close()
        if self.app.run_dir:
            for filename in ("broker.log", "coordinator.log", "simulator.log"):
                source = self.app.run_dir / filename
                if source.exists():
                    shutil.copyfile(source, self.folder / filename)
        if recording:
            save(self.folder / "recording-summary.json", recording)
        return recording


def car(state, car_id="CAR001"):
    return next((c for c in state.get("cars", []) if c["id"] == car_id), {})


def core_campaign(base, output):
    run = Campaign(base, output / "core")
    report = None
    try:
        run.start()
        moving = run.wait(lambda s: 28 <= car(s).get("x", 0) <= 35 and car(s).get("speed", 0) > 1)
        first = car(moving)
        original_request = first["request_id"]
        original_reservation = first["reservation_id"]
        # Actual protocol repetition: same current simulator car/request, new
        # publication identity; no fabricated sensor, telemetry or SQL records.
        repeated = run.recorder.bus.publish("requests/parking", dict(car_id="CAR001",
            request_id=original_request, origin=base["world"]["origin"], sim_time=moving["sim_time"]))
        repeated.wait_for_publish(timeout=2)
        replay = run.wait_receipt("audit", "event_type", "parking_grant_replayed")
        run.wait_receipt("events", "type", "command_duplicate")
        assert replay["payload"]["reservation_id"] == original_reservation
        duplicate_state = run.request()
        assert sum(s.get("car_id") == "CAR001" for s in duplicate_state["parking"]) == 1
        run.mark("S02-idempotencia", "Ugyanaz a car_id/request_id ugyanazt a reservation_id-t kapja; egyetlen logikai helyfoglalás.",
            dict(car_id="CAR001", request_id=original_request, reservation_id=original_reservation,
                audit_receipt_id=replay["receipt_id"], mqtt_audit=replay["payload"],
                close_distinct_requests_proof="Targeted test ID: test_two_close_requests_never_share_a_space; direct Coordinator test, not a concurrent TCP load test"), duplicate_state)
        run.control("pedestrian", crossing_id="ZEBRA1")
        stopped = run.wait(lambda s: car(s).get("state") == "pedestrian_stop" and car(s).get("speed") == 0)
        assert car(stopped)["x"] + base["vehicle"]["length"] / 2 < 43
        resumed = run.wait(lambda s: car(s).get("x", 0) > 45 and car(s).get("speed", 0) > .5)
        run.mark("S04-zebra", "Helyi virtuális észlelésből fékezés, teljes megállás a ZEBRA1 előtt, majd mozgás a szabaddá válás után.",
            dict(car_id="CAR001", stopped_sim_time=stopped["sim_time"], stopped_x_m=car(stopped)["x"],
                stopped_speed_m_s=car(stopped)["speed"], control=car(stopped)["control"],
                resumed_sim_time=resumed["sim_time"], resumed_speed_m_s=car(resumed)["speed"]), stopped)
        run.control("speed", value=8)
        run.wait(lambda s: s.get("speed") == 8)
        run.control("sensor_failure", seconds=24)
        sensor_stopped = run.wait(lambda s: car(s).get("state") == "sensor_stop" and car(s).get("speed") == 0)
        assert car(sensor_stopped)["control"]["target_speed_m_s"] == 0
        assert car(sensor_stopped)["sensors"]["fresh"] is False
        assert car(sensor_stopped)["control"]["sensor_age_s"] > base["vehicle"]["sensor_timeout"]
        held_clock = car(sensor_stopped)["sensors"]["sample_sim_time_s"]
        # Admission can be blocked by a stopped car at the production exit.
        # Probe a new logical requester only after the genuine received sensor
        # has expired. The probe has no simulated physical car and must never
        # obtain a reservation; its real MQTT denial is labelled as such.
        run.wait(lambda s: any("A parkolóérzékelés még nem friss" in w for w in s.get("warnings", [])), 10)
        probe = run.recorder.bus.publish("requests/parking", dict(car_id="COURSE_STALE_PROBE",
            request_id="COURSE_STALE_PROBE-parking", origin=base["world"]["origin"],
            sim_time=run.request()["sim_time"]))
        probe.wait_for_publish(timeout=2)
        stale = run.wait_receipt("audit", "car_id", "COURSE_STALE_PROBE", 5)
        assert stale["payload"]["event_type"] == "parking_wait" and stale["payload"]["reason"] == "stale_parking_sensor"
        assert stale["payload"]["sensor_age_s"] > base["mqtt"]["freshness_s"]
        still_reserved = run.request()
        assert any(s["reservation_id"] == original_reservation and s["car_id"] == "CAR001" for s in still_reserved["parking"])
        recovered = run.wait(lambda s: car(s).get("sensors", {}).get("fresh")
            and car(s)["sensors"]["sample_sim_time_s"] > held_clock and car(s).get("speed", 0) > .5)
        run.mark("S05-erzekelokieses", "A régi érzékelőkeret elavul; helyi célsebesség és tényleges sebesség nulla. Elavult központi adatból új engedély nincs; a logikai foglalás megmarad; új minta után mozgás.",
            dict(stopped_sim_time=sensor_stopped["sim_time"], sample_sim_time_s=held_clock,
                sensor_age_s=car(sensor_stopped)["control"]["sensor_age_s"],
                stale_parking_receipt_id=stale["receipt_id"], stale_parking_audit=stale["payload"],
                stale_probe_definition="Real MQTT request from an experiment client with no physical simulated car; genuine stale simulator sensors; no fabricated sensor/telemetry/SQL data",
                reservation_persisted=original_reservation, recovered_sim_time=recovered["sim_time"],
                new_sample_sim_time_s=car(recovered)["sensors"]["sample_sim_time_s"]), sensor_stopped)
        disconnect = run.control("disconnect", seconds=16)
        disconnected = run.wait(lambda s: not s.get("connected"), 5)
        recovered = run.wait(lambda s: s.get("connected") and car(s).get("speed", 0) > .5, 12)
        recovery = run.wait_receipt("events", "type", "mqtt_recovered")
        # During disconnection there can be no fresh remote snapshot. The
        # original worker's queued event is received only after reconnection.
        stops = [r for r in run.recorder.matching("events", "type", "vehicle_stopped")
            if r["payload"].get("reason") == "Az MQTT-kapcsolat megszakadt"]
        assert stops, "A kommunikációhiba alatti megállás eredeti késleltetett eseménye hiányzik"
        local_stop = stops[0]
        assert local_stop["payload"]["speed_m_s"] == 0
        assert local_stop["payload"]["sim_time"] < recovery["payload"]["sim_time"]
        run.mark("S06-kommunikacio", "Valódi szimulátor-MQTT kapcsolatmegszakítás, helyi fékezés/megállás, újrafeliratkozás és folytatás. A kiesés alatti kijelzőkép elavult, a megállási esemény később érkezik.",
            dict(control_id=disconnect["control_id"], outage_view_connected=disconnected["connected"],
                outage_view_is_stale=True, stop_receipt_id=local_stop["receipt_id"], queued_stop_event=local_stop["payload"],
                recovered_receipt_id=recovery["receipt_id"], recovery_event=recovery["payload"],
                recovered_sim_time=recovered["sim_time"]), disconnected)
        finished = run.wait(lambda s: s.get("metrics", {}).get("completed") == 6, 70)
        assert finished["metrics"]["collisions"] == 0
        assert len(finished["parking"]) == 16 and len(finished["map"]["crossings"]) == 2
        assert len([c for c in finished["cars"] if c["state"] == "parked"]) == 6
        grants = run.recorder.matching("audit", "event_type", "parking_granted")
        assert all(r["payload"]["sensor_age_s"] <= base["mqtt"]["freshness_s"] for r in grants)
        run.mark("S01-normal-parkolas", "A véges hat autós kohorsz külön MQTT koordinátorengedélyekből a 16 helyes/2 zebrás témalapi modellen beparkol, modellezett ütközés nélkül.",
            dict(metrics=finished["metrics"], completed_car_ids=[c["id"] for c in finished["cars"] if c["state"] == "parked"],
                observed_grants=len(grants), all_observed_grant_sensor_ages_within_threshold=True), finished)
        events = run.archive()
        for case in run.cases:
            types = {"S01-normal-parkolas": {"parking_granted", "parked"}, "S02-idempotencia": {"parking_grant_replayed", "command_duplicate"},
                "S04-zebra": {"pedestrian_stop", "vehicle_stopped", "pedestrian_finished"},
                "S05-erzekelokieses": {"sensor_failure", "sensor_recovered", "parking_wait", "vehicle_stopped"},
                "S06-kommunikacio": {"fault", "mqtt_recovered", "vehicle_stopped"}}[case["id"]]
            selected = [e for e in events if e["event_type"] in types]
            case["journal_record_ids"] = [e["id"] for e in selected]
            save(run.folder / (case["id"] + "-evidence.json"), dict(case=case, records=selected))
        report = dict(run_id=run.app.run_id, run_dir=run.app.run_dir.relative_to(ROOT).as_posix(),
            config_sha256=sha(run.folder / "config.json"), seed=run.seed, initial_speed=run.speed,
            cases=run.cases, final_metrics=finished["metrics"], exported_event_count=len(events))
    finally:
        recording = run.close()
    report["recording"] = recording
    return report


def full_campaign(base, output):
    run = Campaign(base, output / "full-parking", full=True)
    report = None
    try:
        run.start()
        full_wait = run.wait_receipt("audit", "reason", "no_available_slot")
        state = run.wait(lambda s: s.get("sim_time", 0) >= 50 and s.get("metrics", {}).get("offered") == 6)
        assert all(s["state"] == "occupied" and s["car_id"] is None for s in state["parking"])
        assert state["metrics"]["completed"] == 0 and state["metrics"]["buffered"] == 5
        assert car(state)["state"] == "waiting" and car(state)["speed"] == 0
        assert not run.recorder.matching("audit", "event_type", "parking_granted")
        case = run.mark("S03-megtelt-parkolo", "16 fizikailag foglalt hely mellett nincs új foglalás; a kijárati autó áll, a további öt felajánlott autó a termelési pufferben vár.",
            dict(metrics=state["metrics"], occupied_slots=16, moving_cars=0,
                denial_receipt_id=full_wait["receipt_id"], no_available_slot_audit=full_wait["payload"]), state)
        events = run.archive()
        selected = [e for e in events if e["event_type"] == "parking_wait"]
        case["journal_record_ids"] = [e["id"] for e in selected]
        save(run.folder / "S03-megtelt-parkolo-evidence.json", dict(case=case, records=selected))
        report = dict(run_id=run.app.run_id, run_dir=run.app.run_dir.relative_to(ROOT).as_posix(),
            config_sha256=sha(run.folder / "config.json"), seed=run.seed, initial_speed=run.speed,
            cases=run.cases, final_metrics=state["metrics"], exported_event_count=len(events))
    finally:
        recording = run.close()
    report["recording"] = recording
    return report


def supporting_evidence():
    """Existing reports are accepted only if all their recorded sources match."""
    result = []
    for name in ("results/operations-check-v18.json",):
        path = ROOT / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        hashes = data.get("source_sha256", {})
        mismatches = [n for n, value in hashes.items() if not (ROOT / n).is_file() or sha(ROOT / n) != value]
        accepted = data.get("status") == "PASS" and bool(hashes) and not mismatches
        result.append(dict(path=name, sha256=sha(path), accepted_as_current=accepted,
            status=data.get("status"), run_id=data.get("run_id"), executed_at_utc=data.get("executed_at_utc"),
            checks=len(data.get("checks", [])), source_mismatches=mismatches,
            scope=data.get("scope"), transport=data.get("transport")))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/course-verification-v18")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("A bizonyíték könyvtára már tartalmaz adatot; új --output útvonalat adj meg.")
    output.mkdir(parents=True, exist_ok=True)
    started, source_before = time.monotonic(), source_hashes()
    report = dict(status="RUNNING", model_version=__version__, started_at_utc=utc(),
        command="python -m experiments.course_verification", source_sha256=source_before,
        transport="Actual MQTT 3.1.1 over isolated loopback TCP broker; independent simulator/coordinator/display/recorder; public HTTP API",
        scope="Topic-sheet geometry: max_active=6, slots=16, crossings=2. Finite six-car cohort, scripted pedestrians, prolonged parking dwell and bounded faults. Named direct Coordinator tests are a separate evidence layer; no concurrent-load or physical safety claim.")
    try:
        base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        assert base["simulation"]["max_active"] == 6 and len(base["slots"]) == 16 and len(base["crossings"]) == 2
        report["reference"] = dict(path="config/default.json", sha256=sha(ROOT / "config/default.json"),
            cars=6, slots=16, crossings=2, alterations=dict(production_limit=6, parking_dwell=1000,
                automatic_pedestrians=False, mqtt_port="OS-selected free loopback port",
                full_case_initial_occupied_count=16), seed=123)
        test_command = [sys.executable, "-m", "unittest", "-v", *TEST_IDS]
        tests = subprocess.run(test_command, cwd=ROOT, text=True, encoding="utf-8", capture_output=True,
            env=dict(__import__("os").environ, PYTHONUTF8="1"), timeout=45)
        (output / "targeted-tests.txt").write_text(tests.stdout + tests.stderr, encoding="utf-8")
        assert tests.returncode == 0, tests.stdout + tests.stderr
        report["targeted_tests"] = dict(status="PASS", count=len(TEST_IDS), ids=TEST_IDS,
            layer="Direct Coordinator unit tests with explicitly supplied model clocks/messages; no broker in this layer",
            command="python -m unittest -v " + " ".join(TEST_IDS))
        print(f"Targeted tests: {len(TEST_IDS)} PASS", flush=True)
        report["core"] = core_campaign(base, output)
        report["full_parking"] = full_campaign(base, output)
        source_after = source_hashes()
        assert source_before == source_after, "A vizsgált források a kampány alatt megváltoztak"
        report.update(status="PASS", source_unchanged_during_campaign=True,
            supporting_evidence=supporting_evidence(), executed_at_utc=utc(), elapsed_host_s=time.monotonic()-started)
        report["artifact_sha256"] = {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}
        save(output / "summary.json", report)
        print(json.dumps(dict(status="PASS", scenarios=6, targeted_tests=len(TEST_IDS),
            core_run=report["core"]["run_id"], full_run=report["full_parking"]["run_id"], output=str(output)), ensure_ascii=False), flush=True)
    except BaseException as error:
        report.update(status="FAIL", failed_at_utc=utc(), error=str(error), traceback=traceback.format_exc())
        save(output / "failure.json", report)
        raise


if __name__ == "__main__":
    main()
