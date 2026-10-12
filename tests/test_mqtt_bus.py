"""Message identities and independent receipt timestamps preserve audit evidence."""
import json
import unittest
from unittest.mock import Mock
from factory_yard.mqtt_bus import Bus


class MQTTEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.bus = Bus({"host": "127.0.0.1", "port": 1}, "run", "simulator", ["#"])
        self.bus.client = Mock()

    def test_failed_publication_does_not_reuse_message_identity(self):
        self.bus.client.publish.return_value.rc = 4
        self.bus.publish("events", {"type": "test"})
        self.bus.client.publish.return_value.rc = 0
        self.bus.publish("events", {"type": "test"})
        packets = [json.loads(call.args[1]) for call in self.bus.client.publish.call_args_list]
        self.assertEqual([p["message_id"] for p in packets], ["run:simulator:1", "run:simulator:2"])
        self.assertEqual(self.bus.published, 1)

    def test_receiver_clock_overrides_sender_supplied_receipt_time(self):
        message = Mock(topic="yard/run/events", payload=b'{"run_id":"run","received_utc":"false"}')
        self.bus._message(None, None, message)
        topic, payload, received_ns = self.bus.inbox.get_nowait()
        self.assertEqual(topic, "events")
        self.assertNotEqual(payload["received_utc"], "false")
        self.assertTrue(payload["received_utc"].endswith("Z"))
        self.assertGreater(received_ns, 0)


if __name__ == "__main__":
    unittest.main()
