"""Callback receipt freshness, observer queue age and restricted recovery evidence."""

import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from factory_yard.journal import Journal
from factory_yard.mqtt_bus import Bus
from factory_yard.runtime import apply_control
from factory_yard.server import Application, ROOT, create_http_server


def configuration():
    return json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))


def callback(bus, topic="snapshot", payload=None, receipt_ns=10_000_000_000):
    payload = dict(run_id=bus.run_id, sent_ns=9_000_000_000,
                   utc="2026-10-09T10:00:00.000Z", sim_time=2) if payload is None else payload
    raw = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
    message = SimpleNamespace(topic=bus.prefix + topic, payload=raw)
    with patch("factory_yard.mqtt_bus.time.perf_counter_ns", return_value=receipt_ns):
        bus._message(None, None, message)


class CallbackReceiptTests(unittest.TestCase):
    def setUp(self):
        self.bus = Bus(configuration()["mqtt"], "receipt-test", "test", ["snapshot", "telemetry/+"])

    def tearDown(self):
        self.bus.close()

    def test_callback_keeps_sender_clocks_and_records_original_receipt_before_processing(self):
        payload = dict(run_id=self.bus.run_id, sent_ns=8_888_888_888,
                       utc="2026-10-09T10:00:00.000Z", sent_utc="sender-clock",
                       sim_time=2, nested={"value": [1, 2]})
        original = copy.deepcopy(payload)
        callback(self.bus, payload=payload, receipt_ns=10_123_456_789)
        topic, received, clock = self.bus.inbox.get_nowait()
        self.assertEqual(topic, "snapshot")
        self.assertEqual(clock, 10_123_456_789)
        self.assertEqual(received["sent_ns"], original["sent_ns"])
        self.assertEqual(received["utc"], original["utc"])
        self.assertEqual(received["sent_utc"], original["sent_utc"])
        self.assertTrue(received["received_utc"].endswith("Z"))
        self.assertEqual(payload, original)

    def test_invalid_json_and_foreign_run_cannot_enter_receipt_queue(self):
        for payload in (b"{", b"\xff", b"[]", b"null",
                        dict(run_id="other-run", sent_ns=1, sim_time=0)):
            with self.subTest(payload=payload):
                callback(self.bus, payload=payload)
        self.assertEqual(self.bus.invalid, 5)
        self.assertEqual(self.bus.received, 0)
        self.assertTrue(self.bus.inbox.empty())

    def test_unseen_callback_ages_stay_unknown_even_with_broker_connected(self):
        self.bus.connected.set()
        status = self.bus.receipt_status(10_000_000_000)
        self.assertIs(status["broker_connected"], True)
        self.assertEqual(status["callback_received_messages"], 0)
        self.assertIsNone(status["last_message_age_s"])
        self.assertIsNone(status["last_snapshot_age_s"])

    def test_genuine_zero_receipt_and_invalid_age_clocks_are_distinguished(self):
        callback(self.bus, receipt_ns=0)
        status = self.bus.receipt_status(0)
        self.assertEqual(status["last_message_age_s"], 0)
        self.assertEqual(status["last_snapshot_age_s"], 0)
        self.assertEqual(self.bus.receipt_status(1_000_000_000)["last_snapshot_age_s"], 1)
        for invalid_now in (-1, True, 0.0):
            with self.subTest(now=invalid_now):
                self.assertIsNone(self.bus.receipt_status(invalid_now)["last_snapshot_age_s"])
        callback(self.bus, receipt_ns=10_000_000_000)
        self.assertIsNone(self.bus.receipt_status(9_999_999_999)["last_message_age_s"])

    def test_later_non_snapshot_packet_updates_message_age_without_refreshing_snapshot(self):
        callback(self.bus, receipt_ns=10_000_000_000)
        callback(self.bus, "telemetry/CAR001", receipt_ns=10_500_000_000)
        status = self.bus.receipt_status(11_000_000_000)
        self.assertEqual(status["callback_received_messages"], 2)
        self.assertEqual(status["last_message_age_s"], .5)
        self.assertEqual(status["last_snapshot_age_s"], 1)
        self.assertEqual(self.bus.inbox.qsize(), 2)
        status["last_snapshot_age_s"] = 0
        self.assertEqual(self.bus.receipt_status(11_000_000_000)["last_snapshot_age_s"], 1)

    def test_rejected_payload_cannot_refresh_previously_valid_callback_evidence(self):
        callback(self.bus, receipt_ns=10_000_000_000)
        for invalid in (b"{", b"null", dict(run_id="previous-run")):
            callback(self.bus, payload=invalid, receipt_ns=14_000_000_000)
        status = self.bus.receipt_status(15_000_000_000)
        self.assertEqual(status["callback_received_messages"], 1)
        self.assertEqual(status["last_message_age_s"], 5)
        self.assertEqual(status["last_snapshot_age_s"], 5)
        self.assertEqual(self.bus.inbox.qsize(), 1)

    def test_new_bus_has_no_receipt_history_from_previous_run(self):
        callback(self.bus)
        fresh = Bus(configuration()["mqtt"], "next-run", "test", ["snapshot"])
        try:
            status = fresh.receipt_status(10_000_000_000)
            self.assertEqual(status["callback_received_messages"], 0)
            self.assertIsNone(status["last_message_age_s"])
            self.assertIsNone(status["last_snapshot_age_s"])
            self.assertTrue(fresh.inbox.empty())
        finally:
            fresh.close()


class ObserverQueueAgeTests(unittest.TestCase):
    def setUp(self):
        self.app = Application(configuration(), ROOT / "config/default.json")
        self.bus = Bus(configuration()["mqtt"], "queue-test", "test", [])
        self.app.bus = self.bus
        self.app._health = Mock()
        self.app._accept = Mock()

    def tearDown(self):
        self.app.close()

    def test_queue_wait_is_measured_before_expensive_ingestion(self):
        self.app.OBSERVER_BATCH_MESSAGES = 1
        clock = [11_000_000_000]
        self.bus.inbox.put(("snapshot", {"seq": 1}, 10_000_000_000))
        def slow_ingest(*_):
            clock[0] += 2_000_000_000
        self.app._accept = slow_ingest
        with patch("factory_yard.server.time.perf_counter_ns", side_effect=lambda: clock[0]):
            self.app._observe_batch()
        status = self.app._observer_status()
        self.assertEqual(status["last_queue_wait_ms"], 1000)
        self.assertEqual(status["queue_wait_p95_ms"], 1000)
        self.assertEqual(status["queue_wait_sample_count"], 1)
        self.assertEqual(clock[0], 13_000_000_000)

    def test_unseen_and_invalid_queue_clocks_stay_unknown_without_fabricated_zero(self):
        initial = self.app._observer_status()
        self.assertIsNone(initial["last_queue_wait_ms"])
        self.assertIsNone(initial["queue_wait_p95_ms"])
        self.assertEqual(initial["queue_wait_sample_count"], 0)
        for clock in (None, True, -1, 2_000_000_001, 1.0):
            self.bus.inbox.put(("snapshot", {}, clock))
        with patch("factory_yard.server.time.perf_counter_ns", return_value=2_000_000_000):
            self.app._observe_batch()
        status = self.app._observer_status()
        self.assertIsNone(status["last_queue_wait_ms"])
        self.assertIsNone(status["queue_wait_p95_ms"])
        self.assertEqual(status["queue_wait_sample_count"], 0)
        self.assertEqual(status["queue_wait_invalid_clocks"], 5)
        self.assertEqual(self.app._accept.call_count, 5)

    def test_real_zero_queue_wait_remains_a_valid_sample(self):
        self.bus.inbox.put(("snapshot", {}, 2_000_000_000))
        with patch("factory_yard.server.time.perf_counter_ns", return_value=2_000_000_000):
            self.app._observe_batch()
        status = self.app._observer_status()
        self.assertEqual(status["last_queue_wait_ms"], 0)
        self.assertEqual(status["queue_wait_p95_ms"], 0)
        self.assertEqual(status["queue_wait_sample_count"], 1)
        self.assertEqual(status["queue_wait_invalid_clocks"], 0)

    def test_percentile_uses_only_latest_thousand_valid_queue_waits(self):
        self.app.OBSERVER_BATCH_SECONDS = 100
        for index in range(1100):
            self.bus.inbox.put(("snapshot", {"seq": index}, 2_000_000_000 - index * 1_000_000))
        with patch("factory_yard.server.time.perf_counter_ns", return_value=2_000_000_000):
            while not self.bus.inbox.empty():
                self.app._observe_batch()
        status = self.app._observer_status()
        self.assertEqual(status["dequeued_messages"], 1100)
        self.assertEqual(status["queue_wait_sample_count"], 1000)
        self.assertEqual(status["queue_wait_window_limit"], 1000)
        self.assertEqual(status["last_queue_wait_ms"], 1099)
        self.assertEqual(status["queue_wait_p95_ms"], 1049)
        status["queue_wait_p95_ms"] = -1
        self.assertEqual(self.app._observer_status()["queue_wait_p95_ms"], 1049)


class TransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 10_000_000_000
        self.app = Application(configuration(), ROOT / "config/default.json", speed=8)
        self.app.run_id, self.app.run_dir = "recovery-test", Path(self.tmp.name)
        self.app.journal = Journal(self.app.run_dir / "journal.sqlite3", self.app.run_id)
        self.app.latest = dict(sim_time=2, speed=8, paused=False, warnings=[], metrics={},
                               cars=[dict(id="CAR001", speed=1)])
        self.app.last_snapshot_ns = 9_000_000_000
        self.new_bus()
        callback(self.app.bus, receipt_ns=self.now)
        self.clock_patch = patch("factory_yard.server.time.perf_counter_ns", side_effect=lambda: self.now)
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.app.close()
        self.tmp.cleanup()

    def new_bus(self):
        if self.app.bus:
            self.app.bus.close()
        self.app.bus = Bus(configuration()["mqtt"], self.app.run_id, "test", ["snapshot"])
        self.app.bus.connected.set()
        self.app.bus.publish = Mock()
        self.app.bus.publish.return_value.is_published.return_value = True

    def test_stale_processed_snapshot_and_fresh_callback_have_distinct_diagnostics(self):
        state = self.app.state()
        self.assertFalse(state["connected"])
        self.assertEqual(state["mqtt"]["last_message_age_s"], 1)
        transport = state["mqtt"]["transport"]
        self.assertIs(transport["broker_connected"], True)
        self.assertIs(transport["communication_fault_active"], False)
        self.assertEqual(transport["last_message_age_s"], 0)
        self.assertEqual(transport["last_snapshot_age_s"], 0)
        self.assertEqual(transport["callback_received_messages"], 1)
        self.assertTrue(state["control_recovery"]["pause_available"])
        self.assertTrue(state["control_recovery"]["slow_available"])
        # Reading diagnostics neither drains nor promotes the queued state.
        self.assertEqual(self.app.last_snapshot_ns, 9_000_000_000)
        self.assertEqual(self.app.bus.inbox.qsize(), 1)

    def test_recovery_allows_only_pause_and_explicit_one_speed_without_claiming_application(self):
        for command in (dict(action="pause"), dict(action="speed", value=1)):
            with self.subTest(command=command):
                result = self.app.control(command)
                publication = self.app.bus.publish.call_args
                self.assertEqual(publication.args[0], "control")
                self.assertEqual(publication.args[1]["control_id"], result["control_id"])
                self.assertEqual(publication.kwargs["qos"], 1)
                context = publication.args[1]["recovery_request"]
                self.assertEqual(context["processed_snapshot_age_s"], 1)
                self.assertEqual(context["callback_snapshot_age_s"], 0)
                self.assertEqual(context["clock"], "host_perf_counter_receipt")
                self.assertEqual(context["allowed_actions"], "pause or speed=1 only")
                rows = [row for row in self.app.logs(category="control")["items"]
                        if row["details"].get("control_id") == result["control_id"]]
                self.assertEqual({row["event_type"] for row in rows}, {"control_requested", "control_published"})
                published_row = next(row for row in rows if row["event_type"] == "control_published")
                self.assertEqual(published_row["details"]["recovery_request"], context)
        self.assertEqual(self.app.speed, 8)
        self.assertEqual(self.app.latest["speed"], 8)
        self.assertFalse(self.app.latest["paused"])

    def test_recovery_denies_resume_other_actions_and_acceleration(self):
        crossing = self.app.config["crossings"][0]["id"]
        slot = self.app.config["slots"][0]["id"]
        commands = [dict(action="resume")]
        commands.extend(dict(action="speed", value=value) for value in (2, 4, 8, True, "1"))
        commands.extend((dict(action="pedestrian", crossing_id=crossing),
                         dict(action="block_slot", slot_id=slot),
                         dict(action="sensor_failure", seconds=3),
                         dict(action="disconnect", seconds=3),
                         dict(action="freeze_car", car_id="CAR001", seconds=3)))
        for command in commands:
            with self.subTest(command=command):
                self.app.bus.publish.reset_mock()
                with self.assertRaises(ValueError):
                    self.app.control(command)
                self.app.bus.publish.assert_not_called()
        self.assertEqual(self.app.fault_started_ns, 0)

    def test_no_recovery_for_unknown_stale_disconnected_fault_or_future_callback(self):
        for situation in ("unknown", "only-telemetry", "stale", "disconnected", "fault", "future", "loading"):
            with self.subTest(situation=situation):
                self.new_bus()
                self.app.latest = dict(sim_time=2, speed=8, paused=False, warnings=[], metrics={}, cars=[])
                self.app.last_snapshot_ns = 9_000_000_000
                self.app.fault_started_ns = 0
                if situation != "unknown":
                    callback(self.app.bus, "telemetry/CAR001" if situation == "only-telemetry" else "snapshot",
                             receipt_ns=self.now - 750_000_001 if situation == "stale" else
                             self.now + 1 if situation == "future" else self.now)
                if situation == "disconnected":
                    self.app.bus.connected.clear()
                elif situation == "fault":
                    self.app.fault_started_ns = 1
                elif situation == "loading":
                    self.app.latest = {}
                    self.app.last_snapshot_ns = 0
                state = self.app.state()
                self.assertFalse(state["control_recovery"]["pause_available"])
                self.assertFalse(state["control_recovery"]["slow_available"])
                for command in (dict(action="pause"), dict(action="speed", value=1)):
                    with self.assertRaises(ValueError):
                        self.app.control(command)
                self.app.bus.publish.assert_not_called()

    def test_callback_freshness_boundary_accepts_exact_three_quarters_second(self):
        self.new_bus()
        callback(self.app.bus, receipt_ns=self.now - 750_000_000)
        self.assertTrue(self.app.state()["control_recovery"]["pause_available"])
        self.app.control(dict(action="pause"))
        self.now += 1
        self.assertFalse(self.app.state()["control_recovery"]["pause_available"])
        self.app.bus.publish.reset_mock()
        with self.assertRaises(ValueError):
            self.app.control(dict(action="pause"))
        self.app.bus.publish.assert_not_called()

    def test_fresh_observed_state_retains_regular_controls(self):
        self.app.last_snapshot_ns = self.now
        self.assertTrue(self.app.state()["connected"])
        self.assertFalse(self.app.state()["control_recovery"]["pause_available"])
        crossing = self.app.config["crossings"][0]["id"]
        slot = self.app.config["slots"][0]["id"]
        for command in (dict(action="pause"), dict(action="resume"), dict(action="speed", value=2),
                        dict(action="pedestrian", crossing_id=crossing), dict(action="block_slot", slot_id=slot),
                        dict(action="sensor_failure", seconds=3), dict(action="freeze_car", car_id="CAR001", seconds=3)):
            with self.subTest(command=command):
                self.app.bus.publish.reset_mock()
                self.app.control(command)
                self.app.bus.publish.assert_called_once()

    def test_client_cannot_forge_recovery_context_for_fresh_normal_control(self):
        self.app.last_snapshot_ns = self.now
        command = dict(action="resume", recovery_request={"reason": "CLIENT-FORGED-CONTEXT",
            "processed_snapshot_age_s": 20, "callback_snapshot_age_s": 0,
            "allowed_actions": "pause or speed=1 only"})
        original = copy.deepcopy(command)
        result = self.app.control(command)
        publication = self.app.bus.publish.call_args.args[1]
        self.assertEqual(publication["action"], "resume")
        self.assertNotIn("recovery_request", publication)
        rows = [row for row in self.app.logs(category="control")["items"]
                if row["details"].get("control_id") == result["control_id"]]
        self.assertEqual({row["event_type"] for row in rows}, {"control_requested", "control_published"})
        self.assertTrue(all("recovery_request" not in row["details"] for row in rows))
        self.assertNotIn("CLIENT-FORGED-CONTEXT", json.dumps(rows))
        world, worker_bus = Mock(), Mock()
        world.snapshot.return_value = {"sim_time": 3}
        applied = apply_control(world, worker_bus, self.app.config, dict(publication, sent_ns=self.now),
                                self.now, True, 8, 0)
        self.assertEqual(applied, (False, 8, 0))
        receipt = worker_bus.publish.call_args.args[1]
        self.assertEqual(receipt["type"], "control_applied")
        self.assertNotIn("recovery_request", receipt)
        self.assertEqual(command, original)
        self.app.bus.publish.reset_mock()
        self.app._control(command)
        self.assertNotIn("recovery_request", self.app.bus.publish.call_args.args[1])
        self.assertEqual(command, original)

    def test_health_event_samples_current_queue_instead_of_previous_batch_backlog(self):
        self.app._observer_stats["backlog_messages"] = 123
        self.app._observer_stats["last_queue_wait_ms"] = 25
        self.app.bus.inbox.get_nowait()  # Consume the callback fixture packet.
        self.assertTrue(self.app.bus.inbox.empty())
        self.app._observed_connection = True
        self.app._health()
        row = self.app.logs(category="transport")["items"][0]
        self.assertEqual(row["event_type"], "stream_stale")
        self.assertEqual(row["details"]["backlog_messages"], 0)
        self.assertEqual(row["details"]["last_queue_wait_ms"], 25)
        self.assertIn("sampled for this health event", row["details"]["backlog_definition"])
        self.assertIn("approximate", row["details"]["backlog_definition"])
        # The distinct batch-completion measurement retains its own scope.
        self.assertEqual(self.app._observer_stats["backlog_messages"], 123)

    def test_unacknowledged_recovery_command_never_becomes_published_or_applied_evidence(self):
        self.app.bus.publish.return_value.is_published.return_value = False
        with self.assertRaises(RuntimeError):
            self.app.control(dict(action="pause"))
        kinds = {row["event_type"] for row in self.app.logs(category="control")["items"]}
        self.assertEqual(kinds, {"control_requested", "control_rejected"})
        self.assertFalse(self.app.latest["paused"])

    def test_worker_applied_pause_retains_admission_context_without_mutating_command(self):
        self.app.control(dict(action="pause"))
        command = dict(self.app.bus.publish.call_args.args[1], sent_ns=self.now)
        original = copy.deepcopy(command)
        world, worker_bus = Mock(), Mock()
        world.snapshot.return_value = {"sim_time": 3}
        result = apply_control(world, worker_bus, self.app.config, command,
                               self.now, False, 8, 0)
        self.assertEqual(result, (True, 8, 0))
        event = worker_bus.publish.call_args.args[1]
        self.assertEqual(event["type"], "control_applied")
        self.assertEqual(event["control_id"], command["control_id"])
        self.assertEqual(event["recovery_request"], original["recovery_request"])
        self.assertIsNot(event["recovery_request"], command["recovery_request"])
        self.assertEqual(command, original)

    def test_stale_worker_command_is_rejected_even_with_recovery_context_and_retains_it(self):
        self.app.control(dict(action="pause"))
        command = dict(self.app.bus.publish.call_args.args[1], sent_ns=self.now - 3_000_000_000)
        original = copy.deepcopy(command)
        world, worker_bus = Mock(), Mock()
        world.snapshot.return_value = {"sim_time": 3}
        result = apply_control(world, worker_bus, self.app.config, command,
                               self.now, False, 8, 0)
        self.assertEqual(result, (False, 8, 0))
        event = worker_bus.publish.call_args.args[1]
        self.assertEqual(event["type"], "control_rejected")
        self.assertEqual(event["control_id"], command["control_id"])
        self.assertEqual(event["recovery_request"], original["recovery_request"])
        self.assertIsNot(event["recovery_request"], command["recovery_request"])
        self.assertEqual(command, original)
        world.control.assert_not_called()

    def test_http_recovery_acknowledges_publication_and_only_later_receipt_marks_application(self):
        server = create_http_server(self.app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        def post(command):
            request = Request(url + "/api/control", data=json.dumps(command).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read())
        try:
            with urlopen(url + "/api/state", timeout=3) as response:
                state = json.loads(response.read())
            self.assertFalse(state["connected"])
            self.assertTrue(state["control_recovery"]["pause_available"])
            result = post(dict(action="pause"))
            self.assertTrue(result["ok"])
            self.assertTrue(result["control_id"])
            rows = self.app.logs(category="control")["items"]
            self.assertEqual({row["event_type"] for row in rows}, {"control_requested", "control_published"})
            with self.assertRaises(HTTPError) as rejected:
                post(dict(action="resume"))
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()
            self.app._accept("events", dict(run_id=self.app.run_id, sent_ns=self.now,
                type="control_applied", category="control", control_id=result["control_id"],
                action="pause", sim_time=2, message="Kezelőparancs alkalmazva: pause"), self.now)
            correlated = [row for row in self.app.logs(category="control")["items"]
                          if row["details"].get("control_id") == result["control_id"]]
            self.assertEqual({row["event_type"] for row in correlated},
                             {"control_requested", "control_published", "control_applied"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
