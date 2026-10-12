"""Directed multi-junction traffic checks using actual protocol decisions.

This is a deterministic model-level integration, with explicit logical transport
timestamps. The real MQTT test is separately executed by the application tests.
"""
import json
from pathlib import Path
import unittest

from factory_yard.coordinator import Coordinator
from factory_yard.model import World


def diamond_configuration():
    config = json.loads((Path(__file__).resolve().parents[1] / "config" / "default.json").read_text(encoding="utf-8"))
    config["network"] = {"directed": True, "nodes": [], "edges": []}
    config["world"] = {"width": 300, "height": 130, "origin": [10, 60]}
    config["simulation"].update(max_active=30, production_limit=30, parking_dwell=100_000,
                                 initial_occupied_count=0, automatic_pedestrians=False, spawn_interval=3.0)
    config["slots"] = []
    for index in range(32):
        upper = index % 2 == 0
        x = 160 + 8 * (index // 2)
        branch_y, slot_y = (20, 35) if upper else (100, 85)
        config["slots"].append(dict(id=f"P{index + 1:02d}", x=x, y=slot_y,
            route=[[10, 60], [60, 60], [60, branch_y], [140, branch_y], [140, 60], [x, 60], [x, slot_y]]))
    config["zones"] = [dict(id=zid, x=x, y=y, w=12, h=12) for zid, x, y in
                       (("SPLIT", 60, 60), ("NW", 60, 20), ("NE", 140, 20),
                        ("SW", 60, 100), ("SE", 140, 100), ("MERGE", 140, 60))]
    config["crossings"] = []
    return config


class Wire:
    """Only the emitted PubSub payloads cross the process/control boundary."""
    def __init__(self, config, seed=42):
        self.world = World(config, "network-test", seed)
        self.coordinator = Coordinator(config, "network-test")
        self.messages = []

    def step(self, dt=.05):
        now = 1_000_000_000_000 + round((self.world.sim_time + dt) * 1e9)
        frames = self.world.step(dt)
        for topic, payload in frames:
            framed = dict(payload, run_id="network-test", sent_ns=now)
            for command_topic, command in self.coordinator.handle(topic, framed, now_ns=now):
                self.world.handle_command(command_topic, dict(command, run_id="network-test"))
            if topic == "events":
                self.messages.append(payload)
        return frames


class NetworkCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.config = diamond_configuration()
        self.c = Coordinator(self.config, "network-test")
        self.now = 1_000_000_000_000
        self.send("sensors/parking", seq=1, slots=[dict(id=slot["id"], occupied=False) for slot in self.config["slots"]])

    def send(self, topic, **payload):
        return self.c.handle(topic, dict(payload, run_id="network-test", sent_ns=self.now,
                                         sim_time=self.now / 1e9), now_ns=self.now)

    def assign(self, car, slot_index):
        slot = self.c.slots[self.config["slots"][slot_index]["id"]]
        slot.update(car_id=car, reservation_id=f"{car}-reservation")

    def telemetry(self, car, x, y, seq=1, state="waiting_zone"):
        self.send("telemetry/" + car, car_id=car, seq=seq, x=x, y=y, speed=0, state=state)

    def zone(self, car, zone):
        return self.send("requests/zone", car_id=car, request_id=f"{car}-{zone}", zone_id=zone)[0][1]

    def test_northbound_follower_cannot_lock_leaders_corner(self):
        self.assign("LEADER", 0)
        self.assign("FOLLOWER", 2)
        self.telemetry("LEADER", 60, 35)
        self.telemetry("FOLLOWER", 60, 44)
        self.assertEqual(self.zone("FOLLOWER", "NW")["kind"], "zone_wait")
        self.assertIsNone(self.c.zones["NW"]["owner"])
        self.assertEqual(self.zone("LEADER", "NW")["kind"], "zone_grant")

    def test_southbound_follower_guard_uses_route_direction(self):
        self.assign("LEADER", 1)
        self.assign("FOLLOWER", 3)
        self.telemetry("LEADER", 60, 85)
        self.telemetry("FOLLOWER", 60, 76)
        self.assertEqual(self.zone("FOLLOWER", "SW")["kind"], "zone_wait")
        self.assertEqual(self.zone("LEADER", "SW")["kind"], "zone_grant")

    def test_two_approaches_never_share_merge_and_release_requires_pose(self):
        self.assign("NORTH", 0)
        self.assign("SOUTH", 1)
        self.telemetry("NORTH", 140, 45)
        self.telemetry("SOUTH", 140, 75)
        grant = self.zone("NORTH", "MERGE")
        self.assertEqual(grant["kind"], "zone_grant")
        self.assertEqual(self.zone("SOUTH", "MERGE")["kind"], "zone_wait")
        self.send("events", type="zone_exit", car_id="NORTH", zone_id="MERGE", grant_id=grant["grant_id"])
        self.assertEqual(self.c.zones["MERGE"]["owner"], "NORTH")
        self.telemetry("NORTH", 150, 60, seq=2, state="driving")
        self.assertIsNone(self.c.zones["MERGE"]["owner"])
        self.assertEqual(self.zone("SOUTH", "MERGE")["kind"], "zone_grant")

    def test_cannot_reserve_far_future_or_unrelated_zone(self):
        self.assign("A", 0)
        self.telemetry("A", 20, 60)
        future = self.zone("A", "NW")
        self.assertEqual(future["kind"], "zone_wait")
        self.assertEqual(future["reason_code"], "earlier_route_zone_not_cleared")
        unrelated = self.zone("A", "SW")
        self.assertEqual(unrelated["reason_code"], "zone_not_on_assigned_route")
        self.assertTrue(all(zone["owner"] is None for zone in self.c.zones.values()))

    def test_no_route_or_past_zone_never_creates_owner(self):
        self.telemetry("A", 40, 60)
        self.assertEqual(self.zone("A", "SPLIT")["reason_code"], "no_assigned_route")
        self.assign("A", 0)
        self.telemetry("A", 60, 40, seq=2)
        self.assertEqual(self.zone("A", "SPLIT")["reason_code"], "zone_already_passed")
        self.assertIsNone(self.c.zones["SPLIT"]["owner"])

    def test_new_zone_waits_for_previous_ownership_release(self):
        self.assign("A", 0)
        self.telemetry("A", 60, 35)
        self.zone("A", "NW")
        self.telemetry("A", 126, 20, seq=2)
        response = self.zone("A", "NE")
        self.assertEqual(response["reason_code"], "other_zone_still_owned")
        self.assertIsNone(self.c.zones["NE"]["owner"])


class NetworkModelTests(unittest.TestCase):
    def test_initial_cars_keep_pose_and_statistics_without_backtracking(self):
        config = diamond_configuration()
        config["simulation"].update(initial_cars=[dict(id="CAR001", x=20, y=60), dict(id="CAR002", x=30, y=60)],
                                     max_active=2, production_limit=2)
        wire = Wire(config)
        initial = {car.id: (car.x, car.y) for car in wire.world.cars.values()}
        wire.step()
        for car in wire.world.cars.values():
            self.assertEqual((car.x, car.y), initial[car.id])
            self.assertAlmostEqual(car.progress, initial[car.id][0] - 10)
            self.assertEqual(car.distance, 0)
        self.assertEqual(wire.world.snapshot()["metrics"]["offered"], 2)
        self.assertEqual(wire.world.snapshot()["metrics"]["admitted"], 2)
        for _ in range(30):
            wire.step()
        self.assertGreater(wire.world.cars["CAR002"].x, 30)
        self.assertEqual(wire.world.snapshot()["metrics"]["collisions"], 0)

    def test_initial_car_off_route_is_rejected_without_teleport(self):
        config = diamond_configuration()
        config["simulation"].update(initial_cars=[dict(id="CAR001", x=20, y=62)], production_limit=1)
        world = World(config, "network-test")
        slot = config["slots"][0]
        world.handle_command("commands/CAR001", dict(kind="parking_grant", car_id="CAR001", request_id="CAR001-parking",
            slot_id=slot["id"], reservation_id="one", route=slot["route"]))
        self.assertEqual((world.cars["CAR001"].x, world.cars["CAR001"].y), (20, 62))
        self.assertIsNone(world.cars["CAR001"].slot_id)
        self.assertEqual(world._events[-1]["reason"], "route_pose_mismatch")

    def test_different_route_to_known_slot_is_rejected_before_moving(self):
        config = diamond_configuration()
        config["simulation"].update(initial_cars=[dict(id="CAR001", x=20, y=60)], production_limit=1)
        world = World(config, "network-test")
        slot = config["slots"][0]
        world.handle_command("commands/CAR001", dict(kind="parking_grant", car_id="CAR001", request_id="CAR001-parking",
            slot_id=slot["id"], reservation_id="one", route=[[10, 60], [slot["x"], slot["y"]]]))
        self.assertIsNone(world.cars["CAR001"].slot_id)
        self.assertEqual(world._events[-1]["reason"], "route_configuration_mismatch")

    def test_initial_vehicle_count_cannot_bypass_capacity_or_cohort(self):
        config = diamond_configuration()
        config["simulation"].update(max_active=1, initial_cars=[dict(id="A", x=20, y=60), dict(id="B", x=30, y=60)])
        with self.assertRaises(ValueError):
            World(config, "invalid")
        config["simulation"].update(max_active=3, production_limit=1)
        with self.assertRaises(ValueError):
            World(config, "invalid")

    def test_initial_custom_identifiers_are_not_reused_by_production(self):
        config = diamond_configuration()
        config["simulation"].update(initial_cars=[dict(id="CAR002", x=30, y=60)], production_limit=2)
        wire = Wire(config)
        for _ in range(80):
            wire.step()
        self.assertEqual(set(wire.world.cars), {"CAR001", "CAR002"})
        self.assertEqual(wire.world.snapshot()["metrics"]["offered"], 2)

    def test_pedestrian_waits_for_initial_unassigned_car_on_crossing(self):
        config = diamond_configuration()
        config["crossings"] = [dict(id="X", x=30, y=60, axis="vertical")]
        config["simulation"].update(initial_cars=[dict(id="CAR001", x=30, y=60)], production_limit=1,
                                     initial_occupied_count=len(config["slots"]))
        wire = Wire(config)
        wire.world.control("pedestrian", dict(crossing_id="X"))
        for _ in range(120):
            wire.step()
        self.assertEqual(wire.world.pedestrians[0]["state"], "waiting")
        self.assertEqual(wire.world.snapshot()["metrics"]["collisions"], 0)

    def test_only_next_zone_requested_until_previous_whole_car_exit(self):
        config = diamond_configuration()
        config["zones"] = [dict(id="FIRST", x=50, y=60, w=8, h=8), dict(id="SECOND", x=70, y=60, w=8, h=8)]
        config["slots"] = [dict(id="P1", x=90, y=60, route=[[10, 60], [90, 60]])]
        config["simulation"]["production_limit"] = 1
        world = World(config, "network-test")
        first = world.step(.05)
        world.handle_command("commands/CAR001", dict(kind="parking_grant", car_id="CAR001", request_id="CAR001-parking",
            slot_id="P1", reservation_id="one", route=config["slots"][0]["route"]))
        world.cars["CAR001"].progress = 35
        world.cars["CAR001"].x = 45
        requests = [payload["zone_id"] for topic, payload in world.step(.05) if topic == "requests/zone"]
        self.assertEqual(requests, ["FIRST"])

    def test_thirty_car_two_branch_six_zone_cohort_completes_without_collisions(self):
        wire = Wire(diamond_configuration(), seed=17)
        for _ in range(24_000):
            wire.step()
            if wire.world.snapshot()["metrics"]["completed"] == 30:
                break
        metrics = wire.world.snapshot()["metrics"]
        self.assertEqual(metrics["completed"], 30, str(metrics))
        self.assertEqual(metrics["collisions"], 0)
        exits = {event["zone_id"] for event in wire.messages if event["type"] == "zone_exit"}
        self.assertEqual(exits, {"SPLIT", "NW", "NE", "SW", "SE", "MERGE"})
        self.assertTrue(all(zone["owner"] is None for zone in wire.coordinator.zones.values()))

    def test_gyor_alternative_exit_south_thirty_car_regression(self):
        """The alternative feeder must never cross the southbound route.

        This maximum initial-cohort test caught a real geometry error during
        development: a feeder ending west of MAIN crossed the south approach.
        The fixed map joins the feeder east of MAIN and keeps every conflicting
        corner protected by sequentially requested resources.
        """
        from factory_yard.scenario import compile_scenario
        base = json.loads((Path(__file__).resolve().parents[1] / "config" / "default.json").read_text(encoding="utf-8"))
        _, config = compile_scenario(base, dict(car_count=30, parking_count=64, initial_car_count=30,
            initial_occupied_count=4, production_limit=30, production_exit_id="EXIT70",
            parking_area_id="south", parking_dwell_s=600))
        wire = Wire(config, seed=17)
        for index in range(20_000):
            wire.step()
            if index % 20 == 0 and wire.world.snapshot()["metrics"]["completed"] == 30:
                break
        metrics = wire.world.snapshot()["metrics"]
        self.assertEqual(metrics["completed"], 30, str(metrics))
        self.assertEqual(metrics["collisions"], 0)
        self.assertGreater(metrics["pedestrian_stops"], 0)
        self.assertTrue(all(zone["owner"] is None for zone in wire.coordinator.zones.values()))


if __name__ == "__main__":
    unittest.main()
