"""Saved diagnostic samples correlate only already received, bounded evidence."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import Mock, patch

from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT, create_http_server
from factory_yard.transport_monitor import TransportMonitor


class TransportContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run-journal.sqlite3"
        self.journal = Journal(self.path, "run", max_telemetry=2)
        self.monitor = TransportMonitor()

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def pose(self, car, time, **fields):
        return self.journal.ingest("telemetry/" + car, dict(car_id=car, sim_time=time,
            x=time, y=2, speed=0, state="driving", seq=int(time), **fields), 100, .1)

    def sample(self, second=0):
        return self.journal.record_transport(self.monitor.observe(second * 1_000_000_000, second, second, 0), sim_time=second)

    def test_exact_sample_freezes_latest_each_car_and_global_events(self):
        self.pose("CAR_B", 1)
        id_a = self.pose("CAR_A", 2)
        id_b = self.pose("CAR_B", 3, heading=1.5, slot_id="P1", position_source="post_step_simulation_reference")
        event = self.journal.record("decision", "crossing", "Old event", car_id="CAR_A", sim_time=2)
        sample = self.sample()
        frozen = self.journal.transport_context(sample)
        self.pose("CAR_A", 100, slot_id="P9")
        self.pose("CAR_FUTURE", 101)
        self.journal.record("decision", "future", "Future event", car_id="CAR_A", sim_time=100)
        self.sample(1)
        self.assertEqual(self.journal.transport_context(sample), frozen)
        self.assertEqual(frozen["telemetry_until_id"], id_b)
        self.assertEqual(frozen["events_until_id"], event)
        self.assertEqual(frozen["sample"], self.journal.transport_history(until_id=sample)["points"][0])
        cars = frozen["vehicles"]["items"]
        self.assertEqual([(c["car_id"], c["id"]) for c in cars], [("CAR_A", id_a), ("CAR_B", id_b)])
        self.assertEqual(cars[1]["heading"], 1.5)
        self.assertEqual(cars[1]["position_source"], "post_step_simulation_reference")
        self.assertIsNone(cars[0]["position_source"])
        self.assertEqual(frozen["events"]["items"][0]["message"], "Old event")
        self.assertEqual(frozen["scope"], "received_evidence_at_transport_sample")

    def test_empty_zero_receipt_prefix_never_fills_from_later_data(self):
        sample = self.sample()
        self.pose("CAR_A", 5)
        self.journal.record("state", "later", "later")
        context = self.journal.transport_context(sample)
        self.assertEqual((context["telemetry_until_id"], context["events_until_id"]), (0, 0))
        self.assertEqual(context["vehicles"]["total_count"], 0)
        self.assertEqual(context["vehicles"]["items"], [])
        self.assertEqual(context["events"]["total"], 0)
        self.assertEqual(context["events"]["items"], [])
        self.assertIsNone(context["vehicles"]["next_after"])
        self.assertIsNone(context["events"]["next_before"])

    def test_car_pages_are_exclusive_lexicographic_and_prefix_frozen(self):
        for index, car in enumerate(("CAR_Z", "CAR_B", "CAR_A", "CAR_D"), 1):
            self.pose(car, index)
        sample = self.sample()
        first = self.journal.transport_context(sample, cars_limit=2)
        self.assertEqual([item["car_id"] for item in first["vehicles"]["items"]], ["CAR_A", "CAR_B"])
        self.assertEqual(first["vehicles"]["next_after"], "CAR_B")
        self.pose("CAR_C", 20)
        second = self.journal.transport_context(sample, cars_limit=2, cars_after="CAR_B")
        self.assertEqual([item["car_id"] for item in second["vehicles"]["items"]], ["CAR_D", "CAR_Z"])
        self.assertEqual(second["vehicles"]["total_count"], 4)
        self.assertFalse(second["vehicles"]["has_more"])
        self.assertIsNone(second["vehicles"]["next_after"])
        self.assertEqual(self.journal.transport_context(sample, cars_after="CAR_Z")["vehicles"]["items"], [])

    def test_event_pages_use_descending_ids_and_exact_fixed_prefix(self):
        ids = [self.journal.record("state", "event", str(index), details={"index": index}) for index in range(5)]
        sample = self.sample()
        first = self.journal.transport_context(sample, events_limit=2)
        self.assertEqual([item["id"] for item in first["events"]["items"]], [ids[4], ids[3]])
        self.journal.record("state", "future", "future")
        second = self.journal.transport_context(sample, events_limit=2, events_before=first["events"]["next_before"])
        last = self.journal.transport_context(sample, events_limit=2, events_before=second["events"]["next_before"])
        self.assertEqual([item["id"] for item in second["events"]["items"]], [ids[2], ids[1]])
        self.assertEqual([item["id"] for item in last["events"]["items"]], [ids[0]])
        self.assertEqual(last["events"]["total"], 5)
        self.assertIsNone(last["events"]["next_before"])
        self.assertEqual(last["events"]["items"][0]["details"], {"index": 0})

    def test_telemetry_rotation_never_loses_compact_context(self):
        old = self.pose("CAR_OLD", 1)
        sample = self.sample()
        for index in range(2, 8):
            self.pose("CAR_NEW", index)
        self.assertIsNone(self.journal._connection.execute("SELECT 1 FROM telemetry WHERE id=?", (old,)).fetchone())
        context = self.journal.transport_context(sample)
        self.assertEqual(context["vehicles"]["items"][0]["id"], old)
        self.assertTrue(context["vehicles"]["complete_received_archive"])
        self.assertEqual(context["vehicles"]["source"], "trip_samples")

    def test_context_does_not_decompress_sensors_or_expose_large_bundles(self):
        self.pose("CAR_A", 1, sensors={"sampled": True, "fresh": True, "points": [1] * 1000}, control={"decision": "go"})
        sample = self.sample()
        with patch("factory_yard.journal.zlib.decompress", side_effect=AssertionError("No sensor expansion")):
            vehicle = self.journal.transport_context(sample)["vehicles"]["items"][0]
        self.assertNotIn("details", vehicle)
        self.assertNotIn("sensors", vehicle)
        self.assertNotIn("control", vehicle)

    def test_received_simulation_time_is_not_a_join_boundary(self):
        self.pose("CAR_A", 100)
        self.journal.record("state", "received", "Earlier receipt, later model timestamp", sim_time=200)
        sample = self.sample()
        self.pose("CAR_B", 0)
        self.journal.record("state", "future", "Later receipt, earlier model timestamp", sim_time=0)
        context = self.journal.transport_context(sample)
        self.assertEqual(context["sample"]["sim_time"], 0)
        self.assertEqual(context["vehicles"]["items"][0]["sim_time"], 100)
        self.assertEqual(context["events"]["items"][0]["sim_time"], 200)
        self.assertEqual(context["vehicles"]["total_count"], 1)
        self.assertEqual(context["events"]["total"], 1)

    def test_strict_integer_identifiers_and_limits(self):
        sample = self.sample()
        for key in ("sample_id", "cars_limit", "events_limit", "events_before"):
            for value in (True, -1, 1.5, "1.0", "NaN", " 1", "9007199254740992", "١", 0):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    kwargs = {"sample_id": sample, key: value}
                    self.journal.transport_context(**kwargs)
        for key in ("sample_id", "cars_limit", "events_limit"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.journal.transport_context(**{"sample_id": sample, key: None})

    def test_unknown_future_or_malformed_page_cursors_reject(self):
        self.pose("CAR_A", 1)
        event = self.journal.record("state", "past", "past")
        sample = self.sample()
        self.pose("CAR_FUTURE", 2)
        future = self.journal.record("state", "future", "future")
        for cursor in ("missing", "CAR_FUTURE", "../CAR_A", "A" * 65, None, False, 1):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                self.journal.transport_context(sample, cars_after=cursor)
        for cursor in (future, future + 1):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                self.journal.transport_context(sample, events_before=cursor)
        self.assertEqual(self.journal.transport_context(sample, events_before=event)["events"]["items"], [])
        with self.assertRaises(ValueError):
            self.journal.transport_context(sample + 1)

    def test_page_sizes_cap_at_one_hundred(self):
        for index in range(105):
            self.pose(f"CAR_{index:03}", index)
            self.journal.record("state", "event", str(index))
        sample = self.sample()
        context = self.journal.transport_context(sample, cars_limit=1000, events_limit=1000)
        self.assertEqual(len(context["vehicles"]["items"]), 100)
        self.assertEqual(len(context["events"]["items"]), 100)
        self.assertTrue(context["vehicles"]["has_more"])
        self.assertIsNotNone(context["events"]["next_before"])

    def test_readonly_reopen_does_not_mutate_archive(self):
        self.pose("CAR_A", 1)
        sample = self.sample()
        self.journal.close()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            context = reader.transport_context(sample)
            self.assertEqual(context["vehicles"]["items"][0]["car_id"], "CAR_A")
        finally:
            reader.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_legacy_missing_transport_rejects_without_migrating(self):
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE transport_samples")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            with self.assertRaises(ValueError):
                reader.transport_context(1)
        finally:
            reader.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_legacy_retained_telemetry_discloses_missing_fields_and_history(self):
        self.pose("CAR_OLD", 1)
        self.pose("CAR_A", 2, heading=2, slot_id="P1", position_source="reported")
        sample = self.sample()
        self.pose("CAR_NEW", 3)
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TABLE trip_samples")
            db.execute("DELETE FROM metadata WHERE key='compact_history_prior_missing'")
        reader = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            context = reader.transport_context(sample)
            self.assertEqual(context["vehicles"]["source"], "legacy_retained_telemetry")
            self.assertFalse(context["vehicles"]["complete_received_archive"])
            self.assertEqual(context["vehicles"]["prior_missing_samples_global"], 1)
            self.assertEqual(context["vehicles"]["total_count"], 1)
            vehicle = context["vehicles"]["items"][0]
            self.assertEqual(vehicle["position_source"], "reported")
            for field in ("heading", "slot_id", "wait_reason", "seq", "gap_before", "time_gap_before_s"):
                self.assertIsNone(vehicle[field])
        finally:
            reader.close()

    def test_compact_migration_prior_missing_still_disclosed(self):
        self.pose("CAR_A", 1)
        sample = self.sample()
        self.journal._history_prior_missing = 7
        context = self.journal.transport_context(sample)
        self.assertFalse(context["vehicles"]["complete_received_archive"])
        self.assertEqual(context["vehicles"]["prior_missing_samples_global"], 7)

    def test_invalid_saved_diagnostic_is_rejected(self):
        sample = self.sample()
        self.journal._connection.execute("UPDATE transport_samples SET telemetry_until_id=-1 WHERE id=?", (sample,))
        with self.assertRaises(ValueError):
            self.journal.transport_context(sample)


class TransportContextHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(self.config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "api-context"
        self.app.journal = Journal(self.app.run_dir / "api-context-journal.sqlite3", self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=self.config)
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(sim_time=0, cars=[], metrics={}, warnings=[])
        self.app.journal.ingest("telemetry/CAR_A", dict(car_id="CAR_A", sim_time=1, x=1, y=2, state="driving"), 100, .1)
        self.app.journal.record("state", "event", "Saved event", car_id="CAR_A")
        self.app.journal.record_transport(TransportMonitor().observe(0, 1, 1, 0), sim_time=0)
        self.server = create_http_server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)
        self.app.close()
        self.tmp.cleanup()

    def get(self, suffix):
        with urlopen(self.url + suffix, timeout=5) as response:
            return json.loads(response.read())

    def test_http_identity_map_and_prefix_are_saved(self):
        payload = self.get("/api/transport/context?run_id=api-context&sample_id=1&cars_limit=1&events_limit=1")
        self.assertEqual(payload["run_id"], "api-context")
        self.assertEqual(payload["telemetry_until_id"], 1)
        self.assertEqual(payload["events_until_id"], 1)
        self.assertEqual(payload["vehicles"]["items"][0]["car_id"], "CAR_A")
        self.assertEqual(payload["events"]["items"][0]["message"], "Saved event")
        self.assertEqual(payload["config"], self.config)
        self.assertEqual(payload["map"], Application._history_map(self.config))

    def test_archived_http_context_survives_active_run_replacement(self):
        suffix = "/api/transport/context?run_id=api-context&sample_id=1"
        original = self.get(suffix)
        path = self.app.run_dir / "api-context-metadata.json"
        path.write_text(json.dumps(self.app.metadata), encoding="utf-8")
        self.app.journal.close()
        self.app.run_id = "new-current"
        self.app.journal = Journal(self.app.run_dir / "new-current-journal.sqlite3", self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config={"world": {"width": 999}})
        self.app.journal.ingest("telemetry/CAR_A", dict(car_id="CAR_A", sim_time=99, x=99, y=99), 100, .1)
        self.app.journal.record_transport(TransportMonitor().observe(0, 1, 1, 0))
        self.assertEqual(self.get(suffix), original)
        current = self.get("/api/transport/context?sample_id=1")
        self.assertEqual(current["run_id"], "new-current")
        self.assertEqual(current["vehicles"]["items"][0]["x"], 99)
        self.assertEqual(current["map"]["width"], 999)

    def test_http_invalid_inputs_are_400(self):
        for suffix in ("?sample_id=0", "?sample_id=2", "?sample_id=1.5", "?sample_id=NaN",
                       "?sample_id=1&cars_limit=0", "?sample_id=1&events_limit=-1", "?sample_id=1&cars_after=missing",
                       "?sample_id=1&cars_after=../escape", "?sample_id=1&events_before=2", "?sample_id=1&events_before=0",
                       "?sample_id=1&run_id=missing", "?sample_id=1&run_id=../escape", ""):
            with self.subTest(suffix=suffix), self.assertRaises(HTTPError) as error:
                self.get("/api/transport/context" + suffix)
            self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
