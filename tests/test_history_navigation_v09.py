"""Bounded replay navigation reaches every received frame without a 20k ceiling."""
from contextlib import closing
import copy
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
import zlib

from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT, create_http_server


class HistoryNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run-a-journal.sqlite3"
        self.journal = Journal(self.path, "run-a", max_telemetry=2)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def sample(self, seq, timestamp=None, car_id="CAR001", *, sensors=None, fault=False):
        payload = dict(run_id="run-a", car_id=car_id, seq=None if fault else seq,
            sim_time=seq / 10 if timestamp is None else timestamp,
            x=seq, y=24, speed=0 if fault else 2, heading=0,
            state="sensor_stop" if fault else "driving", period_s=.1,
            received_utc="2026-10-07T08:00:00.123Z")
        if sensors is not None:
            payload["sensors"] = copy.deepcopy(sensors)
        topic = f"diagnostics/perception/{car_id}" if fault else f"telemetry/{car_id}"
        return self.journal.ingest(topic, payload, seq * 1000, .1)

    def test_bidirectional_pages_use_per_car_ordinals_without_duplicates(self):
        identifiers = []
        for seq in range(1, 9):
            identifiers.append(self.sample(seq))
            self.sample(seq, car_id="CAR002")
        first = self.journal.trip_history_page("CAR001", limit=3)
        self.assertEqual([row["id"] for row in first["telemetry"]], identifiers[:3])
        self.assertEqual((first["window"]["start_ordinal"], first["window"]["end_ordinal"],
                          first["window"]["total_count"]), (1, 3, 8))
        second = self.journal.trip_history_page("CAR001", direction="after",
            cursor=first["window"]["next_after_id"], until_id=first["until_id"], limit=3)
        self.assertEqual([row["id"] for row in second["telemetry"]], identifiers[3:6])
        prior = self.journal.trip_history_page("CAR001", direction="before",
            cursor=second["window"]["previous_before_id"], until_id=first["until_id"], limit=3)
        self.assertEqual([row["id"] for row in prior["telemetry"]], identifiers[:3])
        self.assertEqual(prior["target_id"], identifiers[2])
        end = self.journal.trip_history_page("CAR001", direction="end", limit=3)
        self.assertEqual([row["id"] for row in end["telemetry"]], identifiers[-3:])
        self.assertIsNone(end["window"]["next_after_id"])
        self.assertEqual(end["target_id"], identifiers[-1])

    def test_time_seek_chooses_exact_predecessor_with_duplicate_and_out_of_order_times(self):
        ids = [self.sample(seq, timestamp) for seq, timestamp in
               ((1, .1), (2, .2), (3, 5.2), (4, 5.2), (5, 4.8), (6, 8))]
        sought = self.journal.trip_history_page("CAR001", direction="time", sim_time=5.25, limit=2)
        self.assertEqual(sought["target_id"], ids[3])
        self.assertEqual(sought["telemetry"][0]["sim_time"], 5.2)
        self.assertEqual(sought["window"]["start_ordinal"], 4)
        self.assertEqual(sought["seek"]["resolution"], "predecessor")
        in_gap = self.journal.trip_history_page("CAR001", direction="time", sim_time=3)
        self.assertEqual(in_gap["target_id"], ids[1])
        self.assertEqual(in_gap["telemetry"][0]["sim_time"], .2)
        early = self.journal.trip_history_page("CAR001", direction="time", sim_time=0)
        self.assertEqual(early["target_id"], ids[0])
        self.assertFalse(early["seek"]["predecessor_found"])
        self.assertEqual(early["seek"]["resolution"], "earliest_fallback")
        late = self.journal.trip_history_page("CAR001", direction="time", sim_time=99)
        self.assertEqual(late["target_id"], ids[-1])
        exact = self.journal.trip_history_page("CAR001", direction="time", sim_time=5.2)
        self.assertEqual(exact["target_id"], ids[3])

    def test_fixed_ceiling_excludes_new_rows_and_preserves_sensor_fault_observations(self):
        sensed = dict(schema_version=1, source="virtual_onboard_bundle", sampled=True,
            sample_sim_time_s=.1, fresh=True, fault_active=False, age_s=0,
            pose={"x_m": 1, "y_m": 24, "heading_deg": 0})
        first_id = self.sample(1, sensors=sensed)
        held = dict(sensed, fresh=False, fault_active=True, age_s=.2)
        fault_id = self.sample(3, sensors=held, fault=True)
        first = self.journal.trip_history_page("CAR001", limit=1)
        self.sample(4, sensors=dict(sensed, sample_sim_time_s=.4))
        end = self.journal.trip_history_page("CAR001", direction="end", until_id=first["until_id"])
        self.assertEqual(end["until_id"], fault_id)
        self.assertEqual(end["window"]["total_count"], 2)
        self.assertEqual(end["summary"]["sample_count"], 2)
        self.assertEqual(end["summary"]["total_archived_sample_count"], 3)
        self.assertEqual(end["telemetry"][-1]["sensors"], held)
        self.assertIsNone(end["telemetry"][-1]["seq"])
        seek = self.journal.trip_history_page("CAR001", direction="time", sim_time=.35,
                                              until_id=first["until_id"])
        self.assertEqual(seek["target_id"], fault_id)
        self.assertNotEqual(seek["target_id"], first_id)
        self.assertEqual(seek["telemetry"][0]["sensors"]["sample_sim_time_s"], .1)

    def test_empty_event_only_vehicle_has_no_invented_target(self):
        self.journal.record("lifecycle", "admitted", "Belépett", car_id="CAR999", sim_time=1)
        for direction in ("start", "end", "time"):
            filters = dict(direction=direction)
            if direction == "time":
                filters["sim_time"] = 1
            result = self.journal.trip_history_page("CAR999", **filters)
            self.assertEqual(result["telemetry"], [])
            self.assertEqual(result["window"]["total_count"], 0)
            self.assertIsNone(result["target_id"])
            self.assertEqual(len(result["events"]), 1)

    def test_invalid_or_foreign_navigation_parameters_fail_explicitly(self):
        first = self.sample(1)
        foreign = self.sample(1, car_id="CAR002")
        invalid = [dict(direction="sideways"), dict(direction="after"), dict(direction="before", cursor=0),
            dict(direction="after", cursor=foreign), dict(direction="after", cursor=first, until_id=0),
            dict(direction="time", sim_time="NaN"), dict(direction="time", sim_time="inf"),
            dict(direction="time", sim_time=-1), dict(direction="time", sim_time=True),
            dict(direction="time", sim_time=.1, cursor=first), dict(direction="start", sim_time=1),
            dict(direction="end", cursor=first), dict(limit=0), dict(limit="2.5"), dict(limit=True), dict(limit=None),
            dict(until_id=-1), dict(until_id=9_007_199_254_740_992)]
        for filters in invalid:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.journal.trip_history_page("CAR001", **filters)
        self.assertEqual(self.journal.trip_history_page("CAR001", limit="10000")["window"]["page_limit"], 2000)

    def test_readonly_legacy_seek_keeps_evidence_unchanged_and_recovers_gap(self):
        for seq, timestamp in ((1, .1), (2, .2), (4, 5.2)):
            self.sample(seq, timestamp)
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.executescript("DROP TABLE trip_samples; DROP TABLE trip_cars; DROP TABLE trip_perception; "
                                  "DROP INDEX telemetry_car_time; "
                                  "DELETE FROM metadata WHERE key LIKE 'compact_history%';")
            database.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "run-a")
        try:
            result = reader.trip_history_page("CAR001", direction="time", sim_time=5.3)
            self.assertEqual(result["telemetry"][0]["seq"], 4)
            self.assertEqual(result["telemetry"][0]["gap_before"], 1)
            self.assertEqual(result["telemetry"][0]["time_gap_before_s"], 5)
            self.assertFalse(result["summary"]["complete_received_archive"])
            self.assertEqual(result["window"]["total_count"], 2)
        finally:
            reader.close()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_large_received_archive_reaches_frames_beyond_20k_with_bounded_decompression(self):
        total = 20_025
        sensors = dict(sampled=True, fresh=True, fault_active=False, sample_sim_time_s=0,
                       retained_observation={"marker": "large-received-run"})
        for seq in range(1, total + 1):
            self.sample(seq, sensors=dict(sensors, sample_sim_time_s=seq / 10))
            if seq % 100 == 0:
                self.sample(seq, car_id="CAR002")
        first = self.journal.trip_history_page("CAR001", limit=2000)
        self.sample(total + 1, sensors=sensors)
        fixed_ceiling = first["until_id"]
        received, page = [], first
        while True:
            received.extend(row["seq"] for row in page["telemetry"])
            cursor = page["window"]["next_after_id"]
            if cursor is None:
                break
            page = self.journal.trip_history_page("CAR001", direction="after", cursor=cursor,
                                                 limit=2000, until_id=fixed_ceiling)
        self.assertEqual(received, list(range(1, total + 1)))
        self.assertEqual(page["window"]["end_ordinal"], total)
        with patch("factory_yard.journal.zlib.decompress", wraps=zlib.decompress) as decompression:
            sought = self.journal.trip_history_page("CAR001", direction="time", sim_time=2000.55,
                                                    until_id=fixed_ceiling, limit=128)
            self.assertLessEqual(decompression.call_count, 128)
        self.assertEqual(sought["telemetry"][0]["seq"], 20_005)
        self.assertEqual(sought["window"]["start_ordinal"], 20_005)
        self.assertEqual(sought["window"]["end_ordinal"], total)
        self.assertEqual(sought["telemetry"][0]["sensors"]["retained_observation"]["marker"], "large-received-run")
        end = self.journal.trip_history_page("CAR001", direction="end", limit=128, until_id=fixed_ceiling)
        self.assertEqual((end["window"]["start_ordinal"], end["window"]["end_ordinal"]), (total - 127, total))
        self.assertIsNone(end["window"]["next_after_id"])


class HistoryNavigationAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "page-run"
        self.app.journal = Journal(self.app.run_dir / "page-run-journal.sqlite3", "page-run", max_telemetry=2)
        self.app.metadata = dict(run_id="page-run", seed=7, config=config, model_version="saved-page-fixture")
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(cars=[], metrics={}, warnings=[])
        for seq in range(1, 7):
            self.app.journal.ingest("telemetry/CAR001", dict(run_id="page-run", car_id="CAR001",
                seq=seq, sim_time=seq / 10, x=seq, y=24, state="driving", period_s=.1), seq, .1)
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

    def get(self, query):
        with urlopen(self.url + "/api/car/history/page?car_id=CAR001&" + query, timeout=5) as response:
            return json.loads(response.read())

    def test_http_time_seek_returns_saved_configuration_metadata_and_global_window(self):
        result = self.get("run_id=page-run&direction=time&sim_time=0.45&limit=2")
        self.assertEqual([row["seq"] for row in result["telemetry"]], [4, 5])
        self.assertEqual(result["target_id"], result["telemetry"][0]["id"])
        self.assertEqual((result["window"]["start_ordinal"], result["window"]["end_ordinal"]), (4, 5))
        self.assertEqual(result["metadata"]["model_version"], "saved-page-fixture")
        self.assertEqual(result["map"]["width"], 140)
        self.assertEqual(result["config"], self.app.config)
        end = self.get("direction=end&limit=2")
        self.assertEqual([row["seq"] for row in end["telemetry"]], [5, 6])

    def test_http_invalid_seek_and_identifiers_return_400(self):
        for query in ("direction=time&sim_time=NaN", "direction=time&sim_time=-1", "direction=after&cursor=1.5",
                      "direction=time&sim_time=.2&cursor=1", "direction=start&sim_time=.2",
                      "direction=after", "direction=before&cursor=999", "until_id=-1",
                      "direction=invalid", "limit=0", "run_id=../../bad"):
            with self.subTest(query=query), self.assertRaises(HTTPError) as failed:
                self.get(query)
            self.assertEqual(failed.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
