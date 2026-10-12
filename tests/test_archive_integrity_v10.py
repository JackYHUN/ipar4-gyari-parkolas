"""Frozen semantic evidence and bounded complete-route navigation context."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT, create_http_server


class ArchiveIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "archive-journal.sqlite3"
        self.journal = Journal(self.path, "archive", max_telemetry=2)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def sample(self, seq, *, timestamp=None, x=None, y=24, car_id="CAR001"):
        return self.journal.ingest(f"telemetry/{car_id}", dict(run_id="archive", car_id=car_id,
            seq=seq, sim_time=seq / 10 if timestamp is None else timestamp,
            x=seq if x is None else x, y=y, speed=2, heading=0, state="driving", period_s=.1,
            sensors={"sampled": True, "sample_sim_time_s": seq / 10, "fresh": True}), seq, .1)

    def test_independent_semantic_ceiling_freezes_pages_and_old_event_api(self):
        sample_id = self.sample(1)
        for index in range(505):
            self.journal.record("decision", "early", str(index), car_id="CAR001", sim_time=.1)
        first = self.journal.trip_history_page("CAR001")
        self.sample(2)
        self.journal.record("decision", "later", "Newer event", car_id="CAR001", sim_time=.2)
        frozen = self.journal.trip_history_page("CAR001", until_id=sample_id,
            events_until_id=first["events_until_id"])
        self.assertEqual(frozen["events_until_id"], 505)
        self.assertEqual(frozen["event_total"], 505)
        self.assertTrue(all(row["event_type"] == "early" for row in frozen["events"]))
        older = self.journal.trip_history("CAR001", after=sample_id, until_id=sample_id,
            events_before=frozen["next_events_before"], events_until_id=first["events_until_id"])
        self.assertEqual(len(older["events"]), 5)
        self.assertIsNone(older["next_events_before"])
        self.assertEqual(older["event_total"], 505)
        self.assertEqual(self.journal.trip_history("CAR001", events_until_id=0)["events"], [])
        self.assertEqual(self.journal.trip_history_page("CAR001")["event_total"], 506)
        self.assertNotEqual(first["events_until_id"], first["until_id"])

    def test_snapshot_is_pinned_before_metadata_counts_and_all_later_reads(self):
        self.sample(1)
        self.journal.record("decision", "first", "First", car_id="CAR001", sim_time=.1)
        self.journal.flush()
        reader = Journal.open_readonly(self.path, "archive", snapshot=True)
        try:
            self.sample(2)
            self.journal.record("decision", "later", "Later", car_id="CAR001", sim_time=.2)
            self.journal.flush()
            self.assertEqual(reader.stats()["trip_samples"], 1)
            page = reader.trip_history_page("CAR001")
            overview = reader.trip_overview("CAR001")
            self.assertEqual(page["summary"]["sample_count"], 1)
            self.assertEqual(page["event_total"], 1)
            self.assertEqual(overview["total_count"], 1)
            self.assertEqual(reader.export_boundaries()["complete_trip_samples"], 1)
        finally:
            reader.close()
        current = Journal.open_readonly(self.path, "archive", snapshot=True)
        try:
            self.assertEqual(current.trip_overview("CAR001")["total_count"], 2)
        finally:
            current.close()

    def test_overview_preserves_exact_positions_endpoints_bounds_and_skipped_breaks(self):
        ids = []
        for seq in range(1, 19):
            ids.append(self.sample(seq))
            self.sample(seq, car_id="CAR002")
        self.journal._connection.execute("UPDATE trip_samples SET x=NULL WHERE id=?", (ids[4],))
        self.journal._connection.execute("UPDATE trip_samples SET gap_before=3 WHERE id=?", (ids[6],))
        self.journal._connection.execute("UPDATE trip_samples SET sim_time=.01 WHERE id=?", (ids[9],))
        self.journal._connection.execute("UPDATE trip_samples SET x=900 WHERE id=?", (ids[10],))
        overview = self.journal.trip_overview("CAR001", limit=5)
        self.assertLessEqual(overview["point_count"], 5)
        self.assertEqual((overview["points"][0]["id"], overview["points"][-1]["id"]), (ids[0], ids[-1]))
        self.assertEqual(overview["valid_position_count"], 17)
        self.assertEqual(overview["bounds"]["max_x"], 900)
        self.assertTrue(overview["downsampled"])
        reasons = {reason for point in overview["points"] for reason in point["break_reasons"]}
        self.assertTrue({"invalid_position", "sequence_gap", "time_regression"}.issubset(reasons))
        self.assertTrue(all(point["break_before"] for point in overview["points"][1:] if point["break_reasons"]))
        for point in overview["points"]:
            stored = self.journal._connection.execute("SELECT x,y,sim_time FROM trip_samples WHERE id=?", (point["id"],)).fetchone()
            self.assertEqual((point["x"], point["y"], point["sim_time"]), tuple(stored))
        two = self.journal.trip_overview("CAR001", limit=2)
        self.assertEqual([point["id"] for point in two["points"]], [ids[0], ids[-1]])
        self.assertTrue(two["points"][-1]["break_before"])

    def test_large_overview_is_bounded_without_rich_decoding_and_frozen_when_appended(self):
        total = 20_025
        rows = [(seq * 2 - 1, "2026-10-07T00:00:00.000Z", "CAR001", seq / 10,
                 2, seq / 10, 24, "driving", 0, None, "", seq / 10, seq / 10,
                 seq, int(seq == 17), .3 if seq == 31 else 0, "{}") for seq in range(1, total + 1)]
        self.journal._connection.executemany("""INSERT INTO trip_samples
            (id,utc,car_id,sim_time,speed,x,y,state,heading,slot_id,wait_reason,
             route_progress_m,distance_m,seq,gap_before,time_gap_before_s,details)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        self.journal._connection.execute("INSERT INTO trip_perception VALUES (?,?,?,?,?,?,?,?,?)",
            (1, "CAR001", "2026-10-07T00:00:00.000Z", .1, .1, 1, 1, 5, b"not a zlib payload"))
        with patch("factory_yard.journal.zlib.decompress", side_effect=AssertionError("sensor decoded")), \
             patch.object(self.journal, "_trip_item", side_effect=AssertionError("rich row decoded")), \
             patch("factory_yard.journal.json.loads", side_effect=AssertionError("rich details decoded")):
            overview = self.journal.trip_overview("CAR001")
            prefix = self.journal.trip_overview("CAR001", until_id=39)
        self.assertEqual(overview["total_count"], total)
        self.assertLessEqual(overview["point_count"], 4000)
        self.assertEqual((overview["points"][0]["id"], overview["points"][-1]["id"]), (1, total * 2 - 1))
        self.assertEqual(prefix["total_count"], 20)
        self.assertEqual(prefix["points"][-1]["id"], 39)
        self.journal._connection.execute("INSERT INTO trip_samples SELECT 40051,utc,car_id,3000,speed,x,y,state,heading,"
            "slot_id,wait_reason,route_progress_m,distance_m,seq+1,gap_before,time_gap_before_s,details "
            "FROM trip_samples WHERE id=40049")
        frozen = self.journal.trip_overview("CAR001", until_id=overview["until_id"])
        self.assertEqual(frozen["points"], overview["points"])
        self.assertEqual(frozen["total_count"], total)

    def test_legacy_outline_readonly_evidence_and_gaps_survive_without_sensor_decoding(self):
        ids = [self.sample(1), self.sample(2), self.sample(5, timestamp=2)]
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.executescript("DROP TABLE trip_samples; DROP TABLE trip_cars; DROP TABLE trip_perception; "
                "DELETE FROM metadata WHERE key LIKE 'compact_history%';")
            database.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "archive", snapshot=True)
        try:
            with patch("factory_yard.journal.zlib.decompress", side_effect=AssertionError("sensor decoded")), \
                 patch("factory_yard.journal.json.loads", side_effect=AssertionError("rich details decoded")):
                overview = reader.trip_overview("CAR001")
            self.assertEqual([point["id"] for point in overview["points"]], ids[-2:])
            self.assertEqual(overview["points"][-1]["gap_before"], 2)
            self.assertEqual(overview["points"][-1]["time_gap_before_s"], 1.8)
            self.assertTrue(overview["points"][-1]["break_before"])
            self.assertFalse(overview["complete_received_archive"])
            self.assertEqual(overview["prior_missing_samples_global"], 1)
        finally:
            reader.close()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_empty_or_invalid_positions_do_not_invent_geometry_and_inputs_are_strict(self):
        empty = self.journal.trip_overview("CAR999")
        self.assertEqual(empty["points"], [])
        self.assertIsNone(empty["bounds"])
        self.sample(1)
        self.journal._connection.execute("UPDATE trip_samples SET x=NULL,y=NULL")
        self.assertEqual(self.journal.trip_overview("CAR001")["valid_position_count"], 0)
        for value in (-1, "1.5", True, 9_007_199_254_740_992):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.journal.trip_history_page("CAR001", events_until_id=value)
            with self.subTest(old=value), self.assertRaises(ValueError):
                self.journal.trip_history("CAR001", events_until_id=value)
            with self.subTest(outline=value), self.assertRaises(ValueError):
                self.journal.trip_overview("CAR001", until_id=value)
        for value in (0, 1, "2.5", True, None):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.journal.trip_overview("CAR001", limit=value)


class ArchiveIntegrityAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "api-archive"
        self.app.journal = Journal(self.app.run_dir / "api-archive-journal.sqlite3", self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=config, model_version="saved-archive-context")
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(cars=[], metrics={}, warnings=[])
        for seq in range(1, 4):
            self.sample(seq)
            self.app.journal.record("decision", "early", "Saved", car_id="CAR001", sim_time=seq / 10)
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

    def sample(self, seq):
        self.app.journal.ingest("telemetry/CAR001", dict(run_id=self.app.run_id, car_id="CAR001",
            seq=seq, sim_time=seq / 10, x=seq, y=24, state="driving", period_s=.1), seq, .1)

    def get(self, path, query=""):
        with urlopen(self.url + path + "?car_id=CAR001&" + query, timeout=5) as response:
            return json.loads(response.read())

    def test_http_combined_outline_and_both_ceiling_ids_remain_frozen(self):
        first = self.get("/api/car/history/page", "include_overview=1&limit=1")
        self.sample(4)
        self.app.journal.record("decision", "later", "Later", car_id="CAR001", sim_time=.4)
        query = f"until_id={first['until_id']}&events_until_id={first['events_until_id']}"
        frozen = self.get("/api/car/history/page", query + "&include_overview=1&direction=end")
        self.assertEqual(frozen["overview"], first["overview"])
        self.assertEqual(frozen["event_total"], 3)
        self.assertEqual(frozen["events"], first["events"])
        self.assertEqual(frozen["metadata"]["model_version"], "saved-archive-context")
        standalone = self.get("/api/car/history/overview", query + "&limit=2")
        self.assertEqual(standalone["total_count"], 3)
        self.assertEqual(standalone["map"]["width"], 140)
        self.assertNotIn("overview", self.get("/api/car/history/page", query))
        old = self.get("/api/car/history", query)
        self.assertEqual(old["event_total"], 3)

    def test_http_snapshot_remains_consistent_if_writer_commits_between_page_and_outline(self):
        original = Journal.trip_overview
        committed = []
        def append_then_read(reader, *args, **kwargs):
            if not committed:
                self.sample(4)
                self.app.journal.record("decision", "later", "Later", car_id="CAR001", sim_time=.4)
                self.app.journal.flush()
                committed.append(True)
            return original(reader, *args, **kwargs)
        with patch.object(Journal, "trip_overview", append_then_read):
            result = self.get("/api/car/history/page", "include_overview=1")
        self.assertEqual(result["summary"]["total_archived_sample_count"], 3)
        self.assertEqual(result["overview"]["total_count"], 3)
        self.assertEqual(result["event_total"], 3)
        self.assertEqual(self.get("/api/car/history/page")["event_total"], 4)

    def test_http_invalid_event_ceiling_or_overview_arguments_are_400(self):
        for path, query in (("/api/car/history/page", "events_until_id=-1"),
            ("/api/car/history", "events_until_id=1.5"),
            ("/api/car/history/page", "include_overview=true"),
            ("/api/car/history/overview", "limit=1"),
            ("/api/car/history/overview", "until_id=NaN")):
            with self.subTest(path=path, query=query), self.assertRaises(HTTPError) as failed:
                self.get(path, query)
            self.assertEqual(failed.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
