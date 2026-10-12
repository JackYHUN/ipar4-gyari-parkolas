"""Sampled navigation evidence must not become another control input."""

import copy
import json
import math
import unittest

from factory_yard.model import Car, World, _route_lengths, _signed_route_projection
from tests.test_model import configuration, SimpleProtocolPeer


class SensorViewTests(unittest.TestCase):
    def world(self, seed=42):
        config = configuration()
        config["simulation"].update(automatic_pedestrians=False, production_limit=0)
        config["vehicle"]["position_uncertainty"] = .1
        return World(config, "sensor-evidence", seed)

    def car(self, world, car_id="CAR001", x=10., y=10., heading=0.):
        car = Car(car_id, 0, x, y, request_id=car_id + "-parking", heading=heading)
        car.route = [[0., 10.], [100., 10.]]
        car.lengths = _route_lengths(car.route)
        car.progress = x
        world.cars[car_id] = car
        return car

    def test_projection_signed_offset_and_heading_on_horizontal_and_vertical_routes(self):
        route = [[0., 0.], [20., 0.]]
        sample = _signed_route_projection(route, _route_lengths(route), 7., 2., 15.)
        self.assertEqual(sample["lateral_error_m"], 2.)
        self.assertEqual(sample["heading_error_deg"], 15.)
        self.assertEqual((sample["projected_x_m"], sample["projected_y_m"]), (7., 0.))
        sample = _signed_route_projection(route, _route_lengths(route), 7., -2., 355.)
        self.assertEqual(sample["lateral_error_m"], -2.)
        self.assertEqual(sample["heading_error_deg"], -5.)
        route = [[0., 0.], [0., 20.]]
        sample = _signed_route_projection(route, _route_lengths(route), -3., 7., 90.)
        self.assertEqual(sample["lateral_error_m"], 3.)
        self.assertEqual(sample["progress_m"], 7.)
        self.assertEqual(sample["heading_error_deg"], 0.)

    def test_corner_tie_uses_heading_and_skips_zero_length_segments(self):
        route = [[0., 0.], [0., 0.], [10., 0.], [10., 10.]]
        lengths = _route_lengths(route)
        incoming = _signed_route_projection(route, lengths, 10., 0., 0.)
        outgoing = _signed_route_projection(route, lengths, 10., 0., 90.)
        self.assertEqual(incoming["segment_index"], 1)
        self.assertEqual(outgoing["segment_index"], 2)
        self.assertFalse(_signed_route_projection([], [], 0., 0., 0.)["available"])
        self.assertFalse(_signed_route_projection([[0., 0.], [0., 0.]], [0., 0.], 0., 0., 0.)["available"])

    def test_no_sample_is_not_fresh_and_snapshot_reads_never_create_samples(self):
        world = self.world()
        car = self.car(world)
        before_rng = world.navigation_rng.getstate()
        for _ in range(3):
            bundle = world.snapshot()["cars"][0]["sensors"]
            self.assertFalse(bundle["sampled"])
            self.assertFalse(bundle["fresh"])
            self.assertIsNone(bundle["sample_sim_time_s"])
            self.assertIsNone(bundle["pose"])
            self.assertIsNone(bundle["age_s"])
        self.assertEqual(before_rng, world.navigation_rng.getstate())
        self.assertIsNone(car.sensor_view)

    def test_sampled_bundle_is_emitted_and_identical_in_snapshot_with_declared_units(self):
        world = self.world()
        car = self.car(world)
        world.sim_time = 1.
        world._sample_local()
        world._sample_messages()
        payload = next(p for t, p in world._outbox if t == "telemetry/CAR001")
        bundle = world.snapshot()["cars"][0]["sensors"]
        self.assertEqual(payload["sensors"], bundle)
        self.assertEqual(bundle["sample_sim_time_s"], 1.)
        self.assertTrue(bundle["fresh"])
        self.assertEqual(bundle["period_s"], world.sample_period)
        self.assertEqual(bundle["range_m"], world.sensor_range)
        self.assertAlmostEqual(bundle["position_euclidean_bound_m"], math.sqrt(2) * .1, places=6)
        self.assertFalse(bundle["assumptions"]["localization_drives_motion"])
        self.assertEqual(bundle["odometry"]["source"], "ideal_model_proxy")
        self.assertIsNone(bundle["odometry"]["yaw_rate_deg_s"])
        self.assertEqual(json.loads(json.dumps(payload, allow_nan=False))["sensors"], bundle)
        # Consumers may annotate a received/snapshot bundle without altering
        # the source observation or a later client's copy.
        bundle["pose"]["x_m"] = -999
        self.assertNotEqual(car.sensor_view["pose"]["x_m"], -999)

    def test_noise_within_axis_bounds_and_projection_uses_measured_not_true_pose(self):
        world = self.world()
        car = self.car(world)
        for index in range(100):
            world.sim_time = 1. + index * world.sample_period
            world._sample_local()
            sample = world._sensor_bundle(car)
            measured, reference = sample["pose"], sample["reference_pose"]
            self.assertLessEqual(abs(measured["x_m"] - reference["x_m"]), .100001)
            self.assertLessEqual(abs(measured["y_m"] - reference["y_m"]), .100001)
            self.assertAlmostEqual(sample["guidance"]["lateral_error_m"], measured["y_m"] - 10., places=6)
            self.assertAlmostEqual(sample["guidance"]["progress_m"], measured["x_m"], places=6)
            angle_error = (measured["heading_deg"] - car.heading + 180) % 360 - 180
            self.assertLessEqual(abs(angle_error), 1.000001)
        self.assertNotEqual(sample["guidance"]["lateral_error_m"], 0)

    def test_environment_contains_only_sampled_near_objects_and_known_crossing_coordinates(self):
        world = self.world()
        car = self.car(world)
        self.car(world, "NEAR", 17., 11.)
        self.car(world, "FAR", 75., 10.)
        world.crossings = [{"id": "NEAR_Z", "x": 13., "y": 10.},
                           {"id": "FAR_Z", "x": 80., "y": 10.}]
        world.pedestrians = [{"id": "P1", "x": 13., "y": 11., "state": "waiting", "crossing_id": "NEAR_Z"},
                             {"id": "P2", "x": 80., "y": 11., "state": "crossing", "crossing_id": "FAR_Z"}]
        world.sim_time = 1.
        world._sample_local()
        environment = world._sensor_bundle(car)["environment"]
        self.assertEqual([item["id"] for item in environment["vehicles"]], ["NEAR"])
        self.assertAlmostEqual(environment["vehicles"][0]["x_m"], car.perceived_cars[0]["x"], places=6)
        self.assertEqual([item["id"] for item in environment["crossings"]], ["NEAR_Z"])
        self.assertEqual(environment["crossings"][0]["pedestrian_state"], "waiting")
        self.assertEqual(environment["crossings"][0]["position_source"], "known_map_crossing")
        world.pedestrians = []
        world.sim_time += .1
        world._sample_local()
        self.assertEqual(world._sensor_bundle(car)["environment"]["crossings"][0]["pedestrian_state"],
                         "no_pedestrian_observed")

    def test_outage_keeps_measurement_cache_until_timeout_without_refreshing_rng(self):
        world = self.world()
        car = self.car(world)
        world.sim_time = 1.
        world._sample_local()
        before = copy.deepcopy(car.sensor_view)
        before_rng = world.navigation_rng.getstate()
        world.sensor_failed_until = 4.
        car.x = 30.
        for now, fresh in ((1.2, True), (1.5, True), (1.6, False), (3., False)):
            world.sim_time = now
            world._sample_local()
            bundle = world._sensor_bundle(car)
            self.assertEqual(car.sensor_view, before)
            self.assertEqual(bundle["pose"], before["pose"])
            self.assertEqual(bundle["reference_pose"]["x_m"], 10.)
            self.assertEqual(bundle["fresh"], fresh)
            self.assertTrue(bundle["fault_active"])
            self.assertAlmostEqual(bundle["age_s"], now - 1.)
            world._sample_messages()
            self.assertFalse(any(t.startswith("telemetry/") for t, _ in world._outbox))
        self.assertEqual(before_rng, world.navigation_rng.getstate())
        world.sim_time = 4.
        world._sample_local()
        self.assertEqual(car.sensor_view["sample_sim_time_s"], 4.)
        self.assertTrue(world._sensor_bundle(car)["fresh"])
        self.assertFalse(world._sensor_bundle(car)["fault_active"])
        self.assertEqual(car.sensor_view["reference_pose"]["x_m"], 30.)

    def test_no_sample_before_initial_fault_does_not_manufacture_environment(self):
        world = self.world()
        car = self.car(world)
        world.sensor_failed_until = 3.
        world.sim_time = .1
        world._sample_local()
        bundle = world._sensor_bundle(car)
        self.assertFalse(bundle["sampled"])
        self.assertTrue(bundle["fault_active"])
        self.assertEqual(bundle["environment"]["vehicles"], [])
        self.assertEqual(bundle["environment"]["crossings"], [])

    def test_yaw_rate_wraps_degrees_and_is_explicitly_an_ideal_proxy(self):
        world = self.world()
        car = self.car(world, heading=359.)
        world.sim_time = 1.
        world._sample_local()
        car.heading = 1.
        car.speed = 2.
        car.distance = 9.
        world.sim_time = 1.1
        world._sample_local()
        odometry = world._sensor_bundle(car)["odometry"]
        self.assertAlmostEqual(odometry["yaw_rate_deg_s"], 20.)
        self.assertEqual(odometry["speed_m_s"], 2.)
        self.assertEqual(odometry["distance_m"], 9.)
        self.assertEqual(odometry["source"], "ideal_model_proxy")

    def test_same_seed_reproduces_every_observation(self):
        first, second = self.world(seed=19), self.world(seed=19)
        self.car(first)
        self.car(second)
        for index in range(25):
            for world in (first, second):
                world.sim_time = 1. + index * .1
                world._sample_local()
            self.assertEqual(first.snapshot(), second.snapshot())

    def test_changing_navigation_noise_does_not_change_physics_or_established_rngs(self):
        config = configuration()
        config["simulation"].update(production_limit=3, parking_dwell=10)
        first, second = World(config, "same-run", 42), World(config, "same-run", 42)
        first.navigation_rng.seed(123)
        second.navigation_rng.seed(456)
        peers = SimpleProtocolPeer(config), SimpleProtocolPeer(config)
        differing_measurement = False
        for index in range(2000):
            messages = [world.step(.05) for world in (first, second)]
            normalized = []
            for stream in messages:
                output = []
                for topic, payload in stream:
                    value = dict(payload)
                    value.pop("sensors", None)
                    output.append((topic, value))
                normalized.append(output)
            self.assertEqual(normalized[0], normalized[1])
            for world, peer, stream in zip((first, second), peers, messages):
                peer.deliver(world, stream)
            if index % 20 == 0:
                snapshots = [world.snapshot() for world in (first, second)]
                for car1, car2 in zip(snapshots[0]["cars"], snapshots[1]["cars"]):
                    differing_measurement |= car1["sensors"] != car2["sensors"]
                    car1.pop("sensors")
                    car2.pop("sensors")
                self.assertEqual(snapshots[0], snapshots[1])
        self.assertTrue(differing_measurement)
        self.assertEqual(first.sensor_rng.getstate(), second.sensor_rng.getstate())
        self.assertEqual(first.initial_rng.getstate(), second.initial_rng.getstate())
        self.assertEqual(first.pedestrian_rng.getstate(), second.pedestrian_rng.getstate())


if __name__ == "__main__":
    unittest.main()
