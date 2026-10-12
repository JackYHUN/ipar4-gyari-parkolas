"""Durable application/API evidence, pagination, correlation and exports."""
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile
from factory_yard.journal import Journal
from factory_yard.server import Application, ROOT, create_http_server


class LoggingAPITests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "api-test"
        self.app.journal = Journal(self.app.run_dir / "test.sqlite3", self.app.run_id)
        self.app.metadata = {"run_id": self.app.run_id, "seed": 42, "strategy": "nearest"}
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(sim_time=2.0, paused=False, cars=[dict(id="CAR001", speed=1.0)], metrics={}, warnings=[])
        self.app.last_snapshot_ns = time.perf_counter_ns()
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
        with urlopen(self.url + path, timeout=3) as response:
            return response.read(), response.headers

    def test_filter_and_exclusive_pagination_return_matching_records_only(self):
        for car_id in ("CAR001", "CAR002", "CAR001"):
            self.app.journal.record("decision", "parking_granted", "Útvonal alapján engedélyezve.",
                                    car_id=car_id, sim_time=2, details={"reservation_id": "r-1"})
        raw, _ = self.get("/api/logs?car_id=CAR001&limit=1&category=decision&search=enged")
        first = json.loads(raw)
        self.assertEqual(first["total"], 2)
        self.assertEqual(len(first["items"]), 1)
        raw, _ = self.get(f'/api/logs?car_id=CAR001&limit=1&before={first["next_before"]}')
        second = json.loads(raw)
        self.assertLess(second["items"][0]["id"], first["items"][0]["id"])

    def test_car_history_is_received_telemetry_and_survives_current_car_removal(self):
        now = time.perf_counter_ns()
        for seq in (1, 2):
            self.app._accept("telemetry/CAR001", dict(run_id=self.app.run_id, sent_ns=now, car_id="CAR001",
                seq=seq, sim_time=seq, speed=seq / 2, x=10+seq, y=58, state="driving"), now)
        raw, _ = self.get("/api/car?car_id=CAR001")
        result = json.loads(raw)
        self.assertEqual([row["speed"] for row in result["telemetry"]], [.5, 1])
        self.assertEqual(result["current"]["id"], "CAR001")
        self.app.latest["cars"] = []
        raw, _ = self.get("/api/car?car_id=CAR001")
        result = json.loads(raw)
        self.assertIsNone(result["current"])
        self.assertEqual(len(result["telemetry"]), 2)

    def test_published_control_and_applied_receipt_share_correlation_id(self):
        result = self.app.control({"action": "pause"})
        payload = self.app.bus.publish.call_args.args[1]
        self.assertEqual(payload["control_id"], result["control_id"])
        now = time.perf_counter_ns()
        self.app._accept("events", dict(run_id=self.app.run_id, sent_ns=now, type="control_applied",
            category="control", control_id=result["control_id"], action="pause", sim_time=2,
            message="Kezelőparancs alkalmazva: pause"), now)
        rows = self.app.logs(category="control")["items"]
        self.assertTrue({"control_requested", "control_published", "control_applied"} <= {r["event_type"] for r in rows})
        self.assertTrue(all(result["control_id"] in json.dumps(row["details"]) for row in rows))

    def test_rejected_offline_control_is_persisted_without_publication(self):
        self.app.fault_started_ns = time.perf_counter_ns()
        with self.assertRaises(ValueError):
            self.app.control({"action": "speed", "value": 2})
        self.app.bus.publish.assert_not_called()
        record = self.app.logs(level="warning")["items"][0]
        self.assertEqual(record["event_type"], "control_rejected")

    def test_unacknowledged_fault_command_is_rejected_and_does_not_latch_offline(self):
        self.app.bus.publish.return_value.is_published.return_value = False
        with self.assertRaisesRegex(RuntimeError, "nem igazolta"):
            self.app.control({"action": "disconnect", "seconds": 5})
        self.assertEqual(self.app.fault_started_ns, 0)
        self.assertEqual(self.app.logs(level="warning")["items"][0]["event_type"], "control_rejected")

    def test_zip_contains_matching_run_evidence_units_metadata_and_telemetry(self):
        self.app.journal.record("state", "parked", "Parkolás kész.", car_id="CAR001", sim_time=2)
        now = time.perf_counter_ns()
        self.app._accept("telemetry/CAR001", dict(run_id=self.app.run_id, sent_ns=now, car_id="CAR001",
            sim_time=2, seq=1, speed=0, x=40, y=12, state="parked"), now)
        raw, headers = self.get("/api/export?format=zip")
        self.assertIn("yard-api-test.zip", headers["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertTrue({"metadata.json", "events.jsonl", "events.csv", "telemetry.csv", "state.json", "README.txt"} <= set(archive.namelist()))
            meta = json.loads(archive.read("metadata.json"))
            self.assertEqual(meta["run_id"], self.app.run_id)
            rows = list(csv.DictReader(io.StringIO(archive.read("telemetry.csv").decode("utf-8-sig"))))
            self.assertEqual(rows[0]["car_id"], "CAR001")

    def test_invalid_query_and_export_are_client_errors(self):
        for path in ("/api/logs?limit=bad", "/api/logs?before=bad", "/api/export?format=exe"):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 400)

    def test_http_rejects_foreign_control_origin(self):
        request = Request(self.url + "/api/control", data=b'{"action":"pause"}',
                          headers={"Content-Type": "application/json", "Origin": "https://example.org"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)
        self.app.bus.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
