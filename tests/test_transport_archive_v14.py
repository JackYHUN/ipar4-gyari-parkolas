"""Durable host-clock diagnostics, frozen prefixes and legacy evidence boundaries."""
import csv
from contextlib import closing
import hashlib
import io
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


class TransportArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run-journal.sqlite3"
        self.journal = Journal(self.path, "run")
        self.monitor = TransportMonitor()

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def sample(self, second, *, received=None, dequeued=None, queued=0, sim_time=0):
        point = self.monitor.observe(int(second * 1e9), int(second) if received is None else received,
            int(second) if dequeued is None else dequeued, queued)
        return self.journal.record_transport(point, sim_time=sim_time)

    def test_monitor_returns_only_new_public_independent_points(self):
        point = self.monitor.observe(0, 0, 0, 0)
        self.assertEqual(point["segment"], 0)
        self.assertNotIn("clock_ns", point)
        point["queue_messages"] = 99
        self.assertEqual(self.monitor.snapshot(0)["history"][0]["queue_messages"], 0)
        self.assertIsNone(self.monitor.observe(500_000_000, 20, 20, 0))
        self.assertIsNone(self.monitor.observe(None, 20, 20, 0))

    def test_counter_clock_resets_preserve_distinct_segments_and_new_run_zero(self):
        self.sample(10, received=20, dequeued=20)
        self.sample(11, received=1, dequeued=1)
        self.sample(9, received=2, dequeued=2)
        points = self.journal.transport_history()["points"]
        self.assertEqual([p["segment"] for p in points], [0, 1, 2])
        self.assertEqual([p["elapsed_s"] for p in points], [0, 0, 0])
        self.assertTrue(all(p["arrival_rate_hz"] is None for p in points))
        self.monitor.reset()
        self.assertEqual(self.monitor.observe(12_000_000_000, 0, 0, 0)["segment"], 0)

    def test_every_sample_survives_memory_limit_and_readonly_reopen(self):
        for second in range(145):
            self.sample(second)
        self.assertEqual(len(self.monitor.snapshot(144_000_000_000)["history"]), 120)
        self.journal.close()
        with_read = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            history = with_read.transport_history(limit=500)
            self.assertEqual(history["total_count"], 145)
            self.assertEqual(history["points"][0]["elapsed_s"], 0)
            self.assertEqual(history["points"][-1]["elapsed_s"], 144)
        finally:
            with_read.close()

    def test_frozen_pagination_excludes_later_samples_with_no_duplicates(self):
        for second in range(8):
            self.sample(second)
        page = self.journal.transport_history(limit=3)
        original = page["summary"]
        self.sample(8)
        rows = list(page["points"])
        while page["has_more"]:
            page = self.journal.transport_history(after=page["next_after"], limit=3, until_id=8)
            self.assertEqual(page["summary"], original)
            rows.extend(page["points"])
        self.assertEqual([p["id"] for p in rows], list(range(1, 9)))
        self.assertIsNone(page["next_after"])
        self.assertEqual(self.journal.transport_history()["total_count"], 9)

    def test_zero_rates_zero_queue_and_null_measurements_remain_distinct(self):
        self.monitor.observe(0, 0, 0, 0)
        first = self.monitor.snapshot(0)["history"][0]
        self.journal.record_transport(first)
        self.journal.record_transport(self.monitor.observe(1_000_000_000, 0, 0, 0))
        self.journal.record_transport(self.monitor.observe(2_000_000_000, None, None, None))
        history = self.journal.transport_history()
        points = history["points"]
        self.assertIsNone(points[0]["arrival_rate_hz"])
        self.assertEqual(points[1]["arrival_rate_hz"], 0)
        self.assertEqual(points[1]["queue_messages"], 0)
        self.assertIsNone(points[2]["queue_messages"])
        self.assertEqual(history["summary"]["known_queue_count"], 2)
        self.assertEqual(history["summary"]["peak_queue_messages"], 0)
        self.assertEqual(history["summary"]["peak_arrival_rate_hz"], 0)

    def test_recorded_receipt_boundaries_pin_already_processed_evidence(self):
        self.journal.ingest("telemetry/CAR001", dict(car_id="CAR001", sim_time=1, seq=1,
            x=1, y=2, state="driving"), 100, .1)
        event_id = self.journal.record("decision", "go", "go", sim_time=1)
        self.sample(0, sim_time=1)
        self.journal.ingest("telemetry/CAR001", dict(car_id="CAR001", sim_time=2, seq=2,
            x=2, y=2, state="driving"), 101, .1)
        self.journal.record("decision", "go", "go", sim_time=2)
        point = self.journal.transport_history()["points"][0]
        self.assertEqual(point["telemetry_until_id"], 1)
        self.assertEqual(point["events_until_id"], event_id)
        self.assertEqual(point["sim_time"], 1)

    def test_snapshot_time_preserves_known_zero_but_never_coerces_missing_or_invalid_input(self):
        values = (0, 1.5, True, False, "0", "1.5", None, float("nan"), float("inf"), -1)
        for second, value in enumerate(values):
            self.sample(second, sim_time=value)
        times = [point["sim_time"] for point in self.journal.transport_history(limit=500)["points"]]
        self.assertEqual(times, [0, 1.5, *([None] * 8)])

    def test_empty_archive_is_available_but_missing_legacy_table_not_recorded(self):
        empty = self.journal.transport_history()
        self.assertTrue(empty["available"])
        self.assertEqual(empty["availability"], "empty")
        self.assertIsNone(empty["summary"]["peak_queue_messages"])
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute("DROP TABLE transport_samples")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            history = reader.transport_history()
            self.assertFalse(history["available"])
            self.assertEqual(history["availability"], "not_recorded")
            self.assertEqual(history["points"], [])
            self.assertEqual(history["until_id"], 0)
            self.assertEqual(json.loads(b"".join(reader.iter_transport_export("json")))["availability"], "not_recorded")
        finally:
            reader.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='transport_samples'").fetchone())

    def test_wrong_readonly_run_rejected(self):
        self.journal.flush()
        with self.assertRaises(ValueError):
            Journal.open_readonly(self.path, "wrong-run")

    def test_strict_cursors_and_page_limits_reject_ambiguous_inputs(self):
        self.sample(0)
        for key in ("after", "until_id", "limit"):
            for value in (True, -1, 1.5, "1.0", "NaN", "-1", " 1", "9007199254740992"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.journal.transport_history(**{key: value})
        for filters in (dict(after=2), dict(until_id=2), dict(limit=0)):
            with self.assertRaises(ValueError):
                self.journal.transport_history(**filters)
        self.assertEqual(self.journal.transport_history(limit=900)["total_count"], 1)

    def test_zero_ceiling_freezes_an_empty_prefix(self):
        self.sample(0)
        result = self.journal.transport_history(until_id=0)
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["points"], [])
        self.assertFalse(result["has_more"])

    def test_csv_and_json_export_exact_same_frozen_prefix(self):
        for second in range(505):
            self.sample(second)
        raw = b"".join(self.journal.iter_transport_export("json", until_id=503))
        document = json.loads(raw)
        rows = list(csv.DictReader(io.StringIO(b"".join(self.journal.iter_transport_export("csv", until_id=503)).decode("utf-8-sig"))))
        self.assertEqual(len(document["points"]), 503)
        self.assertEqual(len(rows), 503)
        self.assertEqual(document["until_id"], 503)
        self.assertEqual([int(row["id"]) for row in rows], [p["id"] for p in document["points"]])
        self.assertEqual(rows[0]["arrival_rate_hz"], "")
        self.assertEqual(rows[1]["arrival_rate_hz"], "1.0")

    def test_corrupt_value_outside_requested_page_rejects_complete_prefix(self):
        for second in range(3):
            self.sample(second)
        for key, value in (("queue_messages", -1), ("elapsed_s", "invalid"), ("arrival_rate_hz", float("inf")),
                           ("utc", "not a UTC timestamp")):
            saved = self.journal._connection.execute(f"SELECT {key} FROM transport_samples WHERE id=3").fetchone()[0]
            self.journal._connection.execute(f"UPDATE transport_samples SET {key}=? WHERE id=3", (value,))
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.journal.transport_history(limit=1)
            self.journal._connection.execute(f"UPDATE transport_samples SET {key}=? WHERE id=3", (saved,))

    def test_duplicate_elapsed_clock_is_rejected_without_rate_bridge(self):
        self.sample(0)
        self.sample(1)
        self.journal._connection.execute("UPDATE transport_samples SET elapsed_s=0 WHERE id=2")
        with self.assertRaises(ValueError):
            self.journal.transport_history()


class TransportArchiveHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "api-transport"
        self.app.journal = Journal(self.app.run_dir / "api-transport-journal.sqlite3", self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=config)
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(sim_time=0, cars=[], metrics={}, warnings=[])
        for second in range(3):
            point = self.app._transport_monitor.observe(second * 1_000_000_000, second, second, 0)
            self.app.journal.record_transport(point, sim_time=second)
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
            return response.read()

    def test_http_history_and_exports_have_exact_pinned_identity(self):
        history = json.loads(self.get("/api/transport/history?run_id=api-transport&limit=1"))
        self.assertEqual(history["run_id"], "api-transport")
        self.assertEqual(history["next_after"], 1)
        exported = json.loads(self.get("/api/transport/export?run_id=api-transport&format=json&until_id=2"))
        self.assertEqual([p["id"] for p in exported["points"]], [1, 2])
        self.assertEqual(exported["until_id"], 2)
        self.assertEqual(exported["summary"]["sample_count"], 2)

    def test_http_invalid_run_cursor_ceiling_and_format_are_400(self):
        for suffix in ("/api/transport/history?run_id=missing", "/api/transport/history?after=-1",
            "/api/transport/history?after=1.2", "/api/transport/history?until_id=4",
            "/api/transport/history?limit=0", "/api/transport/export?format=png",
            "/api/transport/export?until_id=NaN", "/api/transport/export?run_id=../escape"):
            with self.subTest(suffix=suffix), self.assertRaises(HTTPError) as error:
                self.get(suffix)
            self.assertEqual(error.exception.code, 400)

    def test_sampling_writes_once_only_when_due_and_includes_last_processed_time(self):
        for now in (3_000_000_000, 3_000_000_000, 3_500_000_000):
            self.app._sample_transport(now, dict(callback_received_messages=3))
        result = self.app.journal.transport_history()
        self.assertEqual(result["total_count"], 4)
        self.assertEqual(result["points"][-1]["sim_time"], 0)

    def test_real_log_zip_includes_same_transport_prefix_and_explicit_schema(self):
        import zipfile
        with patch("factory_yard.server.time.perf_counter_ns", return_value=2_000_000_000):
            raw = self.app.export("zip")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            document = json.loads(archive.read("transport.json"))
            rows = list(csv.DictReader(io.StringIO(archive.read("transport.csv").decode("utf-8-sig"))))
            metadata = json.loads(archive.read("metadata.json"))
            self.assertEqual(document["summary"]["sample_count"], 3)
            self.assertEqual(len(rows), 3)
            self.assertEqual(metadata["export_snapshot"]["transport_until_id"], 3)
            self.assertEqual(metadata["transport_history_schema"], 1)


if __name__ == "__main__":
    unittest.main()
