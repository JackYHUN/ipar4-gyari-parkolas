"""Model reference checks and controller freshness must agree with sensor evidence."""
import unittest

from factory_yard.model import Car, World
from tests.test_model import configuration


class ModelConsistencyTests(unittest.TestCase):
    def world(self):
        config = configuration()
        config["simulation"].update(automatic_pedestrians=False, production_limit=0,
                                    initial_occupied_count=0)
        config["world"]["buildings"] = []
        return World(config, "model-consistency", 42)

    def test_valid_grant_cannot_move_vehicle_before_first_successful_sensor_sample(self):
        config = configuration()
        config["simulation"].update(automatic_pedestrians=False, production_limit=1,
                                    initial_occupied_count=0)
        world = World(config, "initial-sensor-outage", 42)
        world.control("sensor_failure", {"seconds": .3})
        request = next(payload for topic, payload in world.step(.05) if topic == "requests/parking")
        slot = config["slots"][-1]
        world.handle_command("commands/" + request["car_id"], {
            "kind": "parking_grant", **request, "slot_id": slot["id"],
            "reservation_id": "valid-grant-before-sample", "route": slot["route"]})
        self.assertEqual(world.cars[request["car_id"]].reservation_id, "valid-grant-before-sample")
        for _ in range(4):
            world.step(.05)
            car = world.snapshot()["cars"][0]
            self.assertEqual(car["state"], "sensor_stop")
            self.assertEqual(car["speed"], 0)
            self.assertEqual(car["distance_m"], 0)
            self.assertFalse(car["sensor_ok"])
            self.assertIsNone(car["sensor_age_s"])
            self.assertFalse(car["sensors"]["sampled"])
            self.assertFalse(car["sensors"]["fresh"])
            self.assertIsNone(car["control"]["sensor_sample_sim_time_s"])
        # Recovery, followed by an actual due sample, permits bounded motion.
        for _ in range(4):
            world.step(.05)
        car = world.snapshot()["cars"][0]
        self.assertTrue(car["sensor_ok"])
        self.assertTrue(car["sensors"]["sampled"])
        self.assertGreater(car["speed"], 0)

    def test_sample_at_time_zero_has_consistent_age_and_freshness_everywhere(self):
        world = self.world()
        car = Car("EGO", 0., 10., 10., "request")
        world.cars[car.id] = car
        before = world.snapshot()["cars"][0]
        self.assertIsNone(before["sensor_age_s"])
        self.assertFalse(before["sensor_ok"])
        world._sample_local()
        for now, fresh in ((0., True), (.5, True), (.500001, False)):
            world.sim_time = now
            state = world.snapshot()["cars"][0]
            self.assertEqual(state["sensor_ok"], fresh)
            self.assertEqual(state["sensors"]["fresh"], fresh)
            self.assertAlmostEqual(state["sensor_age_s"], now, places=4)
            self.assertAlmostEqual(state["sensors"]["age_s"], now, places=6)
            self.assertEqual(state["sensors"]["sample_sim_time_s"], 0.)

    def test_building_overlap_is_reference_collision_even_during_sensor_outage(self):
        world = self.world()
        world.config["world"]["buildings"] = [{"id": "WALL", "label": "Tesztfal",
                                                "x": 9., "y": 9., "w": 3., "h": 3.}]
        car = Car("EGO", 0., 10., 10., "request")
        world.cars[car.id] = car
        world._sample_local()
        self.assertEqual(car.sensor_view["proximity"]["nearest_distance_m"], 0.)
        world.sensor_failed_until = 3.
        world.sim_time = 1.
        world._check_collisions()
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 1)
        event = next(event for event in world.snapshot()["events"] if event["type"] == "collision")
        self.assertEqual(event["other_id"], "BUILDING:WALL")
        self.assertEqual(event["collision_kind"], "vehicle_building")
        self.assertEqual(event["obstacle_source"], "configured_map_building_rectangle")
        self.assertEqual(event["reference_shape"], "oriented_rectangle")
        self.assertFalse(world._sensor_bundle(car)["fresh"])
        self.assertEqual(car.sensor_view["sample_sim_time_s"], 0.)

    def test_occupied_slot_proxy_uses_saved_approach_orientation_and_pair_deduplication(self):
        world = self.world()
        world.slots = [{"id": "P1", "x": 30., "y": 30.,
                        "route": [[30., 10.], [30., 30.], [30., 30.]]}]
        world.initial_occupied = {"P1"}
        car = Car("EGO", 0., 28.4, 30., "request", heading=90.)
        world.cars[car.id] = car
        world._sample_local()
        world._check_collisions()
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 1)
        event = next(event for event in world.snapshot()["events"] if event["type"] == "collision")
        self.assertEqual(event["collision_kind"], "vehicle_parked_proxy")
        self.assertEqual(event["other_id"], "OCCUPIED:P1")
        # Keep historical semantics: each colliding pair is counted once/run,
        # including separation and a later contact with that same body.
        for x in (28.4, 10., 28.4):
            car.x = x
            world._check_collisions()
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 1)
        self.assertEqual(sum(event["type"] == "collision" for event in world.snapshot()["events"]), 1)

    def test_surface_contact_and_positive_clearance_do_not_count_as_penetration(self):
        world = self.world()
        world.config["world"]["buildings"] = [{"id": "WALL", "label": "Fal",
                                                "x": 10., "y": 9., "w": 10., "h": 3.}]
        car = Car("EGO", 0., 10. - world.length / 2, 10., "request")
        world.cars[car.id] = car
        world._sample_local()
        self.assertEqual(car.sensor_view["proximity"]["nearest_distance_m"], 0.)
        world._check_collisions()
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 0)
        car.x -= .01
        world._check_collisions()
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 0)
        car.x += .02
        world._check_collisions()
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 1)

    def test_active_vehicle_pair_is_not_double_counted_via_static_body_pass(self):
        world = self.world()
        world.cars = {identifier: Car(identifier, 0., 10., 10., "request")
                      for identifier in ("EGO", "OTHER")}
        world._check_collisions()
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 1)
        events = [event for event in world.snapshot()["events"] if event["type"] == "collision"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["collision_kind"], "vehicle_vehicle")


if __name__ == "__main__":
    unittest.main()
