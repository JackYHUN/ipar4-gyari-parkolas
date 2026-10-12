"""Safety-relevant reservation checks, independent of the graphical view."""
import json
import pathlib
import unittest

from factory_yard.coordinator import Coordinator


CONFIG = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config" / "default.json").read_text(encoding="utf-8"))


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000_000_000
        self.c = Coordinator(CONFIG, "test", "nearest")

    def send(self, topic, **payload):
        payload.setdefault("run_id", "test")
        payload.setdefault("sim_time", self.now / 1e9)
        payload.setdefault("sent_ns", self.now)
        return self.c.handle(topic, payload, now_ns=self.now)

    def sensing(self, seq=1, occupied=(), blocked=()):
        return self.send("sensors/parking", seq=seq, slots=[
            {"id": slot["id"], "occupied": slot["id"] in occupied,
             "blocked": slot["id"] in blocked} for slot in CONFIG["slots"]])

    def parking(self, car="A", request="request-A"):
        return self.send("requests/parking", car_id=car, request_id=request, origin=[14, 58])[0][1]

    def telemetry(self, car="A", seq=1, x=113, y=58, sim_time=None):
        p = dict(car_id=car, seq=seq, x=x, y=y, speed=0, state="waiting_zone")
        if sim_time is not None:
            p["sim_time"] = sim_time
        return self.send("telemetry/" + car, **p)

    def zone(self, car="A", request="zone-A"):
        return self.send("requests/zone", car_id=car, request_id=request, zone_id="C1")[0][1]

    def test_no_parking_grant_before_first_sensor_sample(self):
        self.assertEqual(self.parking()["kind"], "parking_wait")

    def test_two_close_requests_never_share_a_space(self):
        self.sensing()
        a, b = self.parking(), self.parking("B", "request-B")
        self.assertEqual(a["kind"], "parking_grant")
        self.assertNotEqual(a["slot_id"], b["slot_id"])
        self.assertNotEqual(a["reservation_id"], b["reservation_id"])

    def test_duplicate_request_reuses_grant_without_second_reservation(self):
        self.sensing()
        first, repeat = self.parking(), self.parking()
        self.assertEqual(first, repeat)
        self.assertEqual(sum(s["car_id"] == "A" for s in self.c.slots.values()), 1)

    def test_new_request_id_cannot_reserve_twice_for_same_car(self):
        self.sensing()
        self.parking()
        self.assertEqual(self.parking(request="second")["kind"], "parking_wait")

    def test_nearest_rule_uses_route_length_and_fixed_rule_uses_id(self):
        self.sensing()
        self.assertEqual(self.parking()["slot_id"], "P08")
        self.c = Coordinator(CONFIG, "test", "fixed")
        self.sensing()
        self.assertEqual(self.parking()["slot_id"], "P01")

    def test_occupied_and_blocked_spaces_are_excluded(self):
        self.sensing(occupied=("P08",), blocked=("P16",))
        self.assertEqual(self.parking()["slot_id"], "P07")

    def test_reordered_sensor_sample_cannot_overwrite_or_refresh_state(self):
        self.sensing(seq=4, occupied=("P08",))
        self.now += 1_000_000_000
        self.sensing(seq=3)
        self.assertTrue(self.c.slots["P08"]["occupied"])
        self.assertEqual(self.c.sensor_received_ns, 10_000_000_000)
        self.now += 2_000_000_000
        self.assertEqual(self.parking()["kind"], "parking_wait")

    def test_stale_payload_and_foreign_run_do_not_update_sensor(self):
        slots = [{"id": s["id"], "occupied": False} for s in CONFIG["slots"]]
        self.send("sensors/parking", seq=1, slots=slots, sent_ns=0)
        self.assertEqual(self.c.sensor_seq, -1)
        self.send("sensors/parking", seq=1, slots=slots, run_id="previous")
        self.assertEqual(self.c.sensor_seq, -1)

    def test_timeout_never_releases_logical_reservation(self):
        self.sensing()
        grant = self.parking()
        self.now += 100_000_000_000
        self.sensing(seq=2)
        other = self.parking("B", "request-B")
        self.assertNotEqual(grant["slot_id"], other["slot_id"])
        self.assertEqual(self.c.slots[grant["slot_id"]]["car_id"], "A")

    def test_departure_requires_matching_id_and_later_empty_sensor(self):
        self.sensing()
        grant = self.parking()
        slot = grant["slot_id"]
        self.send("events", type="departed", car_id="A", slot_id=slot, reservation_id="wrong")
        self.sensing(seq=2)
        self.assertEqual(self.c.slots[slot]["car_id"], "A")
        self.send("events", type="departed", car_id="A", slot_id=slot, reservation_id=grant["reservation_id"])
        self.sensing(seq=2)  # Equal sequence cannot certify a later observation.
        self.assertEqual(self.c.slots[slot]["car_id"], "A")
        self.sensing(seq=3, occupied=(slot,))
        self.assertEqual(self.c.slots[slot]["car_id"], "A")
        self.sensing(seq=4)
        self.assertIsNone(self.c.slots[slot]["car_id"])

    def test_late_duplicate_request_cannot_replay_closed_reservation(self):
        self.sensing()
        grant = self.parking()
        self.send("events", type="departed", car_id="A", slot_id=grant["slot_id"],
                  reservation_id=grant["reservation_id"])
        self.sensing(seq=2)
        self.assertEqual(self.parking()["kind"], "parking_wait")
        self.assertFalse(any(s["car_id"] == "A" for s in self.c.slots.values()))

    def test_zone_requires_recent_telemetry(self):
        self.assertEqual(self.zone()["kind"], "zone_wait")
        self.telemetry()
        self.now += 3_000_000_000
        self.assertEqual(self.zone()["kind"], "zone_wait")

    def test_conflicting_zone_requests_and_duplicate_grant(self):
        self.telemetry("A")
        self.telemetry("B")
        first = self.zone()
        self.assertEqual(first["kind"], "zone_grant")
        self.assertEqual(self.zone(), first)
        self.assertEqual(self.zone("B", "zone-B")["kind"], "zone_wait")

    def test_following_car_cannot_lock_zone_ahead_of_physical_leader(self):
        self.telemetry("A", x=112, y=58)
        self.telemetry("B", x=104, y=58)
        self.assertEqual(self.zone("B", "zone-B")["kind"], "zone_wait")
        self.assertIsNone(self.c.zones["C1"]["owner"])
        self.assertEqual(self.zone("A", "zone-A")["kind"], "zone_grant")
        self.assertEqual(self.c.zones["C1"]["owner"], "A")

    def test_zone_timeout_does_not_free_occupied_area(self):
        self.telemetry()
        self.zone()
        self.now += 100_000_000_000
        self.telemetry("B")
        self.assertEqual(self.zone("B", "zone-B")["kind"], "zone_wait")
        self.assertEqual(self.c.zones["C1"]["owner"], "A")

    def test_zone_exit_needs_matching_grant_and_whole_vehicle_outside(self):
        self.telemetry(x=122, y=58)
        grant = self.zone()
        self.send("events", type="zone_exit", car_id="A", zone_id="C1", grant_id="wrong")
        self.telemetry(seq=2, x=122, y=50)
        self.assertEqual(self.c.zones["C1"]["owner"], "A")
        self.telemetry(seq=3, x=122, y=54)
        self.send("events", type="zone_exit", car_id="A", zone_id="C1", grant_id=grant["grant_id"])
        self.assertEqual(self.c.zones["C1"]["owner"], "A")
        self.telemetry(seq=4, x=122, y=51.9)
        self.assertEqual(self.c.zones["C1"]["owner"], "A")
        self.telemetry(seq=5, x=122, y=51.7)
        self.assertIsNone(self.c.zones["C1"]["owner"])

    def test_zone_exit_cannot_use_older_simulation_sample(self):
        self.telemetry(x=122, y=50, sim_time=9)
        grant = self.zone()
        self.send("events", type="zone_exit", car_id="A", zone_id="C1", grant_id=grant["grant_id"], sim_time=10)
        self.assertEqual(self.c.zones["C1"]["owner"], "A")
        self.telemetry(seq=2, x=122, y=50, sim_time=10)
        self.assertIsNone(self.c.zones["C1"]["owner"])


if __name__ == "__main__":
    unittest.main()
