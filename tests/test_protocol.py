"""End-to-end model protocol verification without broker timing claims."""
import json
import pathlib
import unittest

from experiments.compare import cohort_config, run_model


CONFIG = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config" / "default.json").read_text(encoding="utf-8"))


class ProtocolTests(unittest.TestCase):
    def test_both_strategies_complete_same_six_offered_vehicles(self):
        config = cohort_config(CONFIG, 300)
        for strategy in ("fixed", "nearest"):
            with self.subTest(strategy=strategy):
                result = run_model(config, 42, strategy, 300)
                self.assertEqual(result["arrivals"], 6)
                self.assertEqual(result["completed"], 6)
                self.assertEqual(result["unfinished"], 0)
                self.assertEqual(result["collisions"], 0)
                self.assertEqual(set(result["cars"]), {f"CAR{i:03d}" for i in range(1, 7)})

    def test_same_seed_reproduces_protocol_outcomes(self):
        config = cohort_config(CONFIG, 300)
        first = run_model(config, 7, "nearest", 300)
        second = run_model(config, 7, "nearest", 300)
        self.assertEqual(first, second)

    def test_bounded_faults_recover_and_preserve_collision_invariant(self):
        config = cohort_config(CONFIG, 300)
        faults = [(15, "disconnect", {"seconds": 3}),
                  (30, "sensor_failure", {"seconds": 5}),
                  (45, "freeze_car", {"car_id": "CAR001", "seconds": 4})]
        result = run_model(config, 42, "nearest", 300, faults)
        self.assertEqual(result["completed"], 6)
        self.assertEqual(result["collisions"], 0)
        self.assertTrue(any(event["type"] == "sensor_failure" for event in result["events"]))

    def test_observation_horizon_reports_unfinished_vehicles(self):
        config = cohort_config(CONFIG, 15)
        result = run_model(config, 42, "nearest", 15)
        self.assertEqual(result["arrivals"], 2)
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["unfinished"], 2)
        self.assertIsNone(result["mean_parking_time_s"])

    def test_full_parking_keeps_unfinished_tasks_without_unsafe_admission(self):
        config = cohort_config(CONFIG, 60)
        config["simulation"]["initial_occupied_count"] = len(config["slots"])
        for strategy in ("fixed", "nearest"):
            with self.subTest(strategy=strategy):
                result = run_model(config, 42, strategy, 60)
                self.assertEqual(result["arrivals"], 6)
                self.assertEqual(result["completed"], 0)
                self.assertEqual(result["unfinished"], 6)
                self.assertGreater(result["buffered"], 0)
                self.assertEqual(result["collisions"], 0)
                self.assertIsNone(result["mean_parking_time_s"])


if __name__ == "__main__":
    unittest.main()
