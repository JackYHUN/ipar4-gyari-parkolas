"""Paho adapter. Callbacks only enqueue; worker owns mutable component state."""
import json
from datetime import datetime, timezone
import queue
import threading
import time
import paho.mqtt.client as mqtt


class Bus:
    def __init__(self, config, run_id, role, subscriptions):
        self.config, self.run_id, self.role = config, run_id, role
        self.prefix = f"yard/{run_id}/"
        self.inbox = queue.Queue()
        self.connected, self.ready = threading.Event(), threading.Event()
        self.published, self.received, self.invalid = 0, 0, 0
        self._receipt_lock = threading.Lock()
        self._last_received_ns, self._last_snapshot_received_ns = None, None
        self.publication_seq = 0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"yard-{role}-{run_id}", protocol=mqtt.MQTTv311)
        self.subscriptions = subscriptions
        self.client.on_connect = self._connect
        self.client.on_subscribe = lambda *_: self.ready.set()
        self.client.on_disconnect = lambda *_: (self.connected.clear(), self.ready.clear())
        self.client.on_message = self._message
        self.client.reconnect_delay_set(1, 2)

    def _connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected.set()
            client.subscribe([(self.prefix + s, 1) for s in self.subscriptions])

    def _message(self, client, userdata, message):
        try:
            data = json.loads(message.payload)
            if not isinstance(data, dict) or data.get("run_id") != self.run_id:
                self.invalid += 1
                return
            # Receipt UTC is sampled in the callback, independently of when the
            # display observer later processes the queued packet.
            data["received_utc"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            received_ns = time.perf_counter_ns()
            topic = message.topic[len(self.prefix):]
            with self._receipt_lock:
                self.received += 1
                self._last_received_ns = received_ns
                if topic == "snapshot":
                    self._last_snapshot_received_ns = received_ns
            self.inbox.put((topic, data, received_ns))
        except (ValueError, UnicodeDecodeError):
            self.invalid += 1

    def receipt_status(self, now_ns=None):
        """Callback receipt clocks; independent of the observer's FIFO progress.

        Missing or inconsistent clocks stay unknown. The counter can include
        a callback about to enqueue, so it is not an atomic queue-size measure.
        """
        now_ns = time.perf_counter_ns() if now_ns is None else now_ns
        lock = getattr(self, "_receipt_lock", None)
        if lock is None:
            last, snapshot, received = None, None, getattr(self, "received", 0)
        else:
            with lock:
                last, snapshot, received = self._last_received_ns, self._last_snapshot_received_ns, self.received
        def age(clock):
            if (isinstance(clock, int) and not isinstance(clock, bool) and clock >= 0
                    and isinstance(now_ns, int) and not isinstance(now_ns, bool) and now_ns >= clock):
                return (now_ns - clock) / 1e9
            return None
        return dict(broker_connected=bool(self.connected.is_set()), callback_received_messages=received,
                    last_message_age_s=age(last), last_snapshot_age_s=age(snapshot),
                    snapshot_received=(snapshot is not None) if lock is not None else None,
                    age_clock="host_perf_counter_receipt")

    def start(self, timeout=8):
        self.client.connect(self.config["host"], self.config["port"], keepalive=10)
        self.client.loop_start()
        if not self.ready.wait(timeout):
            self.close()
            raise RuntimeError("Az MQTT-feliratkozás nem indult el")

    def publish(self, topic, payload, qos=None):
        # Message identity belongs to a publication, unlike the stable request /
        # reservation identity carried by idempotently replayed application data.
        self.publication_seq += 1
        data = dict(payload, run_id=self.run_id, sent_ns=time.perf_counter_ns(),
                    producer=self.role, producer_seq=self.publication_seq,
                    message_id=f"{self.run_id}:{self.role}:{self.publication_seq}")
        if qos is None:
            qos = 0 if topic in ("snapshot", "coordinator") or topic.startswith(("telemetry/", "diagnostics/perception/")) else 1
        info = self.client.publish(self.prefix + topic, json.dumps(data, ensure_ascii=False), qos=qos, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            self.published += 1
        return info

    def drain(self):
        while True:
            try:
                yield self.inbox.get_nowait()
            except queue.Empty:
                break

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()

    def reconnect(self, timeout=3):
        # Paho's network thread exits after an explicit disconnect. Opening
        # its socket again is not enough: callbacks need a running loop.
        self.client.loop_stop()
        self.connected.clear()
        self.ready.clear()
        self.client.reconnect()
        self.client.loop_start()
        return self.ready.wait(timeout)
