"""Only complete, typed inventory snapshots may certify parking state."""

import copy
import unittest

from factory_yard.coordinator import Coordinator
from tests.test_model import configuration


class InventoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.now = 10_000_000_000
        self.controller = Coordinator(self.config, "inventory-validation")

    def send(self, topic, **payload):
        payload.setdefault("run_id", "inventory-validation")
        payload.setdefault("sim_time", self.now / 1e9)
        payload.setdefault("sent_ns", self.now)
        return self.controller.handle(topic, payload, now_ns=self.now)

    def inventory(self, occupied=(), blocked=()):
        return [{"id": slot["id"], "occupied": slot["id"] in occupied,
                 "blocked": slot["id"] in blocked} for slot in self.config["slots"]]

    def parking(self, car="A"):
        return self.send("requests/parking", car_id=car,
                         request_id="parking-" + car, origin=[14, 58])[0][1]

    def advance_past_freshness(self):
        self.now += int((self.config["mqtt"]["freshness_s"] + 1) * 1e9)

    def physical_state(self):
        c = self.controller
        return copy.deepcopy({"slots": c.slots, "pending_departures": c.pending_departures,
                              "sensor_seq": c.sensor_seq,
                              "sensor_received_ns": c.sensor_received_ns,
                              "sim_time": c.sim_time, "serial": c.serial,
                              "responses": c.responses, "car_slots": c._car_slots})

    def pending_departure(self):
        self.send("sensors/parking", seq=1, slots=self.inventory())
        grant = self.parking()
        self.send("sensors/parking", seq=2, slots=self.inventory(occupied=(grant["slot_id"],)))
        self.send("events", type="departed", car_id="A", slot_id=grant["slot_id"],
                  reservation_id=grant["reservation_id"])
        self.controller.drain_audit()
        self.advance_past_freshness()
        # Put the soon-to-be-empty reserved space first. A malformed tail must
        # not apply this prefix or release the matching pending departure.
        readings = self.inventory()
        readings.sort(key=lambda reading: reading["id"] != grant["slot_id"])
        return grant, readings

    def assert_rejected_without_state_change(self, readings, reason, seq=3):
        before = self.physical_state()
        self.controller.drain_audit()
        self.assertEqual(self.send("sensors/parking", seq=seq, slots=readings, sim_time=900), [])
        self.assertEqual(self.physical_state(), before)
        audits = self.controller.drain_audit()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["event_type"], "message_rejected")
        self.assertEqual(audits[0]["reason"], reason)
        self.assertFalse(self.controller._fresh(self.now))
        self.assertFalse(any(record["event_type"] == "parking_released" for record in audits))

    def test_stale_empty_snapshot_cannot_refresh_inventory_or_grant_a_space(self):
        self.send("sensors/parking", seq=1, slots=self.inventory())
        self.advance_past_freshness()
        self.assert_rejected_without_state_change([], "incomplete_sensor_frame", seq=2)
        self.assertEqual(self.parking()["kind"], "parking_wait")
        self.assertFalse(any(slot["car_id"] for slot in self.controller.slots.values()))

    def test_missing_and_duplicate_ids_cannot_apply_prefix_or_release_reservation(self):
        for malformed in ("missing", "duplicate", "duplicate-extra"):
            with self.subTest(malformed=malformed):
                self.setUp()
                grant, readings = self.pending_departure()
                if malformed == "missing":
                    readings.pop()
                    reason = "incomplete_sensor_frame"
                elif malformed == "duplicate":
                    readings[-1] = copy.deepcopy(readings[0])
                    reason = "duplicate_slot_id"
                else:
                    readings.append(copy.deepcopy(readings[0]))
                    reason = "duplicate_slot_id"
                self.assert_rejected_without_state_change(readings, reason)
                self.assertEqual(self.controller.slots[grant["slot_id"]]["car_id"], "A")
                self.assertIn(grant["slot_id"], self.controller.pending_departures)

    def test_unknown_slot_at_tail_cannot_certify_reserved_space_is_empty(self):
        grant, readings = self.pending_departure()
        readings[-1]["id"] = "FOREIGN-PARKING"
        self.assert_rejected_without_state_change(readings, "unknown_slot_id")
        self.assertEqual(self.controller.slots[grant["slot_id"]]["reservation_id"], grant["reservation_id"])

    def test_boolean_readings_are_required_without_truthiness_coercion(self):
        for field in ("occupied", "blocked"):
            for value in (0, 1, "false", "true", None, [], {}):
                with self.subTest(field=field, value=value):
                    self.setUp()
                    _, readings = self.pending_departure()
                    readings[-1][field] = value
                    self.assert_rejected_without_state_change(readings, "invalid_slot_reading")

    def test_malformed_reading_at_tail_cannot_mutate_clock_or_other_spaces(self):
        for bad_reading in (None, [], "P01", {}, {"id": "P01"},
                            {"id": [], "occupied": False},
                            {"id": "", "occupied": False}):
            with self.subTest(reading=bad_reading):
                self.setUp()
                _, readings = self.pending_departure()
                readings[-1] = bad_reading
                self.assert_rejected_without_state_change(readings, "invalid_slot_reading")

    def test_boolean_and_noninteger_sequence_values_are_invalid(self):
        for seq in (True, False, 3.0, "3", None, []):
            with self.subTest(seq=seq):
                self.setUp()
                _, readings = self.pending_departure()
                self.assert_rejected_without_state_change(readings, "invalid_sensor_frame", seq=seq)

    def test_repeated_and_old_complete_snapshot_cannot_refresh_or_release(self):
        for seq in (2, 1, -1):
            with self.subTest(seq=seq):
                self.setUp()
                _, readings = self.pending_departure()
                self.assert_rejected_without_state_change(readings, "non_increasing_sequence", seq=seq)

    def test_valid_recovery_reuses_rejected_sequence_and_certifies_departure(self):
        grant, readings = self.pending_departure()
        self.assert_rejected_without_state_change(readings[:-1], "incomplete_sensor_frame")
        # Ordering is immaterial; blocked is optional and defaults to False.
        valid = [{"id": reading["id"], "occupied": False} for reading in reversed(readings)]
        original = copy.deepcopy(valid)
        self.send("sensors/parking", seq=3, slots=valid)
        self.assertEqual(valid, original)
        self.assertTrue(self.controller._fresh(self.now))
        self.assertEqual(self.controller.sensor_seq, 3)
        self.assertEqual(self.controller.sensor_received_ns, self.now)
        self.assertIsNone(self.controller.slots[grant["slot_id"]]["car_id"])
        self.assertNotIn(grant["slot_id"], self.controller.pending_departures)
        audits = self.controller.drain_audit()
        release = next(record for record in audits if record["event_type"] == "parking_released")
        self.assertEqual(release["after_sensor_seq"], 2)
        self.assertEqual(release["confirming_sensor_seq"], 3)
        self.assertTrue(any(record["event_type"] == "parking_sensor_fresh"
                            and record["reason"] == "freshness_recovered" for record in audits))
        self.assertEqual(self.parking("B")["kind"], "parking_grant")

    def test_initial_malformed_container_cannot_create_sensor_evidence(self):
        for slots in (None, {}, "not-a-list", []):
            with self.subTest(slots=slots):
                self.setUp()
                self.assert_rejected_without_state_change(slots,
                    "incomplete_sensor_frame" if slots == [] else "invalid_sensor_frame", seq=1)
                self.assertEqual(self.parking()["kind"], "parking_wait")
                self.assertIsNone(self.controller.sensor_received_ns)


if __name__ == "__main__":
    unittest.main()
