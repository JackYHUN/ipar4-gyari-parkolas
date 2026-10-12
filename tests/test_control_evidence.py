"""Recorded controller evidence must explain, rather than alter, the motion."""
import copy
import unittest

from factory_yard.model import Car, World, _route_lengths
from tests.test_model import configuration, SimpleProtocolPeer


class ControlEvidenceTests(unittest.TestCase):
    def world(self):
        config = configuration()
        config["simulation"].update(automatic_pedestrians=False, production_limit=0)
        config["zones"] = []
        config["crossings"] = []
        return World(config, "controller-evidence", seed=31)

    def routed_car(self, world, speed=3.):
        car = Car("CAR001", 0, 10., 10., "CAR001-parking", speed=speed)
        car.route = [[0., 10.], [100., 10.]]
        car.lengths = _route_lengths(car.route)
        car.progress = 10.
        world.cars[car.id] = car
        return car

    def test_actual_limiter_evaluated_once_and_frozen_reason_is_recorded(self):
        world = self.world()
        car = self.routed_car(world)
        world._sample_local()
        car.frozen_until = 5.
        calls = []
        limits = world._limits
        def counted(candidate):
            calls.append(candidate.id)
            return limits(candidate)
        world._limits = counted
        world.step(.1)
        evidence = car.control_view
        self.assertEqual(calls, [car.id])
        self.assertEqual(evidence["target_speed_m_s"], 0)
        self.assertEqual(evidence["target_state"], "frozen")
        self.assertEqual(evidence["stop_reason"], car.wait_reason)
        self.assertAlmostEqual(evidence["speed_after_m_s"], car.speed)
        self.assertAlmostEqual(evidence["applied_acceleration_m_s2"], -world.braking)
        self.assertAlmostEqual(evidence["braking_distance_m"], 3. ** 2 / (2 * world.braking), places=5)
        self.assertAlmostEqual(evidence["reaction_allowance_m"], 3. * world.sample_period)
        self.assertEqual(evidence["decision_sim_time_s"], world.sim_time)
        self.assertEqual(evidence["sensor_sample_sim_time_s"], car.sensor_view["sample_sim_time_s"])

    def test_stale_perception_keeps_original_clock_while_decisions_continue(self):
        world = self.world()
        car = self.routed_car(world)
        world.step(.1)
        sample_time = car.sensor_view["sample_sim_time_s"]
        sampled_pose = copy.deepcopy(car.sensor_view["pose"])
        world.control("sensor_failure", {"seconds": 4.})
        messages = []
        for _ in range(12):
            messages.extend(world.step(.1))
        self.assertEqual(car.sensor_view["pose"], sampled_pose)
        self.assertEqual(car.control_view["sensor_sample_sim_time_s"], sample_time)
        self.assertGreater(car.control_view["decision_sim_time_s"], sample_time)
        self.assertGreater(car.control_view["sensor_age_s"], world.sensor_timeout)
        self.assertEqual(car.control_view["target_state"], "sensor_stop")
        self.assertEqual(car.control_view["target_speed_m_s"], 0.)
        self.assertFalse(any(topic == "telemetry/CAR001" for topic, _ in messages))
        received = [payload for topic, payload in messages if topic == "diagnostics/perception/CAR001"][-1]
        self.assertEqual(received["source"], "simulation_controller_diagnostics")
        self.assertEqual(received["position_source"], "post_step_simulation_reference")
        self.assertIsNone(received["seq"])
        self.assertEqual(received["control"], car.control_view)
        self.assertEqual(received["sensors"]["sample_sim_time_s"], sample_time)
        received["control"]["zone_grants"]["fake"] = "must-not-mutate"
        self.assertNotIn("fake", car.control_view["zone_grants"])

    def test_waiting_and_parked_have_explicit_stationary_diagnostics(self):
        world = self.world()
        car = Car("CAR001", 0, 10., 10., "CAR001-parking")
        world.cars[car.id] = car
        world.step(.1)
        self.assertEqual(car.control_view["target_state"], "waiting")
        self.assertIsNone(car.control_view["stop_limit_remaining_m"])
        car.state = "parked"
        world.step(.1)
        self.assertEqual(car.control_view["target_state"], "parked")
        self.assertEqual(car.control_view["stop_limit_remaining_m"], 0.)

    def test_recording_does_not_change_motion_or_events(self):
        config = configuration()
        config["simulation"].update(production_limit=3, automatic_pedestrians=False)
        recorded = World(copy.deepcopy(config), "non-interference", seed=31)
        baseline = World(copy.deepcopy(config), "non-interference", seed=31)
        baseline._record_control = lambda *args: None
        peers = [SimpleProtocolPeer(config), SimpleProtocolPeer(config)]
        for _ in range(600):
            for world, peer in zip((recorded, baseline), peers):
                messages = world.step(.1)
                peer.deliver(world, messages)
            a, b = recorded.snapshot(), baseline.snapshot()
            for state in (a, b):
                for car in state["cars"]:
                    car.pop("control")
            self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
