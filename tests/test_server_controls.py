"""Regressions for observable controls and completeness of measurement logs."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from factory_yard.server import Application, ROOT


class ServerControlTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_id = "test"
        self.app.bus = Mock()
        self.app.bus.connected = threading.Event()
        self.app.bus.connected.set()
        self.app.bus.published = self.app.bus.invalid = 0
        self.app.latest = dict(sim_time=1, paused=False, cars=[], metrics={}, warnings=[])
        self.app.last_snapshot_ns = time.perf_counter_ns()
        self.tmp = tempfile.TemporaryDirectory()
        self.app.run_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_control_is_rejected_instead_of_acknowledging_lost_command(self):
        self.app.fault_started_ns = time.perf_counter_ns()
        with self.assertRaisesRegex(ValueError, "szimulátor MQTT"):
            self.app.control(dict(action="speed", value=2))
        self.app.bus.publish.assert_not_called()

    def test_reset_remains_available_while_simulator_is_offline(self):
        self.app.fault_started_ns = time.perf_counter_ns()
        self.app._new_run = Mock()
        self.app.control(dict(action="reset", seed=9, strategy="fixed"))
        self.app._new_run.assert_called_once()
        self.assertEqual((self.app.seed, self.app.strategy), (9, "fixed"))

    def test_pre_fault_snapshot_cannot_restore_connected_indicator(self):
        now = time.perf_counter_ns()
        self.app.fault_started_ns = now
        self.app._accept("snapshot", dict(self.app.latest, run_id="test", sent_ns=now-1), now)
        self.assertFalse(self.app.state()["connected"])
        self.app._accept("snapshot", dict(self.app.latest, run_id="test", sent_ns=now+1), now+2)
        self.assertTrue(self.app.state()["connected"])

    def test_paused_model_does_not_accept_non_recoverable_timed_disconnect(self):
        self.app.latest["paused"] = True
        with self.assertRaisesRegex(ValueError, "folytasd"):
            self.app.control(dict(action="disconnect", seconds=4))
        self.app.bus.publish.assert_not_called()

    def test_raw_ring_keeps_recent_messages_and_does_not_warn_on_normal_rotation(self):
        self.app.detail_limit_bytes = 1200
        self.app.raw_segment_bytes = 400
        now = time.perf_counter_ns()
        for seq in range(12):
            self.app._accept("telemetry/CAR001", dict(run_id="test", sent_ns=now, seq=seq), now)
        self.app._accept("events", dict(run_id="test", sent_ns=now, type="parked"), now)
        self.assertGreater(self.app.raw_removed_segments, 0)
        files = list(self.app.run_dir.glob("test-messages*.jsonl"))
        self.assertLessEqual(sum(path.stat().st_size for path in files), self.app.detail_limit_bytes)
        records = [json.loads(line) for path in files for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(record["payload"].get("type") == "parked" for record in records))
        self.assertFalse(any("naplókeret" in warning for warning in self.app.state()["warnings"]))


if __name__ == "__main__":
    unittest.main()
