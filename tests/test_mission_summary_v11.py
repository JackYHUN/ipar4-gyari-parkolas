"""Full received-prefix mission accounting without rich sensor decompression."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import tracemalloc
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT, create_http_server


class MissionSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "results")
        self.path = Path(self.tmp.name) / "mission-journal.sqlite3"
        self.journal = Journal(self.path, "mission", max_telemetry=2)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def sample(self, seq, *, timestamp=None, state="driving", wait_reason="", distance=None,
               sensors=None, car_id="CAR001", **extra):
        item = dict(run_id="mission", car_id=car_id, seq=seq,
            sim_time=seq / 10 if timestamp is None and seq is not None else timestamp,
            x=seq, y=24, speed=2, heading=0, state=state, wait_reason=wait_reason,
            distance_m=seq if distance is None else distance, period_s=.1)
        if sensors is not None:
            item["sensors"] = sensors
        item.update(extra)
        return self.journal.ingest(f"telemetry/{car_id}", item, 1, .1)

    def test_state_and_reason_exposure_hold_left_sample_only(self):
        ids = [self.sample(1), self.sample(2, state="pedestrian_stop", wait_reason="Zebra X"),
               self.sample(3), self.sample(4, state="parked", wait_reason="Dispatch")]
        result = self.journal.trip_mission("CAR001")
        states = {item["state"]: item for item in result["states"]}
        reasons = {item["reason"]: item for item in result["wait_reasons"]}
        self.assertAlmostEqual(states["driving"]["estimated_s"], .2)
        self.assertAlmostEqual(states["pedestrian_stop"]["estimated_s"], .1)
        self.assertEqual(states["parked"]["estimated_s"], 0)
        self.assertEqual(reasons["Dispatch"]["estimated_s"], 0)
        self.assertAlmostEqual(result["timing"]["valid_estimated_duration_s"], .3)
        self.assertEqual(result["distance"]["value_m"], 3)
        self.assertEqual(result["receipt"]["first"]["id"], ids[0])
        self.assertEqual(result["receipt"]["last"]["id"], ids[-1])
        self.assertIn("estimate", result["inference_note"])

    def test_gaps_have_unknown_duration_and_reported_reason_does_not_fill_gap(self):
        self.sample(1, state="waiting", wait_reason="A")
        self.sample(3, state="waiting", wait_reason="B")
        self.sample(4, state="driving")
        result = self.journal.trip_mission("CAR001")
        timing = result["timing"]
        self.assertAlmostEqual(timing["unknown_positive_duration_s"], .2)
        self.assertAlmostEqual(timing["valid_estimated_duration_s"], .1)
        self.assertEqual(timing["exclusions"], {"sequence_gap": 1, "time_gap": 1})
        reasons = {item["reason"]: item["estimated_s"] for item in result["wait_reasons"]}
        self.assertEqual(reasons["A"], 0)
        self.assertAlmostEqual(reasons["B"], .1)

    def test_missing_reversed_equal_clocks_and_sequence_regressions_are_excluded(self):
        self.sample(10, timestamp=.1)
        self.sample(5, timestamp=.2)
        self.sample(6, timestamp=.3)
        self.sample(7, timestamp=.2)
        self.sample(7, timestamp=.2)
        self.sample(None, timestamp=.3, state="sensor_stop")
        self.sample(8, timestamp=.4)
        self.sample(9, timestamp=-1)
        self.sample(10, timestamp=.5, sim_time=None)
        result = self.journal.trip_mission("CAR001")
        self.assertEqual(result["timing"]["valid_interval_count"], 1)
        self.assertAlmostEqual(result["timing"]["valid_estimated_duration_s"], .1)
        exclusions = result["timing"]["exclusions"]
        for reason in ("sequence_regression", "clock_regression", "zero_clock_interval",
                       "sequence_duplicate", "missing_or_invalid_sequence", "missing_or_invalid_clock"):
            self.assertIn(reason, exclusions)
        self.assertAlmostEqual(sum(item["estimated_s"] for item in result["states"]), .1)

    def test_duplicate_missing_clock_anchors_use_received_ids(self):
        first_id = self.sample(1, state="parked", timestamp=.1)
        self.sample(2, state="parked", timestamp=.1)
        last_id = self.sample(3, state="parked", timestamp=.2)
        result = self.journal.trip_mission("CAR001")
        self.assertEqual(result["receipt"]["first"]["id"], first_id)
        self.assertEqual(result["receipt"]["last"]["id"], last_id)
        self.assertAlmostEqual(result["timing"]["valid_estimated_duration_s"], .1)

    def test_counter_regression_missing_and_negative_do_not_invent_travel(self):
        self.sample(1, distance=10)
        self.sample(2, distance=5)
        result = self.journal.trip_mission("CAR001")["distance"]
        self.assertFalse(result["available"])
        self.assertIsNone(result["value_m"])
        self.assertEqual(result["regression_count"], 1)
        self.sample(3, distance=-1)
        self.sample(4, distance=4, distance_m=None)
        result = self.journal.trip_mission("CAR001")["distance"]
        self.assertEqual(result["missing_counter_count"], 2)
        self.assertEqual(result["negative_counter_count"], 1)
        self.assertIsNone(result["value_m"])

    def test_perception_scalar_counts_age_and_missing_coverage(self):
        sensed = dict(sampled=True, fresh=True, sample_sim_time_s=.1)
        self.sample(1, sensors=sensed)
        self.sample(2, sensors=dict(sensed, fault_active=True))
        self.sample(3, sensors=dict(sampled=False, fresh=False))
        self.sample(4)
        self.sample(5, sensors=dict(sensed, sample_sim_time_s=9))
        self.sample(6, sensors=dict(sensed, sample_sim_time_s=None))
        result = self.journal.trip_mission("CAR001")["perception"]
        self.assertEqual((result["received_frames"], result["fresh_frames"], result["stale_frames"],
            result["unsampled_frames"], result["missing_frames"]), (5, 3, 1, 1, 1))
        self.assertEqual(result["unique_sample_clocks"], 2)
        self.assertAlmostEqual(result["max_age_s"], .1)
        self.assertEqual(result["invalid_age_frames"], 2)
        self.assertFalse(result["complete_received_archive"])

    def test_frozen_sample_and_event_prefix_do_not_include_appends(self):
        self.sample(1)
        self.journal.record("decision", "first", "First", car_id="CAR001")
        self.sample(2)
        first = self.journal.trip_mission("CAR001")
        self.sample(3)
        self.journal.record("decision", "later", "Later", car_id="CAR001")
        frozen = self.journal.trip_mission("CAR001", until_id=first["until_id"],
            events_until_id=first["events_until_id"])
        self.assertEqual(first, frozen)
        current = self.journal.trip_mission("CAR001")
        self.assertEqual(current["receipt"]["sample_count"], 3)
        self.assertEqual(current["events"]["received_count"], 2)

    def test_independent_reader_snapshot_retains_scalar_aggregates(self):
        self.sample(1)
        self.journal.flush()
        reader = Journal.open_readonly(self.path, "mission", snapshot=True)
        try:
            self.sample(2)
            self.journal.flush()
            self.assertEqual(reader.trip_mission("CAR001")["receipt"]["sample_count"], 1)
            self.assertEqual(self.journal.trip_mission("CAR001")["receipt"]["sample_count"], 2)
        finally:
            reader.close()

    def test_over_twenty_thousand_rows_bounded_exact_groups_and_zero_sensor_decodes(self):
        total = 20_025
        for start in range(1, total + 1, 500):
            rows = [(seq, "2026-10-09T00:00:00Z", "CAR001", seq / 10, 2, seq / 10, 24,
                "state-" + str(seq % 35), 0, None, "reason-" + str(seq % 45), seq / 10,
                seq / 10, seq, 0, 0, "{}") for seq in range(start, min(start + 500, total + 1))]
            self.journal._connection.executemany("INSERT INTO trip_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.journal._connection.execute("INSERT INTO trip_perception VALUES (?,?,?,?,?,?,?,?,?)",
            (1, "CAR001", "2026-10-09T00:00:00Z", .1, .1, 1, 1, 7, b"invalid-zlib"))
        tracemalloc.start()
        try:
            with patch("factory_yard.journal.zlib.decompress", side_effect=AssertionError("sensor decoded")), \
                 patch.object(self.journal, "_trip_item", side_effect=AssertionError("rich row decoded")), \
                 patch("factory_yard.journal.json.loads", side_effect=AssertionError("rich details decoded")):
                result = self.journal.trip_mission("CAR001")
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertEqual(result["receipt"]["sample_count"], total)
        self.assertEqual(result["receipt"]["last"]["id"], total)
        self.assertEqual((len(result["states"]), len(result["wait_reasons"])), (20, 20))
        self.assertAlmostEqual(result["timing"]["valid_estimated_duration_s"], (total - 1) / 10)
        for key, other in (("states", "states_other"), ("wait_reasons", "wait_reasons_other")):
            self.assertEqual(sum(item["sample_count"] for item in result[key]) + result["aggregation"][other]["sample_count"], total)
            self.assertAlmostEqual(sum(item["estimated_s"] for item in result[key]) + result["aggregation"][other]["estimated_s"], (total - 1) / 10)
            self.assertGreater(result["aggregation"][other]["sample_count"], 0)

    def test_legacy_readonly_coverage_bytes_and_no_rich_decode(self):
        self.sample(1, sensors={"sampled": True, "fresh": True})
        self.sample(2)
        self.sample(4, timestamp=.4)
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.executescript("DROP TABLE trip_samples; DROP TABLE trip_cars; DROP TABLE trip_perception; "
                "DELETE FROM metadata WHERE key LIKE 'compact_history%';")
            database.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "mission", snapshot=True)
        try:
            with patch("factory_yard.journal.zlib.decompress", side_effect=AssertionError("sensor decoded")), \
                 patch("factory_yard.journal.json.loads", side_effect=AssertionError("rich details decoded")):
                result = reader.trip_mission("CAR001")
            self.assertEqual(result["receipt"]["sample_count"], 2)
            self.assertFalse(result["receipt"]["complete_received_archive"])
            self.assertEqual(result["receipt"]["prior_missing_samples_global"], 1)
            self.assertFalse(result["perception"]["available"])
            self.assertIsNone(result["perception"]["missing_frames"])
            self.assertAlmostEqual(result["timing"]["unknown_positive_duration_s"], .2)
        finally:
            reader.close()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_empty_and_event_only_runs_and_strict_ceilings(self):
        empty = self.journal.trip_mission("CAR999")
        self.assertEqual(empty["receipt"]["sample_count"], 0)
        self.assertIsNone(empty["receipt"]["first"])
        self.assertFalse(empty["distance"]["available"])
        self.assertEqual(empty["states"], [])
        self.journal.record("decision", "only-event", "Event only", car_id="CAR999")
        self.assertEqual(self.journal.trip_mission("CAR999")["events"]["received_count"], 1)
        for value in (-1, "1.5", True, "NaN", 9_007_199_254_740_992):
            for parameter in ("until_id", "events_until_id"):
                with self.subTest(value=value, parameter=parameter), self.assertRaises(ValueError):
                    self.journal.trip_mission("CAR999", **{parameter: value})

    def test_malformed_legacy_clock_cadence_and_sequence_totals_match_groups(self):
        # SQLite numeric clock columns keep REAL affinity, whereas cadence and
        # sequence may be strings or booleans inside old JSON details. They
        # must not be interpreted differently by Python and SQL estimates.
        self.sample(1, timestamp=0)
        self.sample(2, timestamp=.2, period_s=".3")
        self.sample(3, timestamp=.4, period_s=False)
        self.sample(4, timestamp=.6, period_s=True)
        self.sample(5, timestamp=.7, period_s=.1)
        self.sample(6, timestamp=.8)
        self.sample(7, timestamp=.9)
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as database:
            # Retain all old rows for this independent legacy fixture.
            database.execute("DELETE FROM telemetry")
            for index, (clock, seq, period) in enumerate(((0, 1, .1), (.2, 2, ".3"),
                    (.4, 3, False), (.6, 4, True), ("invalid", 5, .1), (.8, "6", .1), (.9, 7, .1)), 1):
                database.execute("INSERT INTO telemetry VALUES (?,?,?,?,?,?,?,?,?)", (index,
                    "2026-10-09T00:00:00Z", "CAR001", clock, 2, index, 24, "driving",
                    json.dumps(dict(seq=seq, period_s=period, distance_m=index))))
            database.executescript("DROP TABLE trip_samples; DROP TABLE trip_cars; DROP TABLE trip_perception; "
                "DELETE FROM metadata WHERE key LIKE 'compact_history%';")
            database.commit()
        reader = Journal.open_readonly(self.path, "mission", snapshot=True)
        try:
            result = reader.trip_mission("CAR001")
            self.assertEqual(result["timing"]["valid_interval_count"], 1)
            self.assertAlmostEqual(result["timing"]["valid_estimated_duration_s"], .2)
            self.assertEqual(result["timing"]["exclusions"]["missing_or_invalid_clock"], 2)
            for key, other in (("states", "states_other"), ("wait_reasons", "wait_reasons_other")):
                self.assertAlmostEqual(sum(item["estimated_s"] for item in result[key]) +
                    result["aggregation"][other]["estimated_s"], result["timing"]["valid_estimated_duration_s"])
        finally:
            reader.close()

    def test_overview_breaks_sequence_regression_and_duplicate_without_loss_count(self):
        for seq, timestamp in ((10, .1), (5, .2), (6, .3), (6, .4)):
            self.sample(seq, timestamp=timestamp)
        points = self.journal.trip_overview("CAR001")["points"]
        self.assertIn("sequence_regression", points[1]["break_reasons"])
        self.assertFalse(points[2]["break_before"])
        self.assertIn("sequence_duplicate", points[3]["break_reasons"])


class MissionAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "results")
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "mission-api"
        self.app.journal = Journal(self.app.run_dir / "mission-api-journal.sqlite3", self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=config)
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(cars=[], metrics={}, warnings=[])
        for seq in range(1, 4):
            self.sample(seq)
            self.app.journal.record("decision", "first", "Saved", car_id="CAR001")
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
            seq=seq, sim_time=seq / 10, x=seq, y=24, state="driving", period_s=.1,
            distance_m=seq), seq, .1)

    def get(self, path, query=""):
        with urlopen(self.url + path + "?car_id=CAR001&" + query, timeout=10) as response:
            return json.loads(response.read())

    def test_optional_mission_and_standalone_share_both_frozen_ceilings(self):
        page = self.get("/api/car/history/page", "include_mission=1&include_overview=1&limit=1")
        self.sample(4)
        self.app.journal.record("decision", "later", "Later", car_id="CAR001")
        query = f"until_id={page['until_id']}&events_until_id={page['events_until_id']}"
        standalone = self.get("/api/car/history/mission", query)
        self.assertEqual(page["mission"], standalone)
        self.assertEqual(page["mission"]["receipt"]["sample_count"], page["overview"]["total_count"])
        self.assertEqual(page["mission"]["events"]["received_count"], page["event_total"])
        self.assertNotIn("mission", self.get("/api/car/history/page", query))

    def test_writer_commit_between_page_and_mission_keeps_same_snapshot(self):
        original = Journal.trip_mission
        committed = []
        def append_then_read(reader, *args, **kwargs):
            if not committed:
                self.sample(4)
                self.app.journal.record("decision", "later", "Later", car_id="CAR001")
                self.app.journal.flush()
                committed.append(True)
            return original(reader, *args, **kwargs)
        with patch.object(Journal, "trip_mission", append_then_read):
            result = self.get("/api/car/history/page", "include_mission=1&include_overview=1")
        self.assertEqual(result["mission"]["receipt"]["sample_count"], 3)
        self.assertEqual(result["mission"]["events"]["received_count"], 3)
        self.assertEqual(self.get("/api/car/history/mission")["receipt"]["sample_count"], 4)

    def test_invalid_mission_ceilings_identifiers_and_switch_return_400(self):
        for path, query in (("/api/car/history/mission", "until_id=-1"),
                ("/api/car/history/mission", "events_until_id=1.2"),
                ("/api/car/history/page", "include_mission=true")):
            with self.subTest(path=path, query=query), self.assertRaises(HTTPError) as failed:
                self.get(path, query)
            self.assertEqual(failed.exception.code, 400)
        with self.assertRaises(HTTPError) as failed:
            urlopen(self.url + "/api/car/history/mission?car_id=../../CAR001", timeout=5)
        self.assertEqual(failed.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
