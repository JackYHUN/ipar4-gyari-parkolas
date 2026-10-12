"""A continuously replenished MQTT inbox must not monopolise HTTP/control."""
import json
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen

from factory_yard.mqtt_bus import Bus
from factory_yard.server import Application, ROOT, create_http_server


class ObserverFairnessTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        # Use the production iterator against a real Queue, without a network
        # client. This reproduces its get_nowait-until-empty behaviour.
        self.bus = Bus.__new__(Bus)
        self.bus.inbox = queue.Queue()
        # The observer also samples receipt diagnostics now. Keep the network
        # event in the queue-only fixture; no network loop is started here.
        self.bus.connected = threading.Event()
        self.app.bus = self.bus
        self.app._health = Mock()

    def test_replenishing_queue_returns_at_message_budget_without_loss_or_reordering(self):
        self.app.OBSERVER_BATCH_MESSAGES = 7
        self.app.OBSERVER_BATCH_SECONDS = 100
        self.bus.inbox.put(("telemetry/CAR001", {"seq": 1}, 1001))
        accepted = []
        def accept(topic, payload, receipt):
            accepted.append((payload["seq"], receipt))
            self.bus.inbox.put((topic, {"seq": payload["seq"] + 1}, receipt + 1))
        self.app._accept = accept
        self.assertTrue(self.app._observe_batch())
        self.assertEqual(accepted, [(seq, 1000 + seq) for seq in range(1, 8)])
        self.assertEqual(self.bus.inbox.qsize(), 1)
        self.assertTrue(self.app._observe_batch())
        self.assertEqual(accepted, [(seq, 1000 + seq) for seq in range(1, 15)])
        self.assertEqual(self.bus.inbox.queue[0][1]["seq"], 15)
        self.assertEqual(self.app._observer_stats["dequeued_messages"], 14)
        self.assertEqual(self.app._observer_stats["backlog_messages"], 1)
        self.assertEqual(self.app._health.call_count, 2)

    def test_wall_budget_is_checked_after_whole_ingestions_only(self):
        packets = [("telemetry/CAR001", {"seq": seq}, 1000 + seq) for seq in range(1, 5)]
        for packet in packets:
            self.bus.inbox.put(packet)
        ticks, accepted = [0.0], []
        def accept(*packet):
            accepted.append(packet)
            ticks[0] += .01
        self.app._accept = accept
        with patch("factory_yard.server.time.perf_counter", side_effect=lambda: ticks[0]):
            self.assertTrue(self.app._observe_batch())
        self.assertEqual(accepted, packets[:3])
        self.assertEqual(self.bus.inbox.qsize(), 1)
        self.assertAlmostEqual(self.app._observer_stats["last_batch_wall_ms"], 30)
        self.assertEqual(self.app._observer_stats["backlog_messages"], 1)
        with patch("factory_yard.server.time.perf_counter", side_effect=lambda: ticks[0]):
            self.assertFalse(self.app._observe_batch())
        self.assertEqual(accepted, packets)
        self.assertEqual(self.app._observer_stats["dequeued_messages"], 4)

    def test_other_thread_can_take_lock_between_replenished_batches(self):
        self.app.OBSERVER_BATCH_MESSAGES = 1
        self.app.OBSERVER_BATCH_SECONDS = 100
        self.bus.inbox.put(("telemetry/CAR001", {"seq": 1}, 1001))
        accepted = []
        def accept(topic, payload, receipt):
            accepted.append(payload["seq"])
            self.bus.inbox.put((topic, {"seq": payload["seq"] + 1}, receipt + 1))
        self.app._accept = accept
        worker = threading.Thread(target=self.app._observe, daemon=True)
        worker.start()
        acquired = self.app.lock.acquire(timeout=2)
        try:
            self.assertTrue(acquired, "observer never yielded the application lock")
            self.assertGreaterEqual(len(accepted), 1)
            self.assertGreaterEqual(self.bus.inbox.qsize(), 1)
            self.app.closed = True
        finally:
            if acquired:
                self.app.lock.release()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(accepted, list(range(1, len(accepted) + 1)))

    def test_batch_keeps_bus_and_packet_receipt_clocks_during_run_switch(self):
        self.app.OBSERVER_BATCH_MESSAGES = 2
        self.app.OBSERVER_BATCH_SECONDS = 100
        for seq in range(1, 4):
            self.bus.inbox.put(("telemetry/CAR001", {"seq": seq}, 123000 + seq))
        seen, started, switched = [], threading.Event(), threading.Event()
        replacement = Bus.__new__(Bus)
        replacement.inbox = queue.Queue()
        replacement.connected = threading.Event()
        def accept(topic, payload, receipt):
            started.set()
            seen.append((payload["seq"], receipt, self.app.bus is self.bus))
            time.sleep(.01)
        self.app._accept = accept
        batch = threading.Thread(target=self.app._observe_batch)
        batch.start()
        self.assertTrue(started.wait(2))
        def switch():
            with self.app.lock:
                self.app.bus = replacement
                switched.set()
        reset = threading.Thread(target=switch)
        reset.start()
        batch.join(2)
        reset.join(2)
        self.assertTrue(switched.is_set())
        self.assertEqual(seen, [(1, 123001, True), (2, 123002, True)])
        self.assertEqual(self.bus.inbox.qsize(), 1)
        self.assertIs(self.app.bus, replacement)

    def test_empty_batch_has_actual_zero_count_and_no_invented_backlog(self):
        self.app._accept = Mock()
        self.assertFalse(self.app._observe_batch())
        self.app._accept.assert_not_called()
        self.assertEqual(self.app._observer_stats["backlog_messages"], 0)
        self.assertEqual(self.app._observer_stats["last_batch_messages"], 0)
        self.app._health.assert_called_once()
        self.assertGreaterEqual(self.app._observer_stats["last_batch_wall_ms"], 0)

    def test_state_observer_statistics_are_copies_and_reset_scope_is_fresh(self):
        self.app._accept = Mock()
        self.app._observe_batch()
        self.app.latest = dict(sim_time=1, cars=[], warnings=[], worker_mqtt={})
        self.app.central = {}
        self.bus.connected = threading.Event()
        self.bus.connected.set()
        self.bus.published = self.bus.invalid = 0
        self.app.last_snapshot_ns = time.perf_counter_ns()
        self.app.raw_logging_stats = Mock(return_value={})
        state = self.app.state()
        state["mqtt"]["observer"]["batches"] = -1
        self.assertEqual(self.app._observer_stats["batches"], 1)
        fresh = self.app._empty_observer_stats()
        self.assertEqual(fresh["dequeued_messages"], 0)
        self.assertIsNone(fresh["backlog_messages"])

    def test_http_state_completes_while_inbox_never_empties(self):
        self.app.OBSERVER_BATCH_MESSAGES = 3
        self.app.OBSERVER_BATCH_SECONDS = 100
        self.app.latest = dict(sim_time=1, cars=[], warnings=[], worker_mqtt={})
        self.bus.connected = threading.Event()
        self.bus.connected.set()
        self.bus.published = self.bus.invalid = 0
        self.app.last_snapshot_ns = time.perf_counter_ns()
        self.bus.inbox.put(("telemetry/CAR001", {"seq": 1}, 1001))
        accepted = []
        started = threading.Event()
        def accept(topic, payload, receipt):
            accepted.append((payload["seq"], receipt))
            self.bus.inbox.put((topic, {"seq": payload["seq"] + 1}, receipt + 1))
            started.set()
        self.app._accept = accept
        server = create_http_server(self.app, 0)
        http = threading.Thread(target=server.serve_forever, daemon=True)
        observer = threading.Thread(target=self.app._observe, daemon=True)
        try:
            http.start()
            observer.start()
            self.assertTrue(started.wait(2))
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/state", timeout=2) as response:
                state = json.loads(response.read())
            self.assertEqual(state["sim_time"], 1)
            self.assertEqual(state["mqtt"]["observer"]["backlog_messages"], 1)
            self.assertGreaterEqual(state["mqtt"]["observer"]["batches"], 1)
        finally:
            self.app.closed = True
            observer.join(2)
            server.shutdown()
            server.server_close()
            http.join(2)
        self.assertEqual(accepted, [(seq, 1000 + seq) for seq in range(1, len(accepted) + 1)])
        self.assertEqual(self.bus.inbox.qsize(), 1)


if __name__ == "__main__":
    unittest.main()
