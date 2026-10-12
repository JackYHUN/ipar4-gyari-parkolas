"""Encoding reuse preserves the received archive contract and scalar semantics."""
from contextlib import closing
import copy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zlib

from factory_yard import journal as module
from factory_yard.journal import Journal
from factory_yard.server import ROOT


def legacy_encode(value):
    """Frozen pre-v12 JSON contract, independent of the new fast encoder."""
    def normalise(item):
        if isinstance(item, float) and not math.isfinite(item):
            return None
        if isinstance(item, dict):
            return {str(key): normalise(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalise(child) for child in item]
        return item
    return json.dumps(normalise(value), ensure_ascii=False, separators=(",", ":"),
                      default=str, allow_nan=False)


class JournalEncodingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "results")
        self.path = Path(self.tmp.name) / "encode.sqlite3"
        self.journal = Journal(self.path, "encode", max_telemetry=2)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    @staticmethod
    def sensors():
        return dict(sampled=True, fresh=True, fault_active=False, sample_sim_time_s=1.,
            label="Árvíztűrő tükörfúrógép 🚗", nested={7: (float("nan"), float("inf"), -0.0),
                None: [float("-inf"), {"x": 2, "tuple": (1, "érték")}],
                "7": {"same_normalised_key": "later value wins"}},
            reference_pose={"x_m": 10., "y_m": 24.})

    def payload(self, sensors=None, **changes):
        result = dict(run_id="encode", car_id="CAR001", seq=1, sim_time=1., speed=2., x=10., y=24.,
            state="driving", received_utc="2026-10-09T12:00:00.123Z", period_s=.1,
            control={"source": "ideal_route_controller", "diagnostic": (float("nan"), "Ő")},
            details={"extra": {2: (3, "😀"), "invalid": float("inf")}})
        if sensors is not None:
            result["sensors"] = sensors
        result.update(changes)
        return result

    def test_rich_details_and_sensor_bytes_equal_frozen_legacy_contract(self):
        sensors = self.sensors()
        payload = self.payload(sensors)
        expected_details = legacy_encode(self.journal._details("telemetry/CAR001", payload, 123456, .125,
            payload["received_utc"]))
        expected_sensor_bytes = legacy_encode(sensors).encode("utf-8")
        row_id = self.journal.ingest("telemetry/CAR001", payload, 123456, .125)
        details = self.journal._connection.execute("SELECT details FROM telemetry WHERE id=?", (row_id,)).fetchone()[0]
        stored = self.journal._connection.execute("SELECT payload,uncompressed_bytes FROM trip_perception WHERE id=?", (row_id,)).fetchone()
        self.assertEqual(details.encode("utf-8"), expected_details.encode("utf-8"))
        self.assertEqual(zlib.decompress(stored["payload"]), expected_sensor_bytes)
        self.assertEqual(stored["payload"], zlib.compress(expected_sensor_bytes, level=3))
        self.assertEqual(stored["uncompressed_bytes"], len(expected_sensor_bytes))
        self.assertNotIn("NaN", details)
        self.assertNotIn("Infinity", details)
        decoded = json.loads(expected_sensor_bytes)
        self.assertEqual(decoded["nested"]["7"], {"same_normalised_key": "later value wins"})

    def test_sensor_normalisation_occurs_once_and_input_is_immutable(self):
        sensors = self.sensors()
        payload = self.payload(sensors)
        before = copy.deepcopy(payload)
        normalise = module._json_safe
        sensor_calls = []
        def counted(value):
            if value is sensors:
                sensor_calls.append(value)
            return normalise(value)
        with patch.object(module, "_json_safe", side_effect=counted):
            self.journal.ingest("telemetry/CAR001", payload, 1, .1)
        self.assertEqual(len(sensor_calls), 1)
        self.assertEqual(payload, before)
        self.assertIs(payload["sensors"], sensors)
        self.assertIsInstance(sensors["nested"][7], tuple)
        self.assertTrue(math.isnan(sensors["nested"][7][0]))

    def test_top_level_key_string_collision_cannot_replace_original_sensor_archive(self):
        class SensorKey:
            def __str__(self):
                return "sensors"
        sensors = self.sensors()
        payload = self.payload(sensors)
        payload["details"] = {"sensors": sensors, SensorKey(): {"different": "legacy collision"}}
        row_id = self.journal.ingest("telemetry/CAR001", payload, 1, .1)
        row = self.journal._connection.execute("SELECT details FROM telemetry WHERE id=?", (row_id,)).fetchone()
        self.assertEqual(json.loads(row["details"])["sensors"], {"different": "legacy collision"})
        archived = self.journal._connection.execute("SELECT payload FROM trip_perception WHERE id=?", (row_id,)).fetchone()
        self.assertEqual(zlib.decompress(archived["payload"]), legacy_encode(sensors).encode("utf-8"))

    def test_original_truthiness_flags_survive_normalised_sensor_bytes(self):
        cases = [({"sampled": True, "fresh": float("nan"), "fault_active": False}, (1, 1)),
            ({"sampled": True, "fresh": True, "fault_active": float("nan")}, (1, 0)),
            ({"sampled": "false", "fresh": [], "fault_active": None}, (1, 0)),
            ({"sampled": False, "fresh": True, "fault_active": False}, (0, 1))]
        for index, (flags, expected) in enumerate(cases, 1):
            with self.subTest(flags=repr(flags)):
                sensors = dict(flags, sample_sim_time_s=float("nan"))
                row_id = self.journal.ingest("telemetry/CAR001", self.payload(sensors, seq=index), index, .1)
                row = self.journal._connection.execute("SELECT sampled,fresh,sensor_sim_time,payload FROM trip_perception WHERE id=?", (row_id,)).fetchone()
                self.assertEqual((row["sampled"], row["fresh"]), expected)
                self.assertIsNone(row["sensor_sim_time"])
                self.assertEqual(zlib.decompress(row["payload"]), legacy_encode(sensors).encode("utf-8"))

    def test_direct_perception_archive_retains_independent_normalisation_path(self):
        sensors = self.sensors()
        sample = dict(id=1, car_id="CAR001", utc="2026-10-09T12:00:00Z", sim_time=1)
        self.journal._archive_perception(sample, sensors)
        row = self.journal._connection.execute("SELECT payload FROM trip_perception WHERE id=1").fetchone()
        self.assertEqual(zlib.decompress(row["payload"]), legacy_encode(sensors).encode("utf-8"))
        self.assertIsNone(self.journal._archive_perception(dict(sample, id=2), ["not a bundle"]))
        self.assertEqual(self.journal._connection.execute("SELECT COUNT(*) FROM trip_perception").fetchone()[0], 1)

    def test_missing_and_non_dictionary_sensors_do_not_create_sensor_rows(self):
        for index, sensors in enumerate((None, [], "unknown", 7), 1):
            self.journal.ingest("telemetry/CAR001", self.payload(sensors, seq=index), index, .1)
        self.assertEqual(self.journal.stats()["trip_samples"], 4)
        self.assertEqual(self.journal.stats()["perception_samples"], 0)
        self.assertEqual(self.journal._connection.execute("SELECT COUNT(*) FROM trip_perception").fetchone()[0], 0)

    def test_stale_repeats_fault_diagnostics_and_events_keep_every_received_row(self):
        bundle = self.sensors()
        expected = []
        for index in range(1, 8):
            sensors = dict(bundle, fresh=index == 1, fault_active=index > 1)
            expected.append(json.loads(legacy_encode(sensors)))
            topic = "telemetry/CAR001" if index == 1 else "diagnostics/perception/CAR001"
            self.journal.ingest(topic, self.payload(sensors, seq=index if index == 1 else None,
                sim_time=index / 10), 123000 + index, .2)
        self.journal.ingest("events", dict(run_id="encode", car_id="CAR001", type="sensor_failure",
            sim_time=1, received_utc="2026-10-09T12:00:00Z", note="Árvíz"), 123008, .2)
        history = self.journal.trip_history("CAR001")
        self.assertEqual([row["sensors"] for row in history["telemetry"]], expected)
        self.assertEqual([row["details"]["mqtt"]["received_ns"] for row in history["telemetry"]], list(range(123001, 123008)))
        self.assertEqual(history["summary"]["perception_stale_frames"], 6)
        self.assertEqual(history["summary"]["perception_distinct_samples"], 1)
        self.assertEqual(history["events"][0]["event_type"], "sensor_failure")
        self.assertEqual(self.journal.stats()["telemetry_records"], 2)
        self.assertEqual(self.journal.stats()["trip_samples"], 7)

    def test_old_rich_rows_migrate_using_default_perception_encoding(self):
        sensors = self.sensors()
        self.journal.ingest("telemetry/CAR001", self.payload(sensors), 1, .1)
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.executescript("DROP TABLE trip_perception; DELETE FROM metadata WHERE key LIKE 'perception_%';")
            database.commit()
        self.journal = Journal(self.path, "encode", max_telemetry=2)
        row = self.journal._connection.execute("SELECT payload FROM trip_perception WHERE id=1").fetchone()
        self.assertEqual(zlib.decompress(row["payload"]), legacy_encode(sensors).encode("utf-8"))
        self.assertEqual(self.journal.stats()["perception_samples"], 1)

    def test_readonly_old_evidence_bytes_unchanged_and_new_encoder_strict(self):
        self.journal.ingest("telemetry/CAR001", self.payload(self.sensors()), 1, .1)
        self.journal.close()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "encode", snapshot=True)
        try:
            self.assertEqual(reader.trip_history("CAR001")["summary"]["perception_samples"], 1)
        finally:
            reader.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        with self.assertRaises(ValueError):
            module._encode_safe({"unsanitised": float("nan")})


if __name__ == "__main__":
    unittest.main()
