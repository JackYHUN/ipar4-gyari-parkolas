"""Decision evidence: reasons, correlation, rejection and release certificates."""
import json
from pathlib import Path
import unittest

from factory_yard.coordinator import Coordinator

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config/default.json").read_text(encoding="utf-8"))


class CoordinatorAuditTests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000_000_000
        self.c = Coordinator(CONFIG, "audit-test", "nearest")

    def send(self, topic, **payload):
        payload.setdefault("run_id", "audit-test")
        payload.setdefault("sent_ns", self.now)
        payload.setdefault("sim_time", self.now / 1e9)
        return self.c.handle(topic, payload, now_ns=self.now)

    def sensing(self, seq=1, occupied=()):
        return self.send("sensors/parking", seq=seq, slots=[
            {"id": s["id"], "occupied": s["id"] in occupied, "blocked": False} for s in CONFIG["slots"]])

    def parking(self):
        return self.send("requests/parking", car_id="A", request_id="park-A", origin=[14, 58])[0][1]

    def test_selected_candidate_cost_and_replay_are_correlated_without_new_reservation(self):
        self.sensing()
        self.c.drain_audit()
        grant = self.parking()
        record = self.c.drain_audit()[0]
        self.assertEqual(record["event_type"], "parking_granted")
        self.assertEqual(record["category"], "decision")
        self.assertEqual(record["request_id"], "park-A")
        self.assertEqual(record["reservation_id"], grant["reservation_id"])
        self.assertEqual(record["selected_route_length_m"], min(candidate["route_length_m"] for candidate in record["candidate_routes"]))
        serial = self.c.serial
        self.assertEqual(self.parking(), grant)
        replay = self.c.drain_audit()[0]
        self.assertEqual(replay["event_type"], "parking_grant_replayed")
        self.assertEqual(replay["reason"], "idempotent_request")
        self.assertEqual(replay["reservation_id"], record["reservation_id"])
        self.assertEqual(self.c.serial, serial)
        self.assertEqual(self.c.drain_audit(), [])

    def test_equal_waits_are_aggregated_with_explicit_repeat_count(self):
        self.parking()
        first = self.c.drain_audit()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["repeats"], 1)
        self.now += 500_000_000
        self.parking()
        self.parking()
        self.assertEqual(self.c.drain_audit(), [])
        self.now += 6_000_000_000
        self.parking()
        repeated = self.c.drain_audit()
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0]["repeats"], 3)
        self.assertEqual(repeated[0]["reason"], "stale_parking_sensor")

    def test_rejected_frame_reasons_are_visible_and_do_not_refresh_physical_evidence(self):
        self.sensing(seq=4)
        self.c.drain_audit()
        accepted_at = self.c.sensor_received_ns
        self.sensing(seq=3)
        self.send("sensors/parking", seq=5, slots=[], run_id="previous-run")
        self.send("sensors/parking", seq=5, slots=[], sent_ns=0)
        self.send("sensors/parking", seq=5, slots="not-a-list")
        records = self.c.drain_audit()
        self.assertEqual({record["reason"] for record in records}, {
            "non_increasing_sequence", "wrong_run_id", "stale_message", "invalid_sensor_frame"})
        self.assertTrue(all(record["category"] == "transport" and record["level"] == "warning" for record in records))
        self.assertEqual(self.c.sensor_seq, 4)
        self.assertEqual(self.c.sensor_received_ns, accepted_at)

    def test_parking_release_records_later_empty_frame_certificate(self):
        self.sensing()
        grant = self.parking()
        self.c.drain_audit()
        self.send("events", type="departed", car_id="A", slot_id=grant["slot_id"], reservation_id=grant["reservation_id"])
        self.sensing(seq=2, occupied=(grant["slot_id"],))
        self.assertEqual(self.c.slots[grant["slot_id"]]["car_id"], "A")
        self.sensing(seq=3)
        release = next(record for record in self.c.drain_audit() if record["event_type"] == "parking_released")
        self.assertEqual(release["car_id"], "A")
        self.assertEqual(release["reservation_id"], grant["reservation_id"])
        self.assertLess(release["after_sensor_seq"], release["confirming_sensor_seq"])
        self.assertFalse(release["occupied"])
        self.assertIsNone(self.c.slots[grant["slot_id"]]["car_id"])

    def test_zone_release_requires_and_records_full_vehicle_pose_confirmation(self):
        self.send("telemetry/A", car_id="A", seq=1, x=122, y=58, state="waiting_zone")
        grant = self.send("requests/zone", car_id="A", request_id="zone-A", zone_id="C1")[0][1]
        self.c.drain_audit()
        self.send("events", type="zone_exit", car_id="A", zone_id="C1", grant_id=grant["grant_id"])
        self.assertEqual(self.c.zones["C1"]["owner"], "A")
        self.send("telemetry/A", car_id="A", seq=2, x=122, y=51.7, state="driving")
        release = next(record for record in self.c.drain_audit() if record["event_type"] == "zone_released")
        self.assertTrue(release["whole_vehicle_outside"])
        self.assertEqual(release["grant_id"], grant["grant_id"])
        self.assertEqual(release["telemetry_seq"], 2)
        self.assertGreaterEqual(release["telemetry_sim_time"], release["exit_event_sim_time"])
        self.assertIsNone(self.c.zones["C1"]["owner"])

    def test_bounded_unconsumed_audit_discloses_dropped_diagnostics(self):
        for _ in range(1107):
            self.send("requests/parking", run_id="foreign", car_id="A", request_id="park-A")
        records = self.c.drain_audit()
        self.assertEqual(len(records), 1001)
        self.assertEqual(records[0]["event_type"], "audit_records_dropped")
        self.assertEqual(records[0]["dropped_records"], 107)
        self.assertEqual(self.c.drain_audit(), [])
        self.assertFalse(any(slot["car_id"] for slot in self.c.slots.values()))


if __name__ == "__main__":
    unittest.main()
