"""Focused process tests; integration/MQTT tests live in separate modules."""

import copy
import json
from pathlib import Path
import unittest

from factory_yard.model import World


def configuration():
    return json.loads((Path(__file__).resolve().parents[1] / "config" / "default.json").read_text(encoding="utf-8"))


class SimpleProtocolPeer:
    """Test peer using emitted messages, never World.snapshot private truth."""
    def __init__(self, config):
        self.config = config
        self.reservations = {}
        self.zones = {}
        self.inventory = {}
        self.departures = set()
        self.zone_queues = {}

    def grant_next_zone(self, world, zone_id):
        pending = self.zone_queues.setdefault(zone_id, {})
        if not self.zones.get(zone_id) and pending:
            car_id = next(iter(pending))
            request = pending.pop(car_id)
            self.zones[zone_id] = car_id
            world.handle_command("commands/" + car_id, {
                "kind": "zone_grant", **request, "grant_id": car_id + "-grant"})

    def deliver(self, world, messages):
        for topic, payload in messages:
            if topic == "sensors/parking":
                self.inventory = {slot["id"]: slot for slot in payload["slots"]}
                for slot_id in list(self.departures):
                    if not self.inventory[slot_id]["occupied"]:
                        self.reservations.pop(slot_id, None)
                        self.departures.remove(slot_id)
            elif topic == "requests/parking":
                available = [slot for slot in self.config["slots"]
                             if slot["id"] not in self.reservations and slot["id"] in self.inventory
                             and not self.inventory[slot["id"]]["occupied"]
                             and not self.inventory[slot["id"]]["blocked"]]
                if available:
                    slot = min(available, key=lambda slot: sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** .5
                                                              for a, b in zip(slot["route"], slot["route"][1:])))
                    self.reservations[slot["id"]] = payload["car_id"]
                    world.handle_command("commands/" + payload["car_id"], {
                        "kind": "parking_grant", **payload, "slot_id": slot["id"],
                        "reservation_id": payload["car_id"] + "-reservation", "route": slot["route"]})
            elif topic == "requests/zone":
                if self.zones.get(payload["zone_id"]) != payload["car_id"]:
                    self.zone_queues.setdefault(payload["zone_id"], {}).setdefault(payload["car_id"], payload)
                    self.grant_next_zone(world, payload["zone_id"])
            elif topic == "events" and payload["type"] == "zone_exit":
                if self.zones.get(payload["zone_id"]) == payload["car_id"]:
                    self.zones.pop(payload["zone_id"])
                    self.grant_next_zone(world, payload["zone_id"])
            elif topic == "events" and payload["type"] == "departed":
                self.departures.add(payload["slot_id"])


class ModelTests(unittest.TestCase):
    def run_steps(self, world, peer, seconds, connected=True):
        for _ in range(round(seconds / .05)):
            peer.deliver(world, world.step(.05, connected=connected))

    def test_seeded_multi_vehicle_process_completes_without_collisions(self):
        config = configuration()
        world = World(config, "process-test", seed=42)
        peer = SimpleProtocolPeer(config)
        self.run_steps(world, peer, 300)
        state = world.snapshot()
        self.assertGreaterEqual(state["metrics"]["completed"], 6)
        self.assertEqual(state["metrics"]["collisions"], 0)
        self.assertLessEqual(state["metrics"]["active"], config["simulation"]["max_active"])
        self.assertGreater(state["metrics"]["pedestrian_stops"], 0)

    def test_no_zone_grant_never_enters_conflict_zone(self):
        config = configuration()
        config["simulation"]["automatic_pedestrians"] = False
        config["simulation"]["max_active"] = 1
        world = World(config, "no-grant", seed=42)
        first = world.step(.05)
        request = next(payload for topic, payload in first if topic == "requests/parking")
        slot = config["slots"][-1]
        world.handle_command("commands/" + request["car_id"], {
            "kind": "parking_grant", **request, "slot_id": slot["id"],
            "reservation_id": "reservation", "route": slot["route"]})
        for _ in range(2000):
            world.step(.05)
        car = world.snapshot()["cars"][0]
        self.assertLessEqual(car["x"] + config["vehicle"]["length"] / 2, 118 - config["vehicle"]["margin"] + 1e-6)
        self.assertAlmostEqual(car["speed"], 0)
        self.assertEqual(car["state"], "waiting_zone")

    def test_connection_loss_brakes_and_retains_reserved_slot(self):
        config = configuration()
        config["simulation"]["automatic_pedestrians"] = False
        config["simulation"]["max_active"] = 1
        world = World(config, "disconnect", seed=42)
        peer = SimpleProtocolPeer(config)
        self.run_steps(world, peer, 10)
        before = world.snapshot()["cars"][0]
        self.assertGreater(before["speed"], 0)
        self.run_steps(world, peer, 3, connected=False)
        after = world.snapshot()["cars"][0]
        self.assertEqual(after["state"], "communication_stop")
        self.assertEqual(after["slot_id"], before["slot_id"])
        self.assertAlmostEqual(after["speed"], 0)
        self.assertLessEqual(after["x"] - before["x"], config["vehicle"]["max_speed"] ** 2 / (2 * config["vehicle"]["braking"]) + .3)

    def test_sensor_freshness_failstop_and_recovery(self):
        config = configuration()
        config["simulation"]["automatic_pedestrians"] = False
        config["simulation"]["max_active"] = 1
        world = World(config, "sensor-fail", seed=42)
        peer = SimpleProtocolPeer(config)
        self.run_steps(world, peer, 5)
        world.control("sensor_failure", {"seconds": 4})
        messages = []
        for _ in range(60):
            emitted = world.step(.05)
            messages += emitted
            peer.deliver(world, emitted)
        self.assertFalse(any(topic.startswith("telemetry/") or topic == "sensors/parking" for topic, _ in messages))
        car = world.snapshot()["cars"][0]
        self.assertEqual(car["state"], "sensor_stop")
        self.assertAlmostEqual(car["speed"], 0)
        self.run_steps(world, peer, 3)
        self.assertGreater(world.snapshot()["cars"][0]["speed"], 0)

    def test_duplicate_grant_does_not_restart_route(self):
        config = configuration()
        config["simulation"]["automatic_pedestrians"] = False
        world = World(config, "duplicate", seed=42)
        request = next(payload for topic, payload in world.step(.05) if topic == "requests/parking")
        slot = config["slots"][-1]
        command = {"kind": "parking_grant", **request, "slot_id": slot["id"],
                   "reservation_id": "res-one", "route": copy.deepcopy(slot["route"])}
        world.handle_command("commands/" + request["car_id"], command)
        for _ in range(30):
            world.step(.05)
        progress = world.cars[request["car_id"]].progress
        world.handle_command("commands/" + request["car_id"], command)
        self.assertEqual(world.cars[request["car_id"]].progress, progress)

    def test_nominal_braking_bound_and_finite_production_cohort(self):
        for seed in (1, 2, 42):
            with self.subTest(seed=seed):
                config = configuration()
                config["simulation"]["production_limit"] = 6
                world = World(config, "braking-test", seed=seed)
                peer = SimpleProtocolPeer(config)
                for _ in range(6000):
                    previous = {car.id: car.speed for car in world.cars.values()}
                    peer.deliver(world, world.step(.05))
                    for car in world.cars.values():
                        if car.id in previous:
                            self.assertLessEqual(previous[car.id] - car.speed, config["vehicle"]["braking"] * .05 + 1e-6,
                                                 f"time={world.sim_time}, car={car.id}, pose={car.x},{car.y}, state={car.state}, reason={car.wait_reason}")
                metrics = world.snapshot()["metrics"]
                self.assertEqual(metrics["offered"], 6)
                self.assertEqual(metrics["completed"], 6)
                self.assertEqual(metrics["unfinished"], 0)
                self.assertEqual(metrics["collisions"], 0)

    def test_late_curb_pedestrian_yields_to_committed_vehicle(self):
        config = configuration()
        config["simulation"]["automatic_pedestrians"] = False
        config["simulation"]["production_limit"] = 1
        world = World(config, "late-curb", seed=42)
        peer = SimpleProtocolPeer(config)
        self.run_steps(world, peer, 10)
        before = world.snapshot()["cars"][0]
        self.assertGreater(before["x"], 39)
        world.control("pedestrian", {"crossing_id": "ZEBRA1"})
        self.run_steps(world, peer, 3)
        after = world.snapshot()["cars"][0]
        self.assertGreater(after["x"], before["x"] + 6)
        self.assertEqual(world.snapshot()["metrics"]["collisions"], 0)
        self.assertTrue(any(event["type"] == "pedestrian_crossing" for event in world.snapshot()["events"]))


if __name__ == "__main__":
    unittest.main()
