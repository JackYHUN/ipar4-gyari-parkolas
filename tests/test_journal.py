"""Evidence preservation, query isolation and durable journal regressions."""
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import unittest
from unittest.mock import patch

from factory_yard.journal import Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run.sqlite3"
        self.journal = Journal(self.path, "run-42", max_telemetry=3)

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def test_transport_and_decision_correlation_survive_ingestion(self):
        payload = dict(run_id="run-42", car_id="CAR001", sim_time=12.5, kind="parking_grant",
                       request_id="request-1", reservation_id="reservation-2", slot_id="P01",
                       producer_seq=18, message_id="msg-18", sent_ns=100)
        self.journal.ingest("commands/CAR001", payload, 300, 0.0002)
        item = self.journal.query()["items"][0]
        self.assertEqual((item["category"], item["event_type"], item["car_id"]),
                         ("decision", "parking_grant", "CAR001"))
        self.assertEqual(item["sim_time"], 12.5)
        self.assertTrue(item["utc"].endswith("Z"))
        for field in ("request_id", "reservation_id", "slot_id", "producer_seq", "message_id", "sent_ns"):
            self.assertEqual(item["details"][field], payload[field])
        self.assertEqual(item["details"]["mqtt"]["topic"], "commands/CAR001")
        self.assertEqual(item["details"]["mqtt"]["latency_ms"], 0.0002)
        self.assertEqual(item["details"]["mqtt"]["received_utc"], item["utc"])

    def test_embedded_event_lists_and_wrong_run_do_not_duplicate_evidence(self):
        event = dict(run_id="run-42", type="parked", car_id="CAR001", sim_time=9, message="Beparkolt")
        self.journal.ingest("events", event, 100, 0)
        for _ in range(3):
            self.journal.ingest("snapshot", dict(run_id="run-42", events=[event]), 100, 0)
            self.journal.ingest("coordinator", dict(run_id="run-42", events=[event]), 100, 0)
        self.journal.ingest("events", dict(event, run_id="old-run"), 100, 0)
        self.assertEqual(self.journal.stats()["records"], 1)

    def test_fault_severity_and_explicit_audit_details_are_preserved(self):
        self.journal.ingest("events", dict(type="collision", car_id="CAR001", sim_time=4), 100, 0)
        self.journal.ingest("audit", dict(event_type="request_rejected", category="protocol", level="warn",
            message="Elavult kérés", car_id="CAR002", request_id="req", details={"reason": "stale", "age_s": 2.5}), 200, 0)
        items = self.journal.query()["items"]
        self.assertEqual((items[0]["category"], items[0]["level"]), ("transport", "warning"))
        self.assertEqual(items[0]["details"]["reason"], "stale")
        self.assertEqual(items[0]["details"]["request_id"], "req")
        self.assertEqual((items[1]["category"], items[1]["level"]), ("fault", "error"))

    def test_paging_is_exclusive_and_total_is_global_for_current_filter(self):
        ids = []
        for index in range(7):
            ids.append(self.journal.record("state", "state_changed", f"Állapot {index}",
                car_id="CAR001" if index % 2 == 0 else "CAR002", details={"index": index}))
        first = self.journal.query(limit=2, car_id="CAR001")
        self.assertEqual(first["total"], 4)
        self.assertEqual([item["id"] for item in first["items"]], [ids[6], ids[4]])
        second = self.journal.query(limit=2, car_id="CAR001", before=first["next_before"])
        self.assertEqual(second["total"], 4)
        self.assertEqual([item["id"] for item in second["items"]], [ids[2], ids[0]])
        self.assertIsNone(second["next_before"])

    def test_search_is_literal_and_sql_fragments_are_not_executed(self):
        self.journal.record("decision", "parking_wait", "100% telítettség", details={"reason": "nincs_hely"})
        self.journal.record("decision", "parking_wait", "100 jármű", details={"reason": "nincsXhely"})
        self.assertEqual(self.journal.query(search="100%")["total"], 1)
        self.assertEqual(self.journal.query(search="nincs_hely")["total"], 1)
        self.assertEqual(self.journal.query(car_id="' OR 1=1 --")["total"], 0)
        with self.assertRaises(ValueError):
            self.journal.query(before="1 OR 1=1")
        self.assertEqual(self.journal.stats()["records"], 2)

    def test_telemetry_retention_is_bounded_disclosed_and_survives_reopen(self):
        for index in range(5):
            self.journal.ingest("telemetry/CAR001", dict(run_id="run-42", car_id="CAR001", seq=index,
                sim_time=index * .1, speed=index, x=index * 2, y=24, state="driving"), 100 + index, .2)
        stats = self.journal.stats()
        self.assertEqual((stats["telemetry_records"], stats["telemetry_total_received"], stats["telemetry_truncated"]),
                         (3, 5, 2))
        history = self.journal.car_history("CAR001", limit=1000)
        self.assertEqual([sample["speed"] for sample in history["telemetry"]], [2, 3, 4])
        self.assertEqual(history["events"], [])
        self.journal.close()
        self.journal = Journal(self.path, "run-42", max_telemetry=3)
        self.assertEqual(self.journal.stats()["telemetry_truncated"], 2)
        self.assertEqual(len(self.journal.car_history("CAR001")["telemetry"]), 3)

    def test_flushed_and_closed_records_are_visible_to_an_external_reader(self):
        self.journal.record("control", "control_requested", "Megállítás", details={"control_id": "ctrl-1"})
        self.journal.flush()
        with closing(sqlite3.connect(self.path)) as reader:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
        self.journal.record("control", "control_applied", "Szüneteltetve", details={"control_id": "ctrl-1"})
        self.journal.close()
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as reader:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)

    def test_concurrent_http_and_observer_writes_keep_unique_complete_records(self):
        def write(index):
            return self.journal.record("lifecycle", "arrival", f"Érkezés {index}", car_id=f"CAR{index:03}")
        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(write, range(120)))
        self.assertEqual(len(set(ids)), 120)
        self.assertEqual(self.journal.query(limit=500)["total"], 120)

    def test_snapshot_heartbeats_commit_pending_controls_during_pause(self):
        with patch("factory_yard.journal.time.monotonic", return_value=100):
            self.journal.flush()
            self.journal.record("control", "control_applied", "Szüneteltetve")
        with closing(sqlite3.connect(self.path)) as reader:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)
        with patch("factory_yard.journal.time.monotonic", return_value=100.6):
            self.journal.ingest("snapshot", dict(run_id="run-42", paused=True), 200, .1)
        with closing(sqlite3.connect(self.path)) as reader:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM records").fetchone()[0], 1)
        self.assertEqual(self.journal.stats()["records"], 1)

    def test_callback_utc_is_preserved_when_journal_ingestion_is_delayed(self):
        receipt_utc = "2026-10-04T18:04:05.123Z"
        processing_utc = "2026-10-04T18:04:12.456Z"
        with patch("factory_yard.journal._utc_now", return_value=processing_utc):
            self.journal.ingest("events", dict(run_id="run-42", type="parked", car_id="CAR001",
                sim_time=24.5, received_utc=receipt_utc), 1000, .2)
            self.journal.ingest("telemetry/CAR001", dict(run_id="run-42", car_id="CAR001", seq=2,
                sim_time=24.5, speed=0, x=32, y=24, state="parked", received_utc=receipt_utc), 1001, .3)
        row = self.journal.query()["items"][0]
        self.assertEqual(row["utc"], receipt_utc)
        self.assertEqual(row["details"]["mqtt"]["received_utc"], receipt_utc)
        self.assertEqual(row["details"]["mqtt"]["received_ns"], 1000)
        self.assertEqual(self.journal.car_history("CAR001")["telemetry"][0]["utc"], receipt_utc)
        exported = json.loads(self.journal.export("jsonl").decode().strip())
        self.assertEqual(exported["utc"], receipt_utc)
        self.assertNotIn(processing_utc, self.journal.export("jsonl").decode())

    def test_exports_preserve_unicode_nested_details_and_order(self):
        self.journal.record("decision", "zone_wait", 'Gyalogos: "elsőbbség", várakozás',
                            car_id="CAR001", sim_time=1.2, details={"zone_id": "C1", "positions": [[1, 2], [3, 4]]})
        self.journal.record("decision", "zone_grant", "Engedély", car_id="CAR001", sim_time=2.4)
        json_rows = [json.loads(line) for line in self.journal.export("jsonl").decode().splitlines()]
        self.assertEqual([row["event_type"] for row in json_rows], ["zone_wait", "zone_grant"])
        self.assertEqual(json_rows[0]["run_id"], "run-42")
        self.assertEqual(json_rows[0]["details"]["positions"], [[1, 2], [3, 4]])
        csv_rows = list(csv.DictReader(io.StringIO(self.journal.export("csv").decode("utf-8-sig"))))
        self.assertEqual(csv_rows[0]["message"], json_rows[0]["message"])
        self.assertEqual(json.loads(csv_rows[0]["details"]), json_rows[0]["details"])
        with self.assertRaises(ValueError):
            self.journal.export("zip")

    def test_nonfinite_measurements_are_unknown_not_invalid_json(self):
        self.journal.record("fault", "invalid_sample", "Érvénytelen minta", sim_time=float("nan"),
                            details={"x": float("inf"), "samples": [float("nan"), 1]})
        row = self.journal.query()["items"][0]
        self.assertIsNone(row["sim_time"])
        self.assertIsNone(row["details"]["x"])
        self.assertEqual(row["details"]["samples"], [None, 1])
        self.assertNotIn("NaN", self.journal.export().decode())

    def test_a_different_run_cannot_append_to_existing_evidence(self):
        self.journal.close()
        with self.assertRaisesRegex(ValueError, "másik futáshoz"):
            Journal(self.path, "another-run")
        self.journal = Journal(self.path, "run-42", max_telemetry=3)

    def test_retained_telemetry_export_preserves_units_sequence_and_clocks(self):
        for seq in range(5):
            self.journal.ingest("telemetry/CAR001", dict(run_id="run-42", car_id="CAR001", seq=seq,
                sim_time=seq * .1, speed=2.5, x=10, y=24, state="driving", source="virtual_pose_sensor",
                message_id=f"msg-{seq}", producer_seq=seq + 10, units={"position": "m", "speed": "m/s"},
                position_uncertainty_m=.1), 1000 + seq, .25)
        rows = list(csv.DictReader(io.StringIO(self.journal.export_telemetry().decode("utf-8-sig"))))
        self.assertEqual([row["seq"] for row in rows], ["2", "3", "4"])
        self.assertEqual(rows[0]["topic"], "telemetry/CAR001")
        self.assertEqual(rows[0]["message_id"], "msg-2")
        self.assertTrue(rows[0]["utc"].endswith("Z"))
        self.assertEqual(float(rows[0]["sim_time"]), .2)
        self.assertEqual(float(rows[0]["speed"]), 2.5)
        details = json.loads(rows[0]["details"])
        self.assertEqual(details["units"], {"position": "m", "speed": "m/s"})
        self.assertEqual(details["position_uncertainty_m"], .1)
        self.assertEqual(self.journal.stats()["telemetry_truncated"], 2)


if __name__ == "__main__":
    unittest.main()
