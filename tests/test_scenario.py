"""Editable scenario geometry, reproducible routes and preview isolation."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import Mock
from factory_yard.scenario import compile_scenario, shortest_path
from factory_yard.server import Application, ROOT, create_http_server


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))

    def test_full_capacity_has_separate_lanes_reachable_slots_and_source(self):
        original = copy.deepcopy(self.base)
        for exit in ("EXIT70", "EXIT80"):
            for area in ("north", "south"):
                active, config = compile_scenario(self.base, dict(car_count=30, parking_count=96,
                    initial_car_count=30, initial_occupied_count=0, production_exit_id=exit, parking_area_id=area))
                self.assertEqual(len(config["slots"]), 96)
                self.assertEqual(len(config["simulation"]["initial_cars"]), 30)
                self.assertEqual(len(config["network"]["edges"]), 12)
                for slot in config["slots"]:
                    self.assertEqual(slot["route"][0], config["world"]["origin"])
                    self.assertEqual(slot["route"][-1], [slot["x"], slot["y"]])
                    self.assertTrue(all(a != b for a, b in zip(slot["route"], slot["route"][1:])))
                self.assertEqual(config["layout"]["source_page"], 2)
                # Normalized parameters reconstruct exactly the same configuration.
                _, again = compile_scenario(self.base, active)
                self.assertEqual(config, again)
        self.assertEqual(self.base, original)

    def test_custom_car_and_parking_positions_compile_actual_routes(self):
        active, config = compile_scenario(self.base, dict(initial_car_count=1,
            initial_cars=[dict(x=400, y=205)], slots=[dict(id="P01", x=346.5, y=17)]))
        self.assertEqual(config["simulation"]["initial_cars"][0]["x"], 400)
        self.assertEqual(config["slots"][0]["route"][-2:], [[346.5, 30], [346.5, 17]])
        self.assertEqual(active["slots"][0]["x"], 346.5)

    def test_invalid_counts_overlap_offroad_and_wrong_areas_are_rejected(self):
        cases = [dict(car_count=31), dict(car_count=True), dict(car_count=1.1),
            dict(parking_count=100), dict(spawn_interval_s=float("nan")),
            dict(parking_count=4, initial_occupied_count=5),
            dict(initial_car_count=7, car_count=6), dict(initial_car_count=3, production_limit=2),
            dict(initial_car_count=1, initial_cars=[dict(x=400, y=250)]),
            dict(initial_car_count=2, initial_cars=[dict(x=400, y=205), dict(x=403, y=205)]),
            dict(slots=[dict(id="P01", x=344, y=30)]),
            dict(slots=[dict(id="P01", x=400, y=200)]),
            dict(slots=[dict(id="P01", x=350, y=18)]),
            dict(slots=[dict(id="UNKNOWN", x=350, y=18)]),
            dict(production_exit_id="unknown"), dict(preset="unknown")]
        for requested in cases:
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                compile_scenario(self.base, requested)

    def test_directed_shortest_path_rejects_unreachable_destination(self):
        nodes = dict(a=(0, 0), b=(5, 0), c=(0, 10))
        edges = [{"from": "a", "to": "b"}]
        self.assertEqual(shortest_path(nodes, edges, "a", "b"), [[0, 0], [5, 0]])
        with self.assertRaises(ValueError):
            shortest_path(nodes, edges, "b", "a")


class ScenarioAPITests(unittest.TestCase):
    def setUp(self):
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_id = "existing-run"
        self.app._new_run = Mock()
        self.server = create_http_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.app.close()

    def test_preview_changes_geometry_without_restart_or_config_mutation(self):
        original = copy.deepcopy(self.app.config)
        query = urlencode({"scenario": json.dumps(dict(parking_count=40, car_count=12, initial_car_count=5))})
        with urlopen(self.url + "/api/scenario?" + query) as response:
            payload = json.load(response)
        self.assertEqual(len(payload["config"]["slots"]), 40)
        self.assertEqual(payload["active"]["car_count"], 12)
        self.assertEqual(self.app.run_id, "existing-run")
        self.assertEqual(self.app.config, original)
        self.app._new_run.assert_not_called()

    def test_bad_preview_has_readable_400(self):
        query = urlencode({"scenario": json.dumps(dict(parking_count=1000))})
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/api/scenario?" + query)
        self.assertEqual(error.exception.code, 400)
        self.assertIn("parking_count", json.load(error.exception)["error"])

    def test_preview_occupancy_matches_seeded_physical_inventory(self):
        from factory_yard.model import World
        for seed in (0, 42, 123):
            payload = self.app.scenario(dict(preview_seed=seed, parking_count=40, initial_occupied_count=7))
            physical = World(payload["config"], "preview-check", seed)
            self.assertEqual(set(payload["preview_occupied_slots"]), physical.initial_occupied)
            self.assertEqual(self.app.seed, 42)

    def test_scenario_download_has_native_attachment_and_edited_seed(self):
        params = dict(car_count=12, parking_count=40, initial_car_count=5, preview_seed=99)
        query = urlencode(dict(scenario=json.dumps(params), strategy="fixed"))
        with urlopen(self.url + "/api/scenario/export?" + query) as response:
            exported = json.load(response)
            self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual((exported["seed"], exported["strategy"]), (99, "fixed"))
        self.assertEqual(exported["scenario"]["parking_count"], 40)
        self.app._new_run.assert_not_called()

    def test_configure_applies_seed_and_strategy_and_rejects_bad_seed_first(self):
        self.app.control(dict(action="configure", scenario={}, seed=123, strategy="fixed"))
        self.assertEqual((self.app.seed, self.app.strategy), (123, "fixed"))
        self.app._new_run.assert_called_once()
        self.app._new_run.reset_mock()
        original = copy.deepcopy(self.app.config)
        with self.assertRaises(ValueError):
            self.app.control(dict(action="configure", scenario=dict(parking_count=40), seed=-1))
        self.app._new_run.assert_not_called()
        self.assertEqual(self.app.config, original)

    def test_invalid_apply_does_not_stop_existing_run(self):
        original = copy.deepcopy(self.app.config)
        with self.assertRaises(ValueError):
            self.app.control(dict(action="configure", scenario=dict(car_count=31)))
        self.assertEqual(original, self.app.config)
        self.app._new_run.assert_not_called()
