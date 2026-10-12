"""Measured traffic windows, missing evidence and independent display-lag sampling."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_yard.mqtt_bus import Bus
from factory_yard.server import Application, ROOT
from factory_yard.transport_monitor import TransportMonitor


class TransportMonitorTests(unittest.TestCase):
    def setUp(self):
        self.monitor = TransportMonitor()

    def observe(self, seconds, received=0, dequeued=0, queued=0, age=None):
        self.monitor.observe(int(seconds * 1e9), received, dequeued, queued, age)
        return self.monitor.snapshot(int(seconds * 1e9))

    def test_first_zero_clock_and_queue_are_valid_but_rate_needs_a_window(self):
        result = self.observe(0)
        self.assertEqual(result["status"], "collecting")
        self.assertEqual(result["queue_messages"], 0)
        self.assertEqual(result["sample_age_s"], 0)
        self.assertIsNone(result["arrival_rate_hz"])
        self.assertIsNone(result["window_s"])

    def test_actual_elapsed_time_not_target_window_sets_rate(self):
        self.observe(0, 100, 95, 5)
        result = self.observe(2, 110, 103, 7)
        self.assertEqual(result["window_s"], 2)
        self.assertEqual(result["arrival_rate_hz"], 5)
        self.assertEqual(result["dequeue_rate_hz"], 4)
        self.assertEqual(result["queue_growth_hz"], 1)

    def test_unchanged_counters_in_positive_window_are_genuine_zero(self):
        self.observe(0, 50, 50, 0)
        result = self.observe(1, 50, 50, 0)
        self.assertEqual(result["status"], "observed")
        for key in ("arrival_rate_hz", "dequeue_rate_hz", "queue_growth_hz"):
            self.assertEqual(result[key], 0)

    def test_sliding_window_excludes_older_activity(self):
        self.observe(0, 0, 0)
        for second in range(1, 8):
            result = self.observe(second, 100, 100)
        self.assertEqual(result["window_s"], 5)
        self.assertEqual(result["arrival_rate_hz"], 0)

    def test_fast_http_requests_do_not_invent_extra_samples(self):
        self.observe(0, 5, 4, 1)
        for second in (0, .1, .5, .999):
            result = self.observe(second, 20, 15, 5)
        self.assertEqual(len(result["history"]), 1)
        self.assertEqual(result["history"][0]["received_total"], 5)
        self.assertIsNone(result["arrival_rate_hz"])

    def test_received_and_dequeued_counter_resets_start_new_baseline(self):
        for received, dequeued in ((1, 100), (100, 1)):
            with self.subTest(received=received, dequeued=dequeued):
                self.monitor.reset()
                self.observe(0, 100, 100)
                result = self.observe(1, received, dequeued)
                self.assertEqual(result["reset_reason"], "counter_reset")
                self.assertEqual(len(result["history"]), 1)
                self.assertEqual(result["history"][0]["elapsed_s"], 0)
                self.assertIsNone(result["arrival_rate_hz"])

    def test_clock_regression_and_invalid_clocks_never_create_negative_windows(self):
        self.observe(10, 10, 10)
        result = self.observe(9, 11, 11)
        self.assertEqual(result["reset_reason"], "clock_regression")
        self.assertIsNone(result["window_s"])
        for invalid in (True, -1, 1.0, None, float("nan")):
            with self.subTest(clock=invalid):
                self.monitor.observe(invalid, 20, 20, 0)
                result = self.monitor.snapshot(10_000_000_000)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["history"], [])
                self.assertEqual(result["reset_reason"], "invalid_clock")

    def test_missing_counter_breaks_rates_until_two_known_samples_arrive(self):
        self.observe(0, 10, 10)
        result = self.observe(1, None, 15, 3)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["queue_messages"], 3)
        self.assertIsNone(result["arrival_rate_hz"])
        self.assertIsNone(self.observe(2, 20, 17, 3)["arrival_rate_hz"])
        self.assertEqual(self.observe(3, 22, 21, 1)["arrival_rate_hz"], 2)

    def test_invalid_counts_and_ages_are_unknown_not_zero(self):
        for invalid in (True, -1, 1.5, float("nan"), float("inf"), "0"):
            with self.subTest(value=invalid):
                self.monitor.reset()
                result = self.observe(0, invalid, invalid, invalid, invalid)
                self.assertIsNone(result["queue_messages"])
                self.assertEqual(result["history"][0]["snapshot_age_s"], 1.5 if invalid == 1.5 else None)
                self.assertEqual(result["status"], "unavailable")

    def test_long_sampling_gap_is_retained_without_a_rate_bridge(self):
        self.observe(0, 10, 10)
        self.observe(1, 20, 20)
        result = self.observe(4, 200, 100, 100)
        self.assertIsNone(result["arrival_rate_hz"])
        self.assertEqual(len(result["history"]), 3)
        result = self.observe(5, 210, 115, 95)
        self.assertEqual(result["window_s"], 1)
        self.assertEqual(result["arrival_rate_hz"], 10)
        self.assertEqual(result["dequeue_rate_hz"], 15)
        self.assertEqual(result["queue_growth_hz"], -5)

    def test_unknown_queue_does_not_destroy_known_message_rates(self):
        self.observe(0, 10, 5, None)
        result = self.observe(1, 20, 15, 5)
        self.assertEqual(result["arrival_rate_hz"], 10)
        self.assertIsNone(result["queue_growth_hz"])

    def test_history_is_bounded_and_exported_values_are_independent_copies(self):
        for second in range(140):
            result = self.observe(second, second, second)
        self.assertEqual(len(result["history"]), 120)
        self.assertEqual(result["history"][0]["elapsed_s"], 20)
        result["history"][-1]["received_total"] = 99999
        result["history"].clear()
        fresh = self.monitor.snapshot(139_000_000_000)
        self.assertEqual(fresh["history"][-1]["received_total"], 139)
        self.assertEqual(len(fresh["history"]), 120)

    def test_reading_stale_history_never_refreshes_its_age(self):
        self.observe(0)
        self.observe(1, 10, 8, 2)
        result = self.monitor.snapshot(4_000_000_000)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["sample_age_s"], 3)
        self.assertEqual(result["arrival_rate_hz"], 10)
        self.assertEqual(len(self.monitor.snapshot(5_000_000_000)["history"]), 2)

    def test_run_reset_discards_previous_traffic_without_changing_old_export(self):
        self.observe(0, 100, 100)
        old = self.observe(1, 110, 110)
        self.monitor.reset()
        fresh = self.observe(10, 0, 0)
        self.assertEqual(len(fresh["history"]), 1)
        self.assertEqual(fresh["history"][0]["elapsed_s"], 0)
        self.assertIsNone(fresh["arrival_rate_hz"])
        self.assertEqual(len(old["history"]), 2)


class ServerTrafficObservationTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.bus = Bus(config["mqtt"], "traffic-test", "test", [])
        self.bus.connected.set()
        self.app.bus = self.bus
        self.app.latest = dict(sim_time=1, cars=[], warnings=[])
        self.app.last_snapshot_ns = 1_000_000_000

    def tearDown(self):
        self.app.close()

    def callback(self, now_ns):
        payload = json.dumps(dict(run_id=self.bus.run_id)).encode()
        message = SimpleNamespace(topic=self.bus.prefix + "snapshot", payload=payload)
        with patch("factory_yard.mqtt_bus.time.perf_counter_ns", return_value=now_ns):
            self.bus._message(None, None, message)

    def test_state_observes_current_callback_queue_even_when_ingestion_is_held(self):
        with patch("factory_yard.server.time.perf_counter_ns", return_value=1_000_000_000):
            initial = self.app.state()
        self.assertEqual(initial["mqtt"]["capacity"]["queue_messages"], 0)
        for _ in range(8):
            self.callback(3_000_000_000)
        with patch("factory_yard.server.time.perf_counter_ns", return_value=3_000_000_000):
            held = self.app.state()
        measured = held["mqtt"]["capacity"]
        self.assertEqual(measured["arrival_rate_hz"], 4)
        self.assertEqual(measured["dequeue_rate_hz"], 0)
        self.assertEqual(measured["queue_growth_hz"], 4)
        self.assertEqual(measured["queue_messages"], 8)
        self.assertIsNone(held["mqtt"]["observer"]["backlog_messages"])
        self.assertFalse(held["connected"])
        self.assertTrue(held["control_recovery"]["pause_available"])
        self.assertEqual(measured["history"][-1]["snapshot_age_s"], 2)

    def test_observer_records_traffic_without_http_polling(self):
        self.app._health = Mock()
        self.app._accept = Mock()
        self.callback(2_000_000_000)
        with patch("factory_yard.server.time.perf_counter_ns", return_value=2_000_000_000):
            self.app._observe_batch()
        measured = self.app._transport_monitor.snapshot(2_000_000_000)
        self.assertEqual(measured["history"][-1]["received_total"], 1)
        self.assertEqual(measured["history"][-1]["dequeued_total"], 1)
        self.assertEqual(measured["queue_messages"], 0)
        self.app._accept.assert_called_once()

    def test_loading_state_stays_unknown_without_a_bus_queue(self):
        self.app.latest = {}
        real_bus, self.app.bus = self.app.bus, None
        try:
            state = self.app.state()
            self.assertTrue(state["loading"])
            self.assertEqual(state["mqtt"]["capacity"]["status"], "unavailable")
            self.assertIsNone(state["mqtt"]["capacity"]["queue_messages"])
        finally:
            self.app.bus = real_bus


if __name__ == "__main__":
    unittest.main()
