"""Declared and controller sampling intervals follow real integration ticks."""

import copy
import json
import math
import unittest

from factory_yard.model import Car, World, _route_lengths
from tests.test_model import configuration


class SampleCadenceTests(unittest.TestCase):
    def world(self, configured_dt=.05, nominal=.1, speed=0.):
        config = configuration()
        config["simulation"].update(dt=configured_dt, telemetry_period=nominal,
                                    production_limit=0, automatic_pedestrians=False,
                                    initial_occupied_count=0)
        config["world"]["buildings"] = []
        config["zones"] = []
        config["crossings"] = []
        world = World(config, "sample-cadence", 42)
        car = Car("EGO", 0., 10., 10., "parking-EGO", speed=speed)
        car.route = [[0., 10.], [100., 10.]]
        car.lengths = _route_lengths(car.route)
        car.progress = 10.
        world.cars[car.id] = car
        return world, car

    def streams(self, world, steps):
        result = {"telemetry": [], "parking": [], "diagnostics": []}
        ticks = []
        for dt in steps:
            messages = world.step(dt)
            ticks.append(round(world.sim_time, 6))
            for topic, payload in messages:
                if topic == "telemetry/EGO":
                    result["telemetry"].append(payload)
                elif topic == "sensors/parking":
                    result["parking"].append(payload)
                elif topic == "diagnostics/perception/EGO":
                    result["diagnostics"].append(payload)
        return result, ticks

    def test_default_cadence_remains_point_one_seconds_with_real_tick_clocks(self):
        world, _ = self.world()
        streams, ticks = self.streams(world, [.05] * 10)
        self.assertEqual([p["sim_time"] for p in streams["telemetry"]], [.05, .15, .25, .35, .45])
        self.assertEqual([p["sim_time"] for p in streams["parking"]], [.05, .15, .25, .35, .45])
        self.assertTrue(all(p["sensors"]["sample_sim_time_s"] in ticks for p in streams["telemetry"]))
        self.assertTrue(all(p["period_s"] == .1 and p["nominal_period_s"] == .1
                            for p in streams["telemetry"] + streams["parking"]))
        self.assertEqual([p["sensors"]["sample_interval_s"] for p in streams["telemetry"]],
                         [None, .1, .1, .1, .1])

    def test_fixed_irregular_ratios_and_large_steps_declare_observed_cadence(self):
        for dt, expected_period in ((.07, .14), (.25, .25), (.06, .12), (.03, .12)):
            with self.subTest(dt=dt):
                world, _ = self.world(configured_dt=dt)
                streams, ticks = self.streams(world, [dt] * 12)
                frames = streams["telemetry"]
                self.assertGreaterEqual(len(frames), 3)
                self.assertIsNone(frames[0]["sensors"]["sample_interval_s"])
                self.assertIsNone(streams["parking"][0]["sample_interval_s"])
                for index, frame in enumerate(frames):
                    sample = frame["sensors"]
                    self.assertEqual(frame["sim_time"], sample["sample_sim_time_s"])
                    self.assertIn(frame["sim_time"], ticks)
                    self.assertAlmostEqual(frame["period_s"], expected_period)
                    self.assertAlmostEqual(sample["period_s"], expected_period)
                    self.assertEqual(frame["nominal_period_s"], .1)
                    self.assertEqual(sample["nominal_period_s"], .1)
                    self.assertEqual(frame["sample_interval_s"], sample["sample_interval_s"])
                    self.assertEqual(frame["sampling"], sample["sampling"])
                    self.assertEqual(sample["sampling"]["period_kind"], "fixed_step_quantized_interval")
                    self.assertFalse(sample["sampling"]["interpolated_samples"])
                    self.assertEqual(sample["sampling"]["integration_step_s"], dt)
                    if index:
                        self.assertAlmostEqual(sample["sample_interval_s"], expected_period)
                        self.assertAlmostEqual(frame["sim_time"] - frames[index - 1]["sim_time"], expected_period)
                for frame in streams["parking"]:
                    self.assertAlmostEqual(frame["period_s"], expected_period)
                    self.assertEqual(frame["nominal_period_s"], .1)
                json.dumps(streams, allow_nan=False)

    def test_world_step_uses_actual_dt_instead_of_configured_dt(self):
        world, _ = self.world(configured_dt=.05)
        self.assertEqual(world.sample_period, .1)
        self.assertEqual(world.snapshot()["cars"][0]["sensors"]["sampling"]["integration_step_source"],
                         "configured_before_first_step")
        streams, _ = self.streams(world, [.07] * 6)
        self.assertAlmostEqual(world.sample_period, .14)
        self.assertEqual([p["sim_time"] for p in streams["telemetry"]], [.07, .21, .35])
        self.assertEqual(streams["telemetry"][0]["sensors"]["sampling"]["integration_step_source"], "actual_step")

    def test_reaction_and_following_allowance_use_effective_period(self):
        world, ego = self.world(configured_dt=.05, speed=3.)
        leader = Car("LEADER", 0., 25., 10., "parking-LEADER")
        world.cars[leader.id] = leader
        world.step(.07)
        control = ego.control_view
        self.assertAlmostEqual(control["reaction_period_s"], .14)
        self.assertEqual(control["nominal_period_s"], .1)
        self.assertAlmostEqual(control["reaction_allowance_m"], 3. * .14)
        measured_progress = ego.perceived_cars[0]["x"]
        expected_limit = (measured_progress - world.length - world.margin
                          - math.sqrt(2) * world.position_uncertainty - world.max_speed * .14)
        self.assertAlmostEqual(control["stop_limit_progress_m"], expected_limit, places=5)
        self.assertAlmostEqual(control["braking_distance_m"], 3. ** 2 / (2 * world.braking))

    def test_variable_steps_report_conservative_bound_without_intermediate_samples(self):
        world, _ = self.world()
        streams, ticks = self.streams(world, [.07, .05, .07, .05, .25, .04, .04, .04, .07, .1])
        self.assertAlmostEqual(world.sample_period, .35)
        for frame in streams["telemetry"]:
            sample = frame["sensors"]
            self.assertIn(sample["sample_sim_time_s"], ticks)
            if sample["sample_interval_s"] is not None:
                self.assertLessEqual(sample["sample_interval_s"], sample["period_s"] + 1e-6)
            self.assertAlmostEqual(frame["control"]["reaction_period_s"], frame["period_s"])
        last = streams["telemetry"][-1]["sensors"]
        self.assertEqual(last["sampling"]["period_kind"], "variable_step_upper_bound")
        self.assertEqual(last["sampling"]["maximum_observed_step_s"], .25)
        self.assertFalse(last["sampling"]["interpolated_samples"])

    def test_sensor_outage_keeps_original_interval_until_actual_recovery_sample(self):
        world, car = self.world(configured_dt=.07)
        initial, _ = self.streams(world, [.07] * 3)
        cached = copy.deepcopy(car.sensor_view)
        self.assertEqual(cached["sample_sim_time_s"], .21)
        self.assertEqual(cached["sample_interval_s"], .14)
        world.control("sensor_failure", {"seconds": .5})
        outage, _ = self.streams(world, [.07] * 7)
        self.assertFalse(outage["telemetry"])
        self.assertFalse(outage["parking"])
        self.assertEqual(car.sensor_view, cached)
        self.assertTrue(outage["diagnostics"])
        for frame in outage["diagnostics"]:
            sample = frame["sensors"]
            self.assertEqual(sample["sample_sim_time_s"], .21)
            self.assertEqual(sample["sample_interval_s"], .14)
            self.assertEqual(sample["nominal_period_s"], .1)
            self.assertEqual(frame["sample_interval_s"], .14)
            self.assertEqual(frame["sampling"], sample["sampling"])
            self.assertTrue(sample["fault_active"])
            self.assertAlmostEqual(sample["period_s"], .14)
        recovery, _ = self.streams(world, [.07] * 2)
        frame = recovery["telemetry"][0]
        self.assertEqual(frame["sensors"]["sample_sim_time_s"], .77)
        self.assertEqual(frame["sensors"]["sample_interval_s"], .56)
        self.assertFalse(frame["sensors"]["fault_active"])
        self.assertTrue(frame["sensors"]["sampling"]["bound_excludes_fault_outages"])
        self.assertEqual(recovery["parking"][0]["sample_interval_s"], .56)
        self.assertAlmostEqual(frame["period_s"], .14)
        self.assertNotEqual(frame["sensors"]["sample_interval_s"], frame["period_s"])

    def test_no_sample_and_first_successful_sample_have_unknown_prior_interval(self):
        world, car = self.world()
        self.assertIsNone(world.snapshot()["cars"][0]["sensors"]["sample_interval_s"])
        world.control("sensor_failure", {"seconds": .3})
        outage, _ = self.streams(world, [.05] * 5)
        self.assertIsNone(car.sensor_view)
        self.assertTrue(all(p["sensors"]["sample_interval_s"] is None for p in outage["diagnostics"]))
        recovery, _ = self.streams(world, [.05] * 4)
        self.assertIsNone(recovery["telemetry"][0]["sensors"]["sample_interval_s"])
        self.assertIsNone(recovery["parking"][0]["sample_interval_s"])

    def test_invalid_step_is_rejected_before_timing_state_changes(self):
        for invalid in (0., -.01, .250001, float("inf"), float("nan")):
            with self.subTest(dt=invalid):
                world, _ = self.world()
                before = world.snapshot()
                with self.assertRaises(ValueError):
                    world.step(invalid)
                self.assertEqual(world.snapshot(), before)

    def test_invalid_nominal_period_or_configured_step_is_rejected(self):
        for period in (0., -.1, float("inf"), float("nan")):
            with self.subTest(period=period), self.assertRaises(ValueError):
                self.world(nominal=period)
        for dt in (0., -.1, .250001, float("inf"), float("nan")):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                self.world(configured_dt=dt)


if __name__ == "__main__":
    unittest.main()
