"""Observable state transitions must distinguish braking and actual standstill."""
import copy
import unittest

from factory_yard.model import World
from tests.test_model import configuration, SimpleProtocolPeer


class ModelAuditTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.config["simulation"].update(automatic_pedestrians=False, production_limit=1, parking_dwell=1000)
        self.world = World(self.config, "state-audit", 42)
        self.peer = SimpleProtocolPeer(self.config)

    def steps(self, seconds, connected=True):
        events = []
        for _ in range(round(seconds / .05)):
            messages = self.world.step(.05, connected=connected)
            events.extend(payload for topic, payload in messages if topic == "events")
            self.peer.deliver(self.world, messages)
        return events

    def test_state_records_only_state_or_reason_changes_and_add_correlated_details(self):
        events = self.steps(.5)
        transitions = [event for event in events if event["type"] == "state_changed"]
        self.assertEqual([(event["from_state"], event["to_state"]) for event in transitions[:2]], [(None, "waiting"), ("waiting", "driving")])
        self.assertTrue(all(event["from_state"] != event["to_state"] or event["from_reason"] != event["reason"] for event in transitions))
        self.assertTrue(all(event["category"] == "state" for event in transitions))
        self.assertEqual(transitions[-1]["request_id"], "CAR001-parking")
        self.assertEqual(transitions[-1]["reservation_id"], "CAR001-reservation")
        self.assertIn("speed_m_s", transitions[-1])
        self.assertEqual(len(transitions[-1]["position_m"]), 2)
        self.assertFalse(any(event["type"] == "state_changed" for event in self.steps(.5)))
        car = self.world.snapshot()["cars"][0]
        self.assertAlmostEqual(car["elapsed_s"], 1.0)
        self.assertAlmostEqual(car["yard_elapsed_s"], .95)
        self.assertTrue(car["sensor_ok"])

    def test_disconnection_logs_braking_before_actual_standstill_without_repeated_state_spam(self):
        self.steps(5)
        first = self.steps(.05, connected=False)
        state = next(event for event in first if event["type"] == "state_changed")
        self.assertEqual(state["to_state"], "communication_stop")
        self.assertGreater(state["speed_m_s"], 0)
        self.assertEqual(state["motion_phase"], "braking")
        self.assertFalse(any(event["type"] == "vehicle_stopped" for event in first))
        remaining = self.steps(3, connected=False)
        stopped = [event for event in remaining if event["type"] == "vehicle_stopped"]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["speed_m_s"], 0)
        self.assertFalse(any(event["type"] == "state_changed" for event in remaining))
        resumed = self.steps(.2)
        self.assertTrue(any(event["type"] == "vehicle_resumed" for event in resumed))

    def test_sensor_failure_recovery_has_one_correlated_lifecycle_and_fresh_snapshot(self):
        self.steps(5)
        self.world.control("sensor_failure", {"seconds": 3})
        events = self.steps(3.5)
        started = [event for event in events if event["type"] == "sensor_failure"]
        recovered = [event for event in events if event["type"] == "sensor_recovered"]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(started[0]["fault_id"], recovered[0]["fault_id"])
        self.assertEqual(started[0]["category"], "fault")
        self.assertTrue(any(event["type"] == "state_changed" and event["to_state"] == "sensor_stop" for event in events))
        car = self.world.snapshot()["cars"][0]
        self.assertTrue(car["sensor_ok"])
        self.assertLessEqual(car["sensor_age_s"], self.config["vehicle"]["sensor_timeout"])

    def test_old_identity_and_invalid_command_rejections_do_not_change_vehicle(self):
        self.world.step(.05)
        car = self.world.cars["CAR001"]
        before = (car.slot_id, car.reservation_id, copy.deepcopy(car.route), car.state, car.wait_reason)
        self.world.handle_command("commands/CAR001", {"kind": "parking_wait", "car_id": "CAR001", "request_id": "old", "reason": "wrong"})
        self.world.handle_command("commands/CAR001", {"kind": ["not-a-kind"], "car_id": "CAR001"})
        self.world.handle_command("commands/CAR001", {"kind": "parking_grant", "car_id": "CAR001", "request_id": car.request_id,
                                                       "slot_id": "P08", "route": self.config["slots"][7]["route"], "run_id": "old-run"})
        self.assertEqual((car.slot_id, car.reservation_id, car.route, car.state, car.wait_reason), before)
        events = self.steps(.05)
        rejected = [event for event in events if event["type"] == "command_rejected"]
        self.assertEqual({event["reason"] for event in rejected}, {"parking_request_mismatch", "invalid_command_kind", "wrong_run_id"})
        self.assertTrue(all(event["category"] == "transport" for event in rejected))

    def test_parked_elapsed_measurements_freeze_at_completion(self):
        self.steps(100)
        first = self.world.snapshot()["cars"][0]
        self.assertEqual(first["state"], "parked")
        self.steps(4)
        second = self.world.snapshot()["cars"][0]
        self.assertEqual(first["elapsed_s"], second["elapsed_s"])
        self.assertEqual(first["yard_elapsed_s"], second["yard_elapsed_s"])

    def test_received_trace_can_distinguish_motion_from_waiting(self):
        self.steps(5)
        self.world.control("freeze_car", {"car_id": "CAR001", "seconds": 2})
        samples = []
        for _ in range(50):
            messages = self.world.step(.05)
            samples.extend(p for t, p in messages if t == "telemetry/CAR001")
            self.peer.deliver(self.world, messages)
        frozen = [sample for sample in samples if sample["state"] == "frozen"]
        self.assertTrue(frozen)
        self.assertTrue(all(sample["wait_reason"] == "Hibainjektálás: jármű megállítva" for sample in frozen))
        self.assertTrue(any(sample["speed"] > 0 for sample in frozen))
        self.assertTrue(any(sample["speed"] == 0 for sample in frozen))
        self.assertTrue(all(sample["period_s"] == self.config["simulation"]["telemetry_period"] for sample in samples))
        self.assertEqual([sample["distance_m"] for sample in samples], sorted(sample["distance_m"] for sample in samples))

    def test_map_routes_are_available_for_focused_view_without_mutating_physics(self):
        snapshot = self.world.snapshot()
        self.assertEqual(snapshot["map"]["slots"][0]["route"], self.world.slots[0]["route"])
        snapshot["map"]["slots"][0]["route"][0][0] = -999
        self.assertNotEqual(self.world.slots[0]["route"][0][0], -999)


if __name__ == "__main__":
    unittest.main()
