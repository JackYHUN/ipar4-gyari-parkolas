"""Received observations remain replayable after rich-window rotation and reset."""
import copy
import csv
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib

from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT


class PerceptionArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run.sqlite3"
        self.journal = Journal(self.path, "run-a", max_telemetry=2)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    @staticmethod
    def bundle(sample_time=1.0):
        return dict(schema_version=1, source="virtual_onboard_bundle", sampled=True,
            sample_sim_time_s=sample_time, age_s=0, fresh=True, fault_active=False,
            pose={"x_m": 12.03, "y_m": 24.02, "heading_deg": 179.7},
            reference_pose={"x_m": 12, "y_m": 24, "heading_deg": 180},
            environment={"coverage": "radial_360_no_occlusion",
                "vehicles": [{"id": "CAR002", "x": 18.01, "y": 24.03, "distance_m": 5.98}],
                "crossings": [{"id": "Z1", "x": 24, "y": 24, "observed_pedestrian": True}]},
            guidance={"lateral_error_m": .02, "heading_error_deg": -.3},
            odometry={"speed_m_s": 2.0, "distance_m": 50.0},
            extra_diagnostic={"future_field": [1, 2, {"label": "Észlelés"}]})

    def sample(self, seq, sensors=None, *, car_id="CAR001", timestamp=None, control=None):
        data = dict(run_id="run-a", car_id=car_id, seq=seq, period_s=.1,
            sim_time=seq / 10 if timestamp is None else timestamp,
            speed=2, x=seq * 2, y=24, state="driving", route_progress_m=seq * 2,
            received_utc=f"2026-10-05T00:00:{seq:02d}.123Z")
        if sensors is not None:
            data["sensors"] = sensors
        if control is not None:
            data["control"] = control
        return self.journal.ingest(f"telemetry/{car_id}", data, seq * 1000, .25)

    def test_all_bundles_and_controller_decisions_survive_rotation_and_readonly_reopen(self):
        bundles = []
        for seq in range(1, 13):
            sensors = self.bundle(seq / 10 - .05)
            sensors["padding"] = "Észlelés " * 100
            bundles.append(sensors)
            self.sample(seq, sensors, control={"source": "ideal_route_controller", "decision_sim_time_s": seq / 10,
                "target_speed_m_s": 2, "zone_grants": ["C1"], "stop_limit_remaining_m": None})
        stats = self.journal.stats()
        self.assertEqual((stats["telemetry_records"], stats["trip_samples"], stats["perception_samples"]), (2, 12, 12))
        self.assertGreater(stats["perception_uncompressed_bytes"], stats["perception_compressed_bytes"])
        self.journal.close()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "run-a")
        try:
            result = reader.trip_history("CAR001")
            self.assertEqual([row["sensors"] for row in result["telemetry"]], bundles)
            self.assertEqual(result["telemetry"][0]["control"]["zone_grants"], ["C1"])
            self.assertEqual(result["telemetry"][0]["details"]["mqtt"]["received_ns"], 1000)
            self.assertEqual(result["summary"]["perception_samples"], 12)
            self.assertEqual(result["summary"]["perception_coverage"], 1)
            self.assertTrue(result["summary"]["perception_complete_received_archive"])
            self.assertEqual(reader.stats()["perception_compressed_bytes"], stats["perception_compressed_bytes"])
            with self.assertRaisesRegex(RuntimeError, "csak olvasható"):
                reader.ingest("telemetry/CAR001", {"x": 0}, 1, 0)
        finally:
            reader.close()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        with closing(sqlite3.connect(self.path)) as database:
            payload = database.execute("SELECT payload FROM trip_perception ORDER BY id LIMIT 1").fetchone()[0]
            self.assertEqual(json.loads(zlib.decompress(payload)), bundles[0])

    def test_stale_sensor_time_pose_and_environment_remain_exact_separate_from_receipt_context(self):
        fresh = self.bundle(7.0)
        self.sample(1, fresh, timestamp=7.05)
        stale = copy.deepcopy(fresh)
        stale.update(age_s=2.05, fresh=False, fault_active=True)
        self.sample(2, stale, timestamp=9.05, control={"decision_sim_time_s": 9.05, "stop_reason": "Elavult érzékelés"})
        history = self.journal.trip_history("CAR001")
        first, last = history["telemetry"]
        self.assertEqual((first["sim_time"], last["sim_time"]), (7.05, 9.05))
        self.assertNotEqual(first["x"], last["x"])
        self.assertEqual(last["sensors"]["pose"], fresh["pose"])
        self.assertEqual(last["sensors"]["environment"], fresh["environment"])
        self.assertEqual(last["sensors"]["sample_sim_time_s"], 7)
        self.assertEqual(last["sensors"]["age_s"], 2.05)
        self.assertEqual(last["control"]["decision_sim_time_s"], 9.05)
        self.assertEqual(history["summary"]["perception_distinct_samples"], 1)
        self.assertEqual(history["summary"]["perception_stale_frames"], 1)
        self.assertEqual(history["summary"]["perception_last_sim_time"], 9.05)
        self.assertEqual(history["summary"]["perception_last_sensor_sim_time"], 7)

    def test_fault_observer_topic_archives_cached_sensors_without_becoming_semantic_events(self):
        sensors = self.bundle(1.0)
        self.sample(1, sensors, timestamp=1.05)
        stale = copy.deepcopy(sensors)
        stale.update(age_s=1.05, fresh=False, fault_active=True)
        decision = {"source": "ideal_route_controller", "decision_sim_time_s": 2.05,
            "sensor_sample_sim_time_s": 1.0, "stop_reason": "Elavult érzékelés"}
        self.journal.ingest("diagnostics/perception/CAR001", {
            "run_id": "run-a", "car_id": "CAR001", "seq": None,
            "sim_time": 2.05, "x": 4, "y": 24, "speed": 0, "state": "sensor_stop",
            "source": "simulation_controller_diagnostics", "position_source": "post_step_simulation_reference",
            "period_s": .1, "sensors": stale, "control": decision}, 2000, .1)
        history = self.journal.trip_history("CAR001")
        self.assertEqual(len(history["telemetry"]), 2)
        self.assertEqual(history["events"], [])
        row = history["telemetry"][1]
        self.assertEqual(row["sensors"], stale)
        self.assertEqual(row["control"], decision)
        self.assertEqual(row["details"]["mqtt"]["topic"], "diagnostics/perception/CAR001")
        self.assertEqual(row["details"]["position_source"], "post_step_simulation_reference")
        self.assertIsNone(row["seq"])
        self.assertEqual(history["summary"]["perception_distinct_samples"], 1)
        self.assertEqual(history["summary"]["perception_stale_frames"], 1)

    def test_paged_prefix_coverage_counts_do_not_include_later_live_bundles(self):
        self.sample(1)
        self.sample(2, self.bundle(.15))
        self.sample(1, self.bundle(.15), car_id="CAR002")
        self.sample(3, self.bundle(.25))
        first = self.journal.trip_history("CAR001", limit=1)
        self.sample(4, self.bundle(.35))
        last = self.journal.trip_history("CAR001", limit=2, after=first["next_after"], until_id=first["until_id"])
        self.assertEqual([row["seq"] for row in last["telemetry"]], [2, 3])
        self.assertEqual(last["summary"]["sample_count"], 3)
        self.assertEqual(last["summary"]["perception_samples"], 2)
        self.assertAlmostEqual(last["summary"]["perception_coverage"], 2 / 3)
        self.assertEqual(last["summary"]["total_archived_perception_samples"], 3)
        self.assertEqual(last["summary"]["perception_last_sensor_sim_time"], .25)
        self.assertIsNone(first["telemetry"][0]["sensors"])
        self.assertEqual(self.journal.trip_history("CAR002")["summary"]["perception_samples"], 1)
        self.assertEqual(self.journal.trip_history("CAR001", until_id=0)["summary"]["perception_samples"], 0)

    def test_streamed_json_csv_preserve_nested_unknown_fields_controls_and_missing_observations(self):
        bundle = self.bundle()
        control = {"source": "ideal_route_controller", "stop_reason": "Gyalogos a zebrán", "zone_grants": ["Z1"]}
        self.sample(1, bundle, control=control)
        self.sample(2)
        self.journal.record("state", "stopped", "Megállás", car_id="CAR001", sim_time=.2)
        stream = self.journal.iter_trip_export("CAR001", "json")
        header = next(stream)
        self.sample(3, self.bundle(3))
        exported = json.loads(header + b"".join(stream))
        self.assertEqual(len(exported["telemetry"]), 2)
        self.assertEqual(exported["summary"]["perception_samples"], 1)
        self.assertEqual(exported["telemetry"][0]["sensors"], bundle)
        self.assertEqual(exported["telemetry"][0]["control"], control)
        self.assertIsNone(exported["telemetry"][1]["sensors"])
        self.assertFalse(exported["summary"]["camera_recording"])
        rows = list(csv.DictReader(io.StringIO(b"".join(self.journal.iter_trip_export("CAR001", "csv")).decode("utf-8-sig"))))
        self.assertEqual(json.loads(rows[0]["sensors"]), bundle)
        self.assertEqual(json.loads(rows[0]["control"]), control)
        self.assertEqual(rows[1]["sensors"], "null")
        self.assertEqual(rows[-1]["record_type"], "event")
        self.assertEqual(rows[-1]["sensors"], "null")

    def test_legacy_compact_history_recovers_only_retained_received_bundles_and_never_truth(self):
        for seq in range(1, 7):
            self.sample(seq, self.bundle(seq / 10))
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.executescript("DROP TABLE trip_perception; DELETE FROM metadata WHERE key LIKE 'perception%';")
            database.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "run-a")
        try:
            history = reader.trip_history("CAR001")
            self.assertEqual([row["sensors"] is None for row in history["telemetry"]], [True] * 4 + [False] * 2)
            self.assertEqual(history["summary"]["perception_samples"], 2)
            self.assertAlmostEqual(history["summary"]["perception_coverage"], 1 / 3)
            self.assertFalse(history["summary"]["perception_complete_received_archive"])
            exported = json.loads(b"".join(reader.iter_trip_export("CAR001", "json")))
            self.assertIsNone(exported["telemetry"][0]["sensors"])
            self.assertEqual(exported["telemetry"][-1]["sensors"], self.bundle(.6))
        finally:
            reader.close()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.journal = Journal(self.path, "run-a", max_telemetry=2)
        self.assertEqual(self.journal.stats()["perception_samples"], 2)
        self.assertEqual([row["sensors"] is None for row in self.journal.trip_history("CAR001")["telemetry"]], [True] * 4 + [False] * 2)

    def test_empty_or_missing_legacy_sensor_data_is_explicitly_unavailable(self):
        self.sample(1)
        self.sample(2, {"sampled": False, "sample_sim_time_s": None, "fresh": False, "pose": None})
        page = self.journal.trip_history("CAR001")
        self.assertIsNone(page["telemetry"][0]["sensors"])
        self.assertFalse(page["telemetry"][1]["sensors"]["sampled"])
        self.assertEqual(page["summary"]["perception_samples"], 1)
        self.assertEqual(page["summary"]["perception_distinct_samples"], 0)
        self.assertIsNone(page["summary"]["perception_first_sensor_sim_time"])
        unknown = self.journal.trip_history("CAR999")
        self.assertEqual(unknown["telemetry"], [])
        self.assertEqual(unknown["summary"]["perception_samples"], 0)
        self.assertIsNone(unknown["summary"]["perception_coverage"])
        self.assertFalse(unknown["summary"]["perception_complete_received_archive"])


class PerceptionRunIsolationTests(unittest.TestCase):
    def test_inflight_archive_export_remains_readable_when_live_run_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = ROOT / "config/default.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            app = Application(config, config_path)
            app.run_dir, app.run_id = Path(directory), "run-a"
            app.metadata = {"run_id": "run-a", "config": config}
            app.journal = Journal(app.run_dir / "run-a-journal.sqlite3", "run-a", max_telemetry=1)
            (app.run_dir / "run-a-metadata.json").write_text(json.dumps(app.metadata), encoding="utf-8")
            for seq in range(3):
                app.journal.ingest("telemetry/CAR001", {"car_id": "CAR001", "sim_time": seq / 10,
                    "x": seq, "sensors": PerceptionArchiveTests.bundle(seq / 10)}, seq, 0)
            download = app.trip_export("CAR001", "run-a", "json")
            head = next(download)
            app.journal.close()
            app.run_id = "run-b"
            app.metadata = {"run_id": "run-b", "config": config}
            app.journal = Journal(app.run_dir / "run-b-journal.sqlite3", "run-b")
            app.journal.ingest("telemetry/CAR001", {"car_id": "CAR001", "x": 999,
                "sensors": PerceptionArchiveTests.bundle(999)}, 100, 0)
            (app.run_dir / "run-b-metadata.json").write_text(json.dumps(app.metadata), encoding="utf-8")
            try:
                exported = json.loads(head + b"".join(download))
                self.assertEqual(exported["run_id"], "run-a")
                self.assertEqual([row["x"] for row in exported["telemetry"]], [0, 1, 2])
                self.assertEqual(exported["summary"]["perception_samples"], 3)
                old = app.trip_history("CAR001", "run-a")
                current = app.trip_history("CAR001", "run-b")
                self.assertIsNone(old["current"])
                self.assertEqual(current["telemetry"][0]["sensors"]["sample_sim_time_s"], 999)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
