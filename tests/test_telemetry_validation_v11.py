"""Malformed MQTT telemetry cannot refresh evidence or release a conflict zone."""

import copy
import unittest

from factory_yard.coordinator import Coordinator
from factory_yard.model import World
from tests.test_model import configuration


class TelemetryValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.now = 10_000_000_000
        self.controller = Coordinator(self.config, "telemetry-validation")

    def payload(self, **fields):
        return dict(run_id="telemetry-validation", sent_ns=self.now,
                    sim_time=10.0, car_id="A", seq=2, x=122.0, y=50.0,
                    speed=0.0, state="driving") | fields

    def send(self, topic, payload):
        return self.controller.handle(topic, payload, now_ns=self.now)

    def decision_state(self):
        c = self.controller
        return copy.deepcopy(dict(telemetry=c.telemetry, sim_time=c.sim_time,
                                  slots=c.slots, zones=c.zones,
                                  pending_exits=c.pending_exits,
                                  pending_departures=c.pending_departures,
                                  sensor_seq=c.sensor_seq,
                                  sensor_received_ns=c.sensor_received_ns,
                                  responses=c.responses, zone_responses=c.zone_responses,
                                  zone_queues=c.zone_queues,
                                  car_slots=c._car_slots, serial=c.serial))

    def pending_exit(self):
        self.send("sensors/parking", self.payload(seq=1, slots=[
            dict(id=slot["id"], occupied=False) for slot in self.config["slots"]]))
        self.send("requests/parking", self.payload(request_id="park-A", origin=[14, 58]))
        self.send("telemetry/A", self.payload(seq=1, x=122, y=58))
        grant = self.send("requests/zone", self.payload(request_id="zone-A", zone_id="C1"))[0][1]
        self.send("events", self.payload(type="zone_exit", zone_id="C1",
                                         grant_id=grant["grant_id"], sim_time=11.0))
        self.assertEqual(self.controller.zones["C1"]["owner"], "A")
        self.assertIn("C1", self.controller.pending_exits)
        self.controller.drain_audit()

    def assert_atomic_rejection(self, payload, reason, topic="telemetry/A"):
        before = self.decision_state()
        self.controller.drain_audit()
        self.assertEqual(self.send(topic, payload), [])
        self.assertEqual(self.decision_state(), before)
        records = self.controller.drain_audit()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event_type"], "message_rejected")
        self.assertEqual(records[0]["reason"], reason)
        self.assertEqual(records[0]["topic"], topic)
        self.assertEqual(records[0]["level"], "warning")

    def test_numeric_string_cannot_poison_pending_exit_and_same_sequence_recovers(self):
        self.pending_exit()
        rejected = self.payload(sim_time="11.5")
        original = copy.deepcopy(rejected)
        self.assert_atomic_rejection(rejected, "invalid_sim_time")
        self.assertEqual(rejected, original)
        self.assertEqual(self.controller.telemetry["A"]["seq"], 1)
        valid = self.payload(sim_time=11.5)
        self.send("telemetry/A", valid)
        self.assertEqual(self.controller.telemetry["A"]["seq"], 2)
        self.assertEqual(self.controller.telemetry["A"]["sim_time"], 11.5)
        self.assertIsNone(self.controller.zones["C1"]["owner"])
        self.assertNotIn("C1", self.controller.pending_exits)
        release = next(record for record in self.controller.drain_audit()
                       if record["event_type"] == "zone_released")
        self.assertEqual(release["telemetry_seq"], 2)
        self.assertEqual(release["telemetry_sim_time"], 11.5)
        self.assertEqual(release["exit_event_sim_time"], 11.0)

    def test_invalid_simulation_clocks_cannot_mutate_existing_evidence(self):
        self.pending_exit()
        for value in (True, False, "11.5", "NaN", None, -1, float("nan"),
                      float("inf"), -float("inf"), [], {}, 10 ** 400):
            with self.subTest(value=value):
                self.assert_atomic_rejection(self.payload(sim_time=value), "invalid_sim_time")

    def test_telemetry_requires_explicit_simulation_clock(self):
        self.pending_exit()
        payload = self.payload()
        payload.pop("sim_time")
        self.assert_atomic_rejection(payload, "invalid_sim_time")

    def test_sequence_requires_nonnegative_integer_without_boolean_coercion(self):
        self.pending_exit()
        for seq in (True, False, 2.0, "2", None, [], {}, -1):
            with self.subTest(seq=seq):
                self.assert_atomic_rejection(self.payload(seq=seq, sim_time=900), "invalid_telemetry")

    def test_coordinates_require_finite_numbers_without_boolean_coercion(self):
        self.pending_exit()
        for axis in ("x", "y"):
            for value in (True, False, "50", None, float("nan"), float("inf"),
                          -float("inf"), [], {}, 10 ** 400):
                with self.subTest(axis=axis, value=value):
                    self.assert_atomic_rejection(self.payload(sim_time=900, **{axis: value}), "invalid_telemetry")
            missing = self.payload(sim_time=900)
            missing.pop(axis)
            self.assert_atomic_rejection(missing, "invalid_telemetry")

    def test_transport_timestamp_requires_nonnegative_integer(self):
        self.pending_exit()
        for value in (True, False, float(self.now), str(self.now), None, -1,
                      float("nan"), float("inf"), [], {}, 10 ** 400):
            with self.subTest(value=value):
                self.assert_atomic_rejection(self.payload(sent_ns=value, sim_time=900), "invalid_timestamp")

    def test_telemetry_requires_explicit_transport_timestamp(self):
        self.pending_exit()
        payload = self.payload(sim_time=900)
        payload.pop("sent_ns")
        self.assert_atomic_rejection(payload, "invalid_timestamp")

    def test_topic_must_match_payload_vehicle_id(self):
        self.pending_exit()
        for topic in ("telemetry/OTHER", "telemetry/", "telemetry/A/extra"):
            with self.subTest(topic=topic):
                self.assert_atomic_rejection(self.payload(sim_time=900),
                                             "telemetry_topic_mismatch", topic)
        self.assertNotIn("OTHER", self.controller.telemetry)

    def test_invalid_vehicle_id_cannot_create_state(self):
        self.pending_exit()
        for value in (True, 1, None, "", [], {}):
            with self.subTest(value=value):
                self.assert_atomic_rejection(self.payload(car_id=value, sim_time=900), "invalid_telemetry")

    def test_repeated_or_old_frame_cannot_advance_clock_refresh_or_clear_exit(self):
        self.pending_exit()
        self.now += 1_000_000_000
        for seq in (1, 0):
            with self.subTest(seq=seq):
                self.assert_atomic_rejection(self.payload(seq=seq, sim_time=900), "non_increasing_sequence")
        self.assertEqual(self.controller.telemetry["A"]["received_ns"], 10_000_000_000)
        self.assertEqual(self.controller.sim_time, 11.0)

    def test_stale_or_future_transport_clock_cannot_advance_simulation_time(self):
        self.pending_exit()
        self.assert_atomic_rejection(self.payload(sent_ns=0, sim_time=900), "stale_message")
        self.assert_atomic_rejection(self.payload(sent_ns=self.now + 1_000_000_001,
                                                  sim_time=900), "future_timestamp")

    def test_zero_sequence_and_mixed_integer_float_measurements_are_retained_exactly(self):
        payload = self.payload(seq=0, sim_time=0, x=-2, y=0.125)
        original = copy.deepcopy(payload)
        self.send("telemetry/A", payload)
        self.assertEqual(payload, original)
        self.assertEqual(self.controller.telemetry["A"], dict(original, received_ns=self.now))
        self.assertIs(type(self.controller.telemetry["A"]["sim_time"]), int)
        self.assertEqual(self.controller.drain_audit(), [])

    def test_invalid_exit_clock_cannot_enter_pending_state(self):
        self.send("telemetry/A", self.payload(seq=1, x=122, y=58))
        grant = self.send("requests/zone", self.payload(request_id="zone-A", zone_id="C1"))[0][1]
        for value in ("11.5", True, None, -1, float("nan")):
            with self.subTest(value=value):
                self.assert_atomic_rejection(self.payload(type="zone_exit", zone_id="C1",
                                                          grant_id=grant["grant_id"], sim_time=value),
                                             "invalid_sim_time", "events")
        self.assertEqual(self.controller.pending_exits, {})
        self.send("telemetry/A", self.payload(seq=2, x=122, y=50, sim_time=12))
        self.assertEqual(self.controller.zones["C1"]["owner"], "A")

    def test_actual_default_model_frames_remain_compatible(self):
        # Model-level contract test with explicit logical transport clocks.
        # A separate smoke test verifies the real MQTT broker and publisher.
        world = World(self.config, "telemetry-validation", 19)
        telemetry_count = 0
        for _ in range(120):
            self.now += 50_000_000
            for topic, payload in world.step(.05):
                framed = dict(payload, run_id="telemetry-validation", sent_ns=self.now)
                commands = self.send(topic, framed)
                if topic.startswith("telemetry/"):
                    telemetry_count += 1
                    self.assertEqual(self.controller.telemetry[framed["car_id"]]["seq"], framed["seq"])
                for command_topic, command in commands:
                    world.handle_command(command_topic, dict(command, run_id="telemetry-validation"))
        self.assertGreater(telemetry_count, 50)
        self.assertFalse(any(record["event_type"] == "message_rejected"
                             for record in self.controller.drain_audit()))


if __name__ == "__main__":
    unittest.main()
