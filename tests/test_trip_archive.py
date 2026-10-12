"""Full received-trip evidence is independent of rolling rich/raw log windows."""
import csv
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import urlopen

from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT, create_http_server


class TripArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run.sqlite3"
        self.journal = Journal(self.path, "run-a", max_telemetry=2)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def sample(self, seq, timestamp, car_id="CAR001"):
        self.journal.ingest(f"telemetry/{car_id}", dict(run_id="run-a", car_id=car_id, seq=seq,
            sim_time=timestamp, x=seq * 2, y=24, speed=2, state="driving", heading=180,
            slot_id="P02", wait_reason="Zónaengedély: C1" if seq == 2 else "",
            distance_m=seq * 2, route_progress_m=seq * 2, period_s=.1,
            received_utc=f"2026-10-05T00:00:0{seq}.000Z"), seq * 100, .2)

    def test_full_archive_survives_rich_window_retention_and_reopening(self):
        for seq in range(1, 7):
            self.sample(seq, seq / 10)
        self.assertEqual(len(self.journal.car_history("CAR001")["telemetry"]), 2)
        first = self.journal.trip_history("CAR001", limit=2)
        self.assertEqual([row["seq"] for row in first["telemetry"]], [1, 2])
        self.assertEqual(first["summary"]["sample_count"], 6)
        self.assertTrue(first["summary"]["complete_received_archive"])
        self.assertEqual(first["telemetry"][1]["wait_reason"], "Zónaengedély: C1")
        self.journal.close()
        self.journal = Journal(self.path, "run-a", max_telemetry=2)
        rows, cursor = [], 0
        while True:
            page = self.journal.trip_history("CAR001", after=cursor, limit=2, until_id=first["until_id"])
            rows.extend(page["telemetry"])
            if page["next_after"] is None:
                break
            cursor = page["next_after"]
        self.assertEqual([row["seq"] for row in rows], list(range(1, 7)))
        self.assertEqual(self.journal.stats()["trip_samples"], 6)

    def test_sequence_loss_and_sensor_time_gap_are_both_visible(self):
        self.sample(1, .1)
        self.sample(2, 5.1)
        self.sample(4, 5.2)
        history = self.journal.trip_history("CAR001")
        self.assertEqual(history["telemetry"][1]["time_gap_before_s"], 5)
        self.assertEqual(history["telemetry"][2]["gap_before"], 1)
        self.assertEqual(history["summary"]["sequence_gaps"], 1)
        self.assertEqual(history["summary"]["time_gap_count"], 1)
        self.assertFalse(history["summary"]["mqtt_lossless"])

    def test_frozen_page_ceiling_and_summary_do_not_chase_new_live_samples(self):
        for seq in range(1, 4):
            self.sample(seq, seq / 10)
        first = self.journal.trip_history("CAR001", limit=1)
        self.sample(4, .4)
        second = self.journal.trip_history("CAR001", limit=2, after=first["next_after"], until_id=first["until_id"])
        self.assertEqual([row["seq"] for row in second["telemetry"]], [2, 3])
        self.assertIsNone(second["next_after"])
        self.assertEqual(second["summary"]["sample_count"], 3)
        self.assertEqual(second["summary"]["total_archived_sample_count"], 4)
        self.assertAlmostEqual(second["summary"]["last_sim_time"], .3)

    def test_streamed_exports_include_all_samples_and_semantic_events_with_clocks(self):
        for seq in range(1, 7):
            self.sample(seq, seq / 10)
        self.journal.record("state", "departed", "Kiadás", car_id="CAR001", sim_time=2, details={"x": 12, "y": 24})
        exported = json.loads(b"".join(self.journal.iter_trip_export("CAR001", "json")))
        self.assertEqual(len(exported["telemetry"]), 6)
        self.assertEqual(exported["events"][0]["event_type"], "departed")
        rows = list(csv.DictReader(io.StringIO(b"".join(self.journal.iter_trip_export("CAR001", "csv")).decode("utf-8-sig"))))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[1]["wait_reason"], "Zónaengedély: C1")
        self.assertEqual(rows[0]["utc"], "2026-10-05T00:00:01.000Z")
        self.assertEqual(rows[-1]["record_type"], "event")

    def test_event_only_vehicle_is_searchable_without_invented_positions(self):
        self.journal.record("lifecycle", "admitted", "Belépett", car_id="CAR999", sim_time=1)
        cars = self.journal.cars(search="CAR999")
        self.assertEqual(cars["cars"][0]["sample_count"], 0)
        history = self.journal.trip_history("CAR999")
        self.assertEqual(history["telemetry"], [])
        self.assertEqual(len(history["events"]), 1)

    def test_legacy_readonly_history_discloses_missing_prefix_without_changing_evidence(self):
        for seq, timestamp in ((1, .1), (2, .2), (3, .3), (4, 5.3)):
            self.sample(seq, timestamp)
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.executescript("DROP TABLE trip_samples; DROP TABLE trip_cars; DELETE FROM metadata WHERE key LIKE 'compact_history%';")
            database.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with_readonly = Journal.open_readonly(self.path, "run-a")
        try:
            history = with_readonly.trip_history("CAR001")
            self.assertFalse(history["summary"]["complete_received_archive"])
            self.assertEqual(history["summary"]["prior_missing_samples_global"], 2)
            self.assertEqual([row["seq"] for row in history["telemetry"]], [3, 4])
            self.assertEqual(history["telemetry"][1]["time_gap_before_s"], 5)
            exported = json.loads(b"".join(with_readonly.iter_trip_export("CAR001", "json")))
            self.assertEqual(exported["telemetry"][1]["time_gap_before_s"], 5)
        finally:
            with_readonly.close()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())


class TripAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "run-b"
        self.app.journal = Journal(self.app.run_dir / "run-b-journal.sqlite3", "run-b", max_telemetry=2)
        self.app.metadata = dict(run_id="run-b", seed=7, strategy="nearest", history_schema=1,
                                 started_at_utc="2026-10-05T00:01:00Z", config=config)
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(sim_time=1, cars=[], metrics={}, warnings=[])
        self.app.last_snapshot_ns = time.perf_counter_ns()
        old = Journal(self.app.run_dir / "run-a-journal.sqlite3", "run-a", max_telemetry=2)
        for index in range(5):
            old.ingest("telemetry/CAR001", dict(run_id="run-a", car_id="CAR001", seq=index + 1,
                sim_time=index / 10, x=50+index, y=24, speed=2, state="driving", period_s=.1), 1000 + index, .1)
        old.close()
        (self.app.run_dir / "run-a-metadata.json").write_text(json.dumps(dict(run_id="run-a", config=config,
            started_at_utc="2026-10-05T00:00:00Z", seed=1, strategy="fixed", history_schema=1)), encoding="utf-8")
        self.app.journal.ingest("telemetry/CAR001", dict(run_id="run-b", car_id="CAR001", seq=1,
                sim_time=0, x=99, y=24, speed=2, state="driving"), 2000, .1)
        self.server = create_http_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.app.close()
        self.tmp.cleanup()

    def get(self, path):
        with urlopen(self.url + path, timeout=5) as response:
            return response.read(), response.headers

    def test_archived_runs_and_reused_car_id_resolve_their_own_positions_and_map(self):
        current = json.loads(self.get("/api/car/history?car_id=CAR001")[0])
        archived = json.loads(self.get("/api/car/history?run_id=run-a&car_id=CAR001&limit=2")[0])
        self.assertEqual(current["telemetry"][0]["x"], 99)
        self.assertEqual([row["x"] for row in archived["telemetry"]], [50, 51])
        self.assertEqual(archived["map"]["width"], 140)
        self.assertEqual(archived["summary"]["sample_count"], 5)
        self.assertIsNone(archived["current"])
        cars = json.loads(self.get("/api/cars?run_id=run-a&search=001")[0])
        self.assertEqual(cars["cars"][0]["id"], "CAR001")
        self.assertFalse(cars["cars"][0]["active"])
        runs = json.loads(self.get("/api/runs?limit=1")[0])
        self.assertEqual(runs["runs"][0]["run_id"], "run-b")
        self.assertTrue(runs["next_before"])

    def test_native_streamed_csv_json_have_attachment_headers_and_all_archived_samples(self):
        raw, headers = self.get("/api/car/history/export?run_id=run-a&car_id=CAR001&format=csv")
        self.assertIn("utnaplo-run-a-CAR001.csv", headers["Content-Disposition"])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))), 5)
        exported = json.loads(self.get("/api/car/history/export?run_id=run-a&car_id=CAR001&format=json")[0])
        self.assertEqual(len(exported["telemetry"]), 5)
        self.assertEqual(exported["run_id"], "run-a")

    def test_invalid_archive_ids_and_paging_fail_before_successful_download_headers(self):
        for path in ("/api/car/history?car_id=", "/api/car/history?run_id=../../x&car_id=CAR001",
                     "/api/car/history?car_id=CAR001&after=abc", "/api/car/history/export?run_id=missing&car_id=CAR001",
                     "/api/car/history/export?car_id=CAR001&format=exe"):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
