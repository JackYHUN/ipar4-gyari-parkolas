"""Races at worker application must reject safely and leave recoverable state."""
import time
import unittest
from unittest.mock import Mock
from factory_yard.runtime import apply_control


class RuntimeControlsTests(unittest.TestCase):
    def setUp(self):
        self.world, self.bus = Mock(), Mock()
        self.world.snapshot.return_value = {"sim_time": 10}
        self.config = {"mqtt": {"freshness_s": 2}}
        self.now = time.perf_counter_ns()

    def control(self, action, *, paused=False, **params):
        return apply_control(self.world, self.bus, self.config,
                             dict(action=action, control_id="c-1", sent_ns=self.now, **params),
                             self.now, paused, 1, 0)

    def test_pause_then_disconnect_is_rejected_using_worker_state(self):
        paused, speed, until = self.control("pause")
        paused, speed, until = self.control("disconnect", paused=paused, seconds=5)
        self.assertTrue(paused)
        self.assertEqual(until, 0)
        self.bus.client.disconnect.assert_not_called()
        self.assertEqual(self.bus.publish.call_args.args[1]["type"], "control_rejected")

    def test_car_disappearing_between_http_and_mqtt_does_not_crash_worker(self):
        self.world.control.side_effect = ValueError("Ismeretlen jármű")
        self.assertEqual(self.control("freeze_car", car_id="CAR001", seconds=3), (False, 1, 0))
        event = self.bus.publish.call_args.args[1]
        self.assertEqual(event["type"], "control_rejected")
        self.assertEqual((event["car_id"], event["control_id"]), ("CAR001", "c-1"))

    def test_stale_control_is_not_applied(self):
        apply_control(self.world, self.bus, self.config,
                      dict(action="block_slot", slot_id="P01", sent_ns=self.now-3_000_000_000),
                      self.now, False, 1, 0)
        self.world.control.assert_not_called()
        self.assertEqual(self.bus.publish.call_args.args[1]["type"], "control_rejected")

    def test_invalid_speed_cannot_break_runtime_division(self):
        self.assertEqual(self.control("speed", value=0), (False, 1, 0))
        self.assertEqual(self.bus.publish.call_args.args[1]["type"], "control_rejected")

    def test_applied_fault_preserves_car_and_control_correlation(self):
        self.control("freeze_car", car_id="CAR003", seconds=5)
        event = self.bus.publish.call_args.args[1]
        self.assertEqual(event["type"], "control_applied")
        self.assertEqual(event["car_id"], "CAR003")
        self.assertEqual(event["parameters"]["seconds"], 5)


if __name__ == "__main__":
    unittest.main()
