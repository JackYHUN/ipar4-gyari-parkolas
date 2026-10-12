"""Incident confirmation, independent acknowledgment, and exact saved evidence."""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock, patch
import zipfile

from factory_yard.journal import Journal
from factory_yard.operations import OperationsMonitor, OperationsConflict
from factory_yard.server import Application, ROOT, create_http_server
from factory_yard.transport_monitor import TransportMonitor


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run-journal.sqlite3"
        self.journal = Journal(self.path, "run")
        self.monitor, self.transport = OperationsMonitor(), TransportMonitor()

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def sample(self, second, *, callback=.01, processed=.01, broker=True, fault=False,
               cars=(), snapshot=True, workers=(), sim_time=1, snapshot_received=None):
        now = int(second * 1e9)
        point = self.transport.observe(now, int(second), int(second), 0, processed)
        sample_id = self.journal.record_transport(point, sim_time=sim_time)
        saved = self.journal.transport_observation(sample_id)
        accepted = self.monitor.observe(self.journal, now_ns=now, sample=saved,
            transport=dict(broker_connected=broker, communication_fault_active=fault,
                           last_snapshot_age_s=callback, snapshot_received=snapshot_received),
            snapshot=dict(sim_time=sim_time, cars=list(cars)) if snapshot else {},
            processed_age_s=processed, workers=workers)
        return saved, accepted

    @staticmethod
    def car(fresh=True, *, sampled=True, fault=False, state="driving"):
        return dict(id="CAR001", state=state, sensors=dict(sampled=sampled, fresh=fresh,
            fault_active=fault, age_s=.1))

    def items(self, **filters):
        return self.journal.operations(**filters)["items"]

    def open_sensor(self):
        first, _ = self.sample(0, cars=[self.car(False)])
        last, _ = self.sample(1, cars=[self.car(False)])
        return self.items()[0], first, last

    def test_first_bad_sample_is_evidence_second_opens_and_duplicate_does_not_confirm(self):
        first, _ = self.sample(0, cars=[self.car(False)])
        self.assertEqual(self.items(), [])
        self.assertFalse(self.monitor.observe(self.journal, now_ns=0, sample=first,
            transport={}, snapshot={}, processed_age_s=0))
        self.assertEqual(self.items(), [])
        last, _ = self.sample(1, cars=[self.car(False)])
        item = self.items()[0]
        self.assertEqual(item["opening"]["transport_sample_id"], first["id"])
        self.assertEqual(item["latest"]["transport_sample_id"], last["id"])
        self.assertEqual(item["opened_utc"], last["utc"])
        self.assertEqual(self.journal.query(category="operations")["total"], 1)

    def test_unknown_unsampled_sensor_never_opens(self):
        for second in range(8):
            self.sample(second, cars=[self.car(False, sampled=False)])
        self.assertEqual(self.items(), [])
        self.assertEqual(self.monitor.summary(7_000_000_000)["quality"]["sensor_unavailable"], "unknown")

    def test_stale_snapshot_preserves_active_sensor_and_breaks_recovery_streak(self):
        item, _, _ = self.open_sensor()
        self.sample(2, cars=[self.car(True)])
        self.sample(3, cars=[self.car(True)], processed=1)
        self.sample(4, cars=[self.car(True)])
        self.assertTrue(self.items()[0]["active"])
        self.sample(5, cars=[self.car(True)])
        resolved = self.items()[0]
        self.assertFalse(resolved["active"])
        self.assertFalse(resolved["acknowledged"])
        self.assertEqual(resolved["revision"], item["revision"] + 1)

    def test_fresh_parked_car_resolves_sensor_without_fabricating_recovery(self):
        self.open_sensor()
        for second in (2, 3):
            self.sample(second, cars=[self.car(False, state="parked")])
        self.assertFalse(self.items()[0]["active"])
        self.assertEqual(self.items()[0]["measurements"]["state"], "parked")

    def test_paused_model_clock_does_not_age_fresh_received_observations(self):
        for second in range(9):
            self.sample(second, cars=[self.car(True)], sim_time=5)
        self.assertEqual(self.items(), [])
        self.assertEqual(self.monitor.summary(8_000_000_000)["known_rules"], 4)

    def test_missing_car_does_not_resolve_active_incident(self):
        self.open_sensor()
        for second in (2, 3, 4):
            self.sample(second, cars=[])
        self.assertTrue(self.items()[0]["active"])

    def test_startup_grace_requires_two_fault_observations_after_grace(self):
        for second in range(6):
            self.sample(second, broker=False, callback=None, processed=None, snapshot=False)
        self.assertEqual(self.items(), [])
        self.sample(6, broker=False, callback=None, processed=None, snapshot=False)
        self.assertEqual([item["rule"] for item in self.items()], ["communication_unavailable"])
        self.assertEqual(self.items()[0]["opening"]["transport_sample_id"], 6)

    def test_processing_lag_distinguishes_fresh_callback_from_communication_loss(self):
        for second in range(7):
            self.sample(second, callback=.01, processed=3)
        self.assertEqual([item["rule"] for item in self.items()], ["processing_lag"])
        self.assertEqual(self.items()[0]["measurements"]["callback_snapshot_age_s"], .01)

    def test_no_first_snapshot_after_grace_is_fault_but_unknown_age_is_not(self):
        for second in range(7):
            self.sample(second, callback=None, processed=None, snapshot=False, snapshot_received=False)
        self.assertEqual([item["rule"] for item in self.items()], ["communication_unavailable"])
        for second in (7, 8, 9):
            self.sample(second, callback=None, processed=None, snapshot=False, snapshot_received=True)
        self.assertTrue(self.items()[0]["active"])
        self.assertEqual(self.monitor.summary(9_000_000_000)["quality"]["communication_unavailable"], "unknown")

    def test_new_run_does_not_inherit_acknowledged_episode_or_confirmation(self):
        item, _, _ = self.open_sensor()
        ack, _ = self.journal.acknowledge_operation(item["id"], 1)
        self.monitor.acknowledged(ack)
        new = Journal(Path(self.tmp.name) / "new-journal.sqlite3", "new")
        try:
            point = TransportMonitor().observe(10_000_000_000, 0, 0, 0)
            sample_id = new.record_transport(point)
            self.monitor.observe(new, now_ns=10_000_000_000, sample=new.transport_observation(sample_id),
                transport=dict(broker_connected=True, communication_fault_active=False, last_snapshot_age_s=0),
                snapshot=dict(cars=[self.car(False)]), processed_age_s=0)
            self.assertEqual(new.operations()["items"], [])
            self.assertTrue(self.journal.operations()["items"][0]["acknowledged"])
        finally:
            new.close()

    def test_communication_loss_is_not_processing_lag(self):
        for second in range(7):
            self.sample(second, callback=4, processed=4)
        self.assertEqual([item["rule"] for item in self.items()], ["communication_unavailable"])

    def test_long_observation_gap_breaks_confirmation_streak(self):
        self.sample(0, cars=[self.car(False)])
        self.sample(4, cars=[self.car(False)])
        self.assertEqual(self.items(), [])
        self.sample(5, cars=[self.car(False)])
        self.assertEqual(self.items()[0]["opening"]["transport_sample_id"], 2)

    def test_clock_regression_and_new_transport_segment_break_streak(self):
        self.sample(10, cars=[self.car(False)])
        self.sample(9, cars=[self.car(False)])
        self.assertEqual(self.items(), [])
        self.sample(10, cars=[self.car(False)])
        self.assertEqual(self.items()[0]["opening"]["transport_sample_id"], 2)

    def test_worker_stop_is_immediate_and_latched(self):
        self.sample(0, workers=[dict(pid=12, exit_code=1)])
        self.assertEqual(self.items()[0]["rule"], "worker_stopped")
        for second in range(1, 8):
            self.sample(second, workers=[])
        self.assertTrue(self.items()[0]["active"])

    def test_acknowledgment_remains_active_and_idempotent_without_repeat_audit(self):
        item, _, _ = self.open_sensor()
        ack, duplicate = self.journal.acknowledge_operation(item["id"], 1, " ellenőrizve ")
        self.monitor.acknowledged(ack)
        self.assertFalse(duplicate)
        self.assertTrue(ack["active"])
        self.assertEqual(ack["note"], "ellenőrizve")
        self.sample(2, cars=[self.car(False)])
        self.assertEqual(self.items()[0]["revision"], 2)
        again, duplicate = self.journal.acknowledge_operation(item["id"], 1, "different note")
        self.assertTrue(duplicate)
        self.assertEqual(again["note"], "ellenőrizve")
        self.assertEqual(self.journal.query(category="operations")["total"], 2)

    def test_stale_revision_rejected_when_fault_resolved_before_ack(self):
        item, _, _ = self.open_sensor()
        self.sample(2, cars=[self.car(True)])
        self.sample(3, cars=[self.car(True)])
        with self.assertRaises(OperationsConflict):
            self.journal.acknowledge_operation(item["id"], 1)
        self.assertFalse(self.items()[0]["acknowledged"])

    def test_recurrence_opens_new_unacknowledged_episode(self):
        item, _, _ = self.open_sensor()
        ack, _ = self.journal.acknowledge_operation(item["id"], 1)
        self.monitor.acknowledged(ack)
        for second in (2, 3):
            self.sample(second, cars=[self.car(True)])
        for second in (4, 5):
            self.sample(second, cars=[self.car(False)])
        recent, old = self.items()
        self.assertNotEqual(recent["id"], old["id"])
        self.assertFalse(recent["acknowledged"])
        self.assertFalse(old["active"])
        self.assertTrue(old["acknowledged"])

    def test_opening_prefix_excludes_lifecycle_record_and_later_received_data(self):
        self.journal.ingest("telemetry/CAR001", dict(car_id="CAR001", sim_time=1, x=1, y=2, state="driving"), 1, 0)
        event = self.journal.record("fault", "sensor_fault", "test")
        item, _, _ = self.open_sensor()
        self.journal.ingest("telemetry/CAR_LATER", dict(car_id="CAR_LATER", sim_time=0, x=4, y=2, state="driving"), 1, 0)
        context = self.journal.transport_context(item["opening"]["transport_sample_id"])
        self.assertEqual(context["events_until_id"], event)
        self.assertEqual(item["opening"]["events_until_id"], event)
        self.assertEqual([car["car_id"] for car in context["vehicles"]["items"]], ["CAR001"])
        self.assertFalse(any(e["event_type"] == "incident_opened" for e in context["events"]["items"]))

    def test_durable_reopen_and_new_monitor_preserve_active_acknowledged_item(self):
        item, _, _ = self.open_sensor()
        ack, _ = self.journal.acknowledge_operation(item["id"], 1, "saved")
        self.journal.close()
        self.journal = Journal(self.path, "run")
        self.monitor = OperationsMonitor()
        self.sample(2, cars=[self.car(False)])
        self.assertTrue(self.items()[0]["acknowledged"])
        self.assertEqual(self.items()[0]["revision"], ack["revision"])
        reader = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            self.assertTrue(reader.operations()["items"][0]["acknowledged"])
        finally:
            reader.close()

    def test_bounded_pages_and_until_id_freeze_membership(self):
        for number in range(4):
            self.sample(number, workers=[dict(pid=number + 1, exit_code=1)])
        first = self.journal.operations(limit=2)
        self.sample(4, workers=[dict(pid=99, exit_code=1)])
        last = self.journal.operations(limit=2, before=first["next_before"], until_id=first["until_id"])
        self.assertEqual([item["id"] for item in first["items"] + last["items"]], [4, 3, 2, 1])
        self.assertEqual(last["summary"]["total"], 5)
        self.assertEqual(last["summary"]["prefix_count"], 4)
        self.assertIsNone(last["next_before"])

    def test_strict_ack_ids_revision_and_note(self):
        item, _, _ = self.open_sensor()
        for value in (None, True, 0, -1, 1.5, "1.0", " 1", "١", "9007199254740992"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.journal.acknowledge_operation(value, 1)
        for revision in (None, True, 0, 1.5):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.journal.acknowledge_operation(item["id"], revision)
        for note in (None, {}, "x" * 501):
            with self.subTest(note=note), self.assertRaises(ValueError):
                self.journal.acknowledge_operation(item["id"], 1, note)

    def test_readonly_legacy_is_explicit_unavailable_without_migration(self):
        self.journal.close()
        connection = sqlite3.connect(self.path)
        connection.execute("DROP TABLE operations_incidents")
        connection.commit()
        connection.close()
        reader = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            result = reader.operations()
            self.assertFalse(result["available"])
            self.assertEqual(result["items"], [])
            self.assertFalse(reader._connection.execute("SELECT 1 FROM sqlite_master WHERE name='operations_incidents'").fetchone())
        finally:
            reader.close()

    def test_export_contains_all_incidents_and_saved_coverage(self):
        self.open_sensor()
        document = json.loads(b"".join(self.journal.iter_operations_export()))
        self.assertEqual(document["items"], self.items())
        self.assertEqual(document["summary"]["quality"]["sensor_unavailable"], "fault")
        self.assertIsNone(document["summary"]["observation_fresh"])


class OperationsHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "api-operations"
        self.app.journal = Journal(self.app.run_dir / "api-operations-journal.sqlite3", self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=config)
        (self.app.run_dir / "api-operations-metadata.json").write_text(json.dumps(self.app.metadata), encoding="utf-8")
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(sim_time=1, cars=[OperationsTests.car(False)], metrics={}, warnings=[])
        self.app.last_snapshot_ns = 0
        for second in (0, 1):
            self.app.last_snapshot_ns = int(second * 1e9)
            self.app._sample_transport(int(second * 1e9), dict(broker_connected=True,
                communication_fault_active=False, last_snapshot_age_s=0, callback_received_messages=second))
        self.item = self.app.journal.operations()["items"][0]
        self.server = create_http_server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)
        self.app.journal.close()
        self.tmp.cleanup()

    def get(self, path):
        with urlopen(self.url + path) as response:
            return json.load(response)

    def post(self, data, *, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        with urlopen(Request(self.url + "/api/operations/acknowledge", json.dumps(data).encode(), headers)) as response:
            return json.load(response)

    def test_ack_is_bookkeeping_without_mqtt_publish_and_repeat_is_idempotent(self):
        data = dict(run_id=self.app.run_id, id=self.item["id"], expected_revision=1, note="checked")
        first = self.post(data)
        again = self.post(data)
        self.assertTrue(first["item"]["active"])
        self.assertTrue(again["already_acknowledged"])
        self.app.bus.publish.assert_not_called()

    def test_http_requires_explicit_run_and_valid_filter_cursors(self):
        for path in ("/api/operations", "/api/operations?run_id=missing",
                     "/api/operations?run_id=api-operations&before=99",
                     "/api/operations?run_id=api-operations&status=bad",
                     "/api/operations?run_id=api-operations&limit=0"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 400)

    def test_stale_run_revision_and_origin_rejected(self):
        base = dict(run_id=self.app.run_id, id=self.item["id"], expected_revision=1)
        for data, code in ((dict(base, run_id="old-run"), 409), (dict(base, expected_revision=99), 409),
                           (dict(base, id=True), 400), (dict(base, unexpected=True), 400)):
            with self.subTest(data=data), self.assertRaises(HTTPError) as error:
                self.post(data)
            self.assertEqual(error.exception.code, code)
        with self.assertRaises(HTTPError) as error:
            self.post(base, origin="https://outside.invalid")
        self.assertEqual(error.exception.code, 403)

    def test_state_summary_is_compact_and_archived_live_flag_follows_run(self):
        with patch("factory_yard.server.time.perf_counter_ns", return_value=1_000_000_000):
            state = self.app.state()
        self.assertNotIn("items", state["operations"])
        self.assertEqual(state["operations"]["summary"]["active"], 1)
        result = self.app.operations(self.app.run_id)
        self.assertTrue(result["current_live"])
        self.app.run_id = "new-run"
        archived = self.app.operations("api-operations")
        self.assertFalse(archived["current_live"])
        self.assertIsNone(archived["summary"]["observation_fresh"])

    def test_first_fault_candidate_does_not_show_green_health_before_confirmation(self):
        # Resolve the setup incident and acknowledge its retained episode.
        for second in (2, 3, 4, 5):
            self.app.latest["cars"] = [OperationsTests.car(True)]
            self.app.last_snapshot_ns = second * 1_000_000_000
            self.app._sample_transport(second * 1_000_000_000, dict(broker_connected=True,
                communication_fault_active=False, last_snapshot_age_s=0, callback_received_messages=second))
        old = self.app.journal.operations()["items"][0]
        self.app.acknowledge_operation(dict(run_id=self.app.run_id, id=old["id"], expected_revision=old["revision"]))
        self.assertEqual(self.app._operations_summary(5_000_000_000)["summary"]["health"], "healthy")
        self.app.latest["cars"] = [OperationsTests.car(False)]
        self.app.last_snapshot_ns = 6_000_000_000
        self.app._sample_transport(6_000_000_000, dict(broker_connected=True,
            communication_fault_active=False, last_snapshot_age_s=0, callback_received_messages=6))
        summary = self.app._operations_summary(6_000_000_000)["summary"]
        self.assertEqual(summary["active"], 0)
        self.assertEqual(summary["health"], "unknown")
        self.assertEqual(summary["quality"]["sensor_unavailable"], "fault")

    def test_run_zip_includes_complete_incident_catalog_same_snapshot(self):
        with patch("factory_yard.server.time.perf_counter_ns", return_value=1_000_000_000):
            raw = self.app.export("zip")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            document = json.loads(archive.read("incidents.json"))
            metadata = json.loads(archive.read("metadata.json"))
            self.assertEqual(document["items"][0]["id"], self.item["id"])
            self.assertEqual(metadata["export_snapshot"]["operations_until_id"], self.item["id"])
            self.assertEqual(metadata["export_snapshot"]["operations_member"], "incidents.json")


if __name__ == "__main__":
    unittest.main()
