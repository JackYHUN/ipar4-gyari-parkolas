"""Short-range geometry, recorded sample fidelity and control noninterference."""
import copy
import json
import math
import unittest

from factory_yard.model import Car, World
from factory_yard.proximity import ParkingSensorModel, ray_rectangle_distance
from tests.test_model import configuration, SimpleProtocolPeer


def body(identifier, x=0., y=0., heading=0., length=4., width=2., kind="vehicle"):
    return {"id": identifier, "kind": kind, "label": identifier, "source": "test_geometry",
            "x_m": x, "y_m": y, "heading_deg": heading, "length_m": length, "width_m": width}


def channel(sample, identifier):
    return next(item for item in sample["channels"] if item["id"] == identifier)


class ProximityTests(unittest.TestCase):
    def test_surface_clearance_is_measured_from_bumper_not_car_centre(self):
        sample = ParkingSensorModel().sample(body("EGO"), [body("LEADER", x=6.)], 1.)
        front = channel(sample, "front_center")
        self.assertEqual(front["origin_world_m"], {"x_m": 2., "y_m": 0.})
        self.assertEqual(front["distance_m"], 2.)
        self.assertEqual(front["status"], "clear")
        self.assertEqual(front["target"]["id"], "LEADER")
        self.assertEqual(front["hit_world_m"], {"x_m": 4., "y_m": 0.})
        self.assertEqual(sample["sample_sim_time_s"], 1.)
        self.assertFalse(sample["assumptions"]["control_input"])
        self.assertEqual(json.loads(json.dumps(sample, allow_nan=False)), sample)

    def test_front_back_left_right_remain_correct_when_ego_rotates(self):
        scanner = ParkingSensorModel()
        ego = body("EGO", heading=90.)
        targets = [body("FRONT", y=4.4, heading=90.), body("BACK", y=-5., heading=90.),
                   body("LEFT", x=3.1, heading=90.), body("RIGHT", x=-3.6, heading=90.)]
        sample = scanner.sample(ego, targets, 0.)
        self.assertEqual(channel(sample, "front_center")["target"]["id"], "FRONT")
        self.assertEqual(channel(sample, "rear_center")["target"]["id"], "BACK")
        self.assertEqual(channel(sample, "left")["target"]["id"], "LEFT")
        self.assertEqual(channel(sample, "right")["target"]["id"], "RIGHT")
        self.assertEqual(channel(sample, "front_center")["status"], "close")
        self.assertEqual(channel(sample, "left")["status"], "warning")
        self.assertEqual(channel(sample, "right")["status"], "clear")
        self.assertAlmostEqual(channel(sample, "left")["distance_m"], 1.1)

    def test_ray_rectangle_handles_oriented_bodies_parallel_miss_and_overlap(self):
        rotated = body("ROTATED", x=6., heading=90.)
        self.assertEqual(ray_rectangle_distance((2., 0.), (1., 0.), rotated, 5.), 3.)
        self.assertIsNone(ray_rectangle_distance((2., 3.), (1., 0.), rotated, 5.))
        self.assertEqual(ray_rectangle_distance((6., 0.), (1., 0.), rotated, 5.), 0.)
        self.assertIsNone(ray_rectangle_distance((2., 0.), (-1., 0.), rotated, 5.))
        # A surface contact at precisely the instrument range is a return.
        self.assertEqual(ray_rectangle_distance((2., 0.), (1., 0.), rotated, 3.), 3.)

    def test_first_return_excludes_occluded_farther_body_with_deterministic_ties(self):
        scanner = ParkingSensorModel()
        near = body("NEAR", x=4.3)
        far = body("FAR", x=6.)
        a = scanner.sample(body("EGO"), [far, near], 0.)
        b = scanner.sample(body("EGO"), [near, far], 0.)
        self.assertEqual(a, b)
        self.assertEqual(channel(a, "front_center")["target"]["id"], "NEAR")
        self.assertEqual(channel(a, "front_center")["distance_m"], .3)
        self.assertEqual(channel(a, "front_center")["status"], "close")
        tied = scanner.sample(body("EGO"), [body("Z", x=5.), body("A", x=5.)], 0.)
        self.assertEqual(channel(tied, "front_center")["target"]["id"], "A")

    def test_no_return_is_null_and_not_a_free_space_measurement(self):
        sample = ParkingSensorModel().sample(body("EGO"), [body("EGO"), body("FAR", x=30.)], 0.)
        self.assertIsNone(sample["nearest_distance_m"])
        for measurement in sample["channels"]:
            self.assertEqual(measurement["status"], "no_return")
            self.assertIsNone(measurement["distance_m"])
            self.assertIsNone(measurement["target"])
            self.assertIsNone(measurement["hit_world_m"])
            self.assertEqual(len(measurement["beam_endpoints_world_m"]), 2)
        self.assertTrue(sample["assumptions"]["finite_ray_gaps_unobserved"])
        self.assertFalse(sample["assumptions"]["continuous_beam_coverage"])

    def test_exact_configured_thresholds_and_invalid_settings(self):
        scanner = ParkingSensorModel(range_m=4., close_m=.5, warning_m=1.5)
        for clearance, expected in ((.5, "close"), (1., "warning"), (2., "clear")):
            sample = scanner.sample(body("EGO"), [body("TARGET", x=4. + clearance)], 0.)
            self.assertEqual(channel(sample, "front_center")["status"], expected)
        default = ParkingSensorModel()
        exact_warning = default.sample(body("EGO"), [body("TARGET", x=5.2)], 0.)
        self.assertEqual(channel(exact_warning, "front_center")["distance_m"], 1.2)
        self.assertEqual(channel(exact_warning, "front_center")["status"], "warning")
        for arguments in ({"range_m": math.inf}, {"warning_m": 4.}, {"close_m": -1.},
                          {"rays_per_channel": 8}, {"beam_half_angle_deg": 0.}):
            with self.assertRaises(ValueError):
                ParkingSensorModel(**arguments)

    def world(self):
        config = configuration()
        config["simulation"].update(automatic_pedestrians=False, production_limit=0,
                                    initial_occupied_count=0)
        return World(config, "parking-sensor-evidence", seed=31)

    def test_initial_occupied_parking_geometry_and_active_parked_cars(self):
        world = self.world()
        world.slots = [{"id": "P1", "x": 30., "y": 30.,
                        "route": [[30., 10.], [30., 30.], [30., 30.]]}]
        world.initial_occupied = {"P1"}
        world.cars = {"EGO": Car("EGO", 0., 26.5, 30., "request", heading=90.)}
        occupied = next(item for item in world._proximity_bodies() if item["id"] == "OCCUPIED:P1")
        self.assertEqual(occupied["heading_deg"], 90.)
        self.assertEqual(occupied["source"], "initial_occupied_slot_rectangular_proxy")
        world._sample_local()
        left = channel(world._sensor_bundle(world.cars["EGO"])["proximity"], "left")
        self.assertEqual(left["target"]["id"], "OCCUPIED:P1")
        self.assertAlmostEqual(left["distance_m"], 3.5 - world.width)
        world.initial_occupied.clear()
        world.cars["PARKED"] = Car("PARKED", 0., 30., 30., "request", heading=90., state="parked")
        world._sample_local()
        left = channel(world._sensor_bundle(world.cars["EGO"])["proximity"], "left")
        self.assertEqual(left["target"]["id"], "PARKED")

    def test_configured_building_is_surface_obstacle_but_crossings_and_bounds_are_not(self):
        world = self.world()
        world.config["world"].update(width=6., height=16.,
                                    buildings=[{"id": "WALL", "x": 10., "y": 10., "w": 10., "h": 10.}])
        world.crossings = [{"id": "CROSSING", "x": 8., "y": 15.}]
        car = Car("EGO", 0., 5., 15., "request")
        world.cars[car.id] = car
        world._sample_local()
        front = channel(world._sensor_bundle(car)["proximity"], "front_center")
        self.assertEqual(front["target"]["id"], "BUILDING:WALL")
        self.assertAlmostEqual(front["distance_m"], 10. - 5. - world.length / 2)
        world.config["world"]["buildings"] = []
        world._sample_local()
        self.assertIsNone(channel(world._sensor_bundle(car)["proximity"], "front_center")["distance_m"])

    def test_fault_freezes_exact_sample_and_heading_dimension_evidence(self):
        world = self.world()
        car = Car("EGO", 0., 10., 10., "request")
        other = Car("LEADER", 0., 16., 10., "request", heading=90.)
        world.cars = {car.id: car, other.id: other}
        self.assertIsNone(world._sensor_bundle(car)["proximity"])
        world.sim_time = 1.
        world._sample_local()
        before = copy.deepcopy(car.sensor_view)
        observation = before["environment"]["vehicles"][0]
        self.assertEqual(observation["heading_deg"], 90.)
        self.assertEqual(observation["length_m"], world.length)
        world.sensor_failed_until = 5.
        world.sim_time = 2.
        other.x, other.heading = 80., 180.
        car.x = 20.
        rng = world.sensor_rng.getstate(), world.navigation_rng.getstate()
        world._sample_local()
        bundle = world._sensor_bundle(car)
        self.assertEqual(bundle["proximity"], before["proximity"])
        self.assertEqual(bundle["proximity"]["sample_sim_time_s"], 1.)
        self.assertEqual(bundle["environment"], before["environment"])
        self.assertFalse(bundle["fresh"])
        self.assertEqual(bundle["age_s"], 1.)
        self.assertEqual(rng, (world.sensor_rng.getstate(), world.navigation_rng.getstate()))
        bundle["proximity"]["channels"][0]["status"] = "modified"
        self.assertNotEqual(car.sensor_view["proximity"]["channels"][0]["status"], "modified")

    def test_observer_does_not_change_motion_messages_events_or_rng(self):
        config = configuration()
        config["simulation"].update(production_limit=3, automatic_pedestrians=False)
        observed = World(copy.deepcopy(config), "non-interference", seed=31)
        baseline = World(copy.deepcopy(config), "non-interference", seed=31)
        baseline.parking_sensors.sample = lambda *args: None
        peers = [SimpleProtocolPeer(config), SimpleProtocolPeer(config)]
        for _ in range(500):
            messages = []
            for world, peer in zip((observed, baseline), peers):
                emitted = world.step(.1)
                peer.deliver(world, emitted)
                for _, payload in emitted:
                    if "sensors" in payload:
                        payload["sensors"].pop("proximity")
                messages.append(emitted)
            self.assertEqual(*messages)
            a, b = observed.snapshot(), baseline.snapshot()
            for state in (a, b):
                for car in state["cars"]:
                    car["sensors"].pop("proximity")
            self.assertEqual(a, b)
        self.assertEqual(observed.sensor_rng.getstate(), baseline.sensor_rng.getstate())
        self.assertEqual(observed.navigation_rng.getstate(), baseline.navigation_rng.getstate())


if __name__ == "__main__":
    unittest.main()
