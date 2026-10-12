"""Immutable export prefixes, resource bounds and confirmed controller state."""
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import tracemalloc
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen
import zipfile

from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT, create_http_server


class DataConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(self.config, ROOT / "config/default.json", speed=1)
        self.app.run_id, self.app.run_dir = "consistent-run", Path(self.tmp.name)
        self.app.journal = Journal(self.app.run_dir / "consistent-run-journal.sqlite3", self.app.run_id,
                                   max_telemetry=2)
        self.app.metadata = dict(run_id=self.app.run_id, seed=42, model_version="test-version", config=self.config,
                                 source_sha256={"model.py": "original-digest"})
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(sim_time=1, paused=False, speed=1, cars=[], metrics={}, warnings=[])
        self.app.last_snapshot_ns = time.perf_counter_ns()

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def sample(self, sequence):
        return self.app.journal.ingest("telemetry/CAR001", dict(run_id=self.app.run_id,
            car_id="CAR001", seq=sequence, sim_time=sequence / 10, x=sequence, y=24,
            state="driving", speed=2, heading=0, period_s=.1,
            received_utc=f"2026-10-07T00:00:{sequence:02d}.123Z",
            sensors={"sampled": True, "sample_sim_time_s": sequence / 10 - .05,
                "fresh": True, "extra": {"labels": ["Észlelés", sequence]}},
            control={"source": "ideal_route_controller", "decision_sim_time_s": sequence / 10}),
            sequence * 1000, .2)

    def test_prefix_sequence_summary_does_not_include_later_samples(self):
        for sequence in range(1, 4):
            self.sample(sequence)
        first = self.app.journal.trip_history("CAR001", limit=1)
        self.sample(4)
        frozen = self.app.journal.trip_history("CAR001", until_id=first["until_id"])
        self.assertEqual(frozen["summary"]["last_seq"], 3)
        self.assertEqual(frozen["summary"]["sample_count"], 3)
        empty = self.app.journal.trip_history("CAR001", until_id=0)["summary"]
        self.assertEqual(empty["sample_count"], 0)
        self.assertIsNone(empty["last_seq"])

    def test_prefix_last_sequence_survives_observer_only_fault_frame(self):
        self.sample(1)
        fault_id = self.app.journal.ingest("diagnostics/perception/CAR001", dict(run_id=self.app.run_id,
            car_id="CAR001", seq=None, sim_time=.2, x=1, y=24, state="sensor_stop"), 2000, .2)
        self.sample(2)
        frozen = self.app.journal.trip_history("CAR001", until_id=fault_id)
        self.assertEqual(frozen["summary"]["last_seq"], 1)
        self.assertIsNone(frozen["telemetry"][-1]["seq"])

    def test_trip_json_carries_its_saved_map_configuration_and_run_hashes(self):
        self.sample(1)
        exported = json.loads(b"".join(self.app.trip_export("CAR001", format="json")))
        self.assertEqual(exported["config"], self.config)
        self.assertEqual(exported["map"]["width"], self.config["world"]["width"])
        self.assertEqual(exported["metadata"]["model_version"], "test-version")
        self.assertEqual(exported["metadata"]["source_sha256"], {"model.py": "original-digest"})
        self.assertEqual(exported["telemetry"][0]["sensors"]["extra"]["labels"], ["Észlelés", 1])

    def test_rejected_and_only_published_speed_intent_do_not_change_restart_speed(self):
        self.app.bus.publish.return_value.is_published.return_value = False
        with self.assertRaises(RuntimeError):
            self.app.control({"action": "speed", "value": 4})
        self.assertEqual(self.app.speed, 1)
        self.app.bus.publish.return_value.is_published.return_value = True
        self.app.control({"action": "speed", "value": 4})
        self.assertEqual(self.app.speed, 1)
        observed = dict(self.app.latest, run_id=self.app.run_id, speed=4, sent_ns=time.perf_counter_ns())
        self.app._accept("snapshot", observed, time.perf_counter_ns())
        self.assertEqual(self.app.speed, 4)

    def test_applied_speed_receipt_updates_restart_speed_and_boolean_intent_is_rejected(self):
        self.app._accept("events", dict(run_id=self.app.run_id, type="control_applied",
            action="speed", parameters={"value": 2}, sent_ns=time.perf_counter_ns()), time.perf_counter_ns())
        self.assertEqual(self.app.speed, 2)
        with self.assertRaises(ValueError):
            self.app.control({"action": "speed", "value": True})

    def test_complete_zip_uses_immutable_rows_while_live_writer_and_retention_continue(self):
        for sequence in range(1, 6):
            self.sample(sequence)
        started, release = threading.Event(), threading.Event()
        collected, failures = [], []
        original = Journal.iter_complete_trip_export

        def held_iterator(reader):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test export release timeout")
            yield from original(reader)

        def build():
            try:
                with self.app.export_archive() as (output, run_id, length):
                    collected.append((output.read(), run_id, length))
            except BaseException as error:
                failures.append(error)

        with patch.object(Journal, "iter_complete_trip_export", held_iterator):
            worker = threading.Thread(target=build)
            worker.start()
            self.assertTrue(started.wait(5))
            acquired = self.app.lock.acquire(timeout=1)
            try:
                self.assertTrue(acquired, "ZIP compression must not hold the live application lock")
                self.sample(6)
                self.app.journal.flush()
            finally:
                if acquired:
                    self.app.lock.release()
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        raw, run_id, length = collected[0]
        self.assertEqual(len(raw), length)
        self.assertEqual(run_id, self.app.run_id)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            metadata = json.loads(archive.read("metadata.json"))
            complete = [json.loads(row) for row in archive.read("complete_trips.jsonl").splitlines()]
            retained = list(csv.DictReader(io.StringIO(archive.read("telemetry.csv").decode("utf-8-sig"))))
            self.assertEqual([row["seq"] for row in complete], [1, 2, 3, 4, 5])
            self.assertEqual([row["seq"] for row in retained], ["4", "5"])
            self.assertEqual(metadata["export_snapshot"]["complete_trip_samples"], 5)
            self.assertEqual(metadata["export_snapshot"]["retained_telemetry_samples"], 2)
            self.assertEqual(metadata["export_snapshot"]["trips_until_id"], complete[-1]["id"])
            self.assertEqual(complete[0]["utc"], "2026-10-07T00:00:01.123Z")
            self.assertEqual(complete[0]["sensors"]["extra"]["labels"], ["Észlelés", 1])
            self.assertEqual(complete[0]["control"]["decision_sim_time_s"], .1)

    def test_http_zip_streams_temp_file_with_exact_length_without_byte_export_api(self):
        self.sample(1)
        server = create_http_server(self.app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(self.app, "export", side_effect=AssertionError("HTTP must not materialise ZIP bytes")):
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/export?format=zip", timeout=5) as response:
                    raw = response.read()
                    self.assertEqual(int(response.headers["Content-Length"]), len(raw))
                    self.assertIn("yard-consistent-run.zip", response.headers["Content-Disposition"])
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("complete_trips.jsonl", archive.namelist())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_spooled_output_is_closed_after_client_use_or_failure(self):
        self.sample(1)
        with self.app.export_archive() as (output, _, _):
            held = output
            self.assertFalse(output.closed)
        self.assertTrue(held.closed)

        with self.assertRaisesRegex(RuntimeError, "client cancelled"):
            with self.app.export_archive() as (output, _, _):
                held = output
                raise RuntimeError("client cancelled")
        self.assertTrue(held.closed)

    def test_export_memory_does_not_grow_with_full_rich_telemetry_payload(self):
        # The former StringIO -> encoded CSV -> in-memory ZIP path allocated
        # several copies of this 32 MiB retained payload. Only export allocations
        # are measured; creating the fixture does not count against its budget.
        self.app.journal.max_telemetry = 1000
        for sequence in range(500):
            self.app.journal.ingest("telemetry/CAR001", dict(run_id=self.app.run_id,
                car_id="CAR001", seq=sequence, sim_time=sequence / 10, x=sequence, y=24,
                state="driving", padding="x" * (64 * 1024),
                sensors={"sampled": True, "sample_sim_time_s": sequence / 10, "fresh": True}),
                sequence * 1000, .2)
        tracemalloc.start()
        try:
            with self.app.export_archive() as (output, _, _):
                with zipfile.ZipFile(output) as archive:
                    self.assertEqual(json.loads(archive.read("metadata.json"))["export_snapshot"]["retained_telemetry_samples"], 500)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 24 * 1024 * 1024, f"Export allocated {peak / 1024 ** 2:.1f} MiB")

    def test_compressed_archive_spills_to_disk_and_releases_temporary_file(self):
        self.sample(1)
        original = tempfile.SpooledTemporaryFile
        settings = []

        def small_test_spool(**kwargs):
            settings.append(kwargs["max_size"])
            return original(max_size=256, mode=kwargs["mode"])

        with patch("factory_yard.server.tempfile.SpooledTemporaryFile", side_effect=small_test_spool):
            with self.app.export_archive() as (output, _, length):
                held = output
                self.assertGreater(length, 256)
                self.assertTrue(output._rolled)
                with zipfile.ZipFile(output) as archive:
                    self.assertIsNone(archive.testzip())
        self.assertEqual(settings, [8 * 1024 * 1024])
        self.assertTrue(held.closed)


if __name__ == "__main__":
    unittest.main()
