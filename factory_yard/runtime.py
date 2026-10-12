"""Independent simulator and coordinator workers, communicating through MQTT."""
import queue
import time
from .mqtt_bus import Bus


def apply_control(world, bus, config, payload, received, paused, speed, disconnect_until):
    """Validate against the worker's current state; HTTP validation can race it."""
    action = payload.get("action")
    receipt = {"category": "control", "control_id": payload.get("control_id"),
               "car_id": payload.get("car_id"), "action": action, "sim_time": world.snapshot()["sim_time"],
               "parameters": {k: payload[k] for k in ("value", "seconds", "car_id", "slot_id", "crossing_id") if k in payload}}
    if isinstance(payload.get("recovery_request"), dict):
        # Echo the HTTP admission context, not a second worker-side permission
        # decision. Actual application/rejection is still evaluated below.
        receipt["recovery_request"] = dict(payload["recovery_request"])
    try:
        if received - payload.get("sent_ns", 0) > config["mqtt"]["freshness_s"] * 1e9:
            raise ValueError("Elavult kezelőparancs")
        if action == "pause":
            paused = True
        elif action == "resume":
            paused = False
        elif action == "speed":
            if payload.get("value") not in (1, 2, 4, 8):
                raise ValueError("Hibás szimulációs sebesség")
            speed = float(payload["value"])
        elif action in ("disconnect", "sensor_failure", "freeze_car"):
            seconds = float(payload.get("seconds", 8))
            if not 0 < seconds <= 60:
                raise ValueError("Hibás hibaidőtartam")
            if action == "disconnect":
                if paused:
                    raise ValueError("Szüneteltetett modellnél az időzített kapcsolatmegszakítás nem alkalmazható")
                disconnect_until = receipt["sim_time"] + seconds
            else:
                world.control(action, payload)
        elif action in ("pedestrian", "block_slot"):
            world.control(action, payload)
        else:
            raise ValueError("Ismeretlen kezelőparancs")
    except (ValueError, KeyError, TypeError) as error:
        bus.publish("events", dict(receipt, type="control_rejected", level="warning",
                    message=f"A szimulátor elutasította a kezelőparancsot: {error}", reason=str(error)))
        return paused, speed, disconnect_until
    info = bus.publish("events", dict(receipt, type="control_applied", level="info",
                       message=f"Kezelőparancs alkalmazva: {action}"))
    if action == "disconnect":
        info.wait_for_publish(timeout=2)
        bus.publish("events", dict(receipt, type="fault", category="fault", level="warning",
                    duration_s=seconds,
                    message="A szimulátor MQTT-kapcsolata megszakad; helyi megállás.")).wait_for_publish(timeout=2)
        bus.client.disconnect()
    return paused, speed, disconnect_until


def simulator(config, run_id, seed=42, speed=1.0):
    from .model import World
    world = World(config, run_id, seed)
    bus = Bus(config["mqtt"], run_id, "simulator", ["commands/+", "control"])
    bus.start()
    paused, disconnect_until, deferred_events = False, 0.0, []
    dt = config["simulation"]["dt"]
    next_tick, last_snapshot = time.monotonic(), 0.0
    try:
        while True:
            for topic, payload, received in bus.drain():
                if topic.startswith("commands/"):
                    if received - payload["sent_ns"] <= config["mqtt"]["freshness_s"] * 1e9:
                        world.handle_command(topic, payload)
                elif topic == "control":
                    paused, speed, disconnect_until = apply_control(world, bus, config, payload, received,
                                                                    paused, speed, disconnect_until)
            sim_time = world.snapshot()["sim_time"]
            if disconnect_until and sim_time >= disconnect_until:
                if bus.reconnect():
                    disconnect_until = 0.0
                    bus.publish("events", {"type": "mqtt_recovered", "category": "fault", "level": "info",
                                "sim_time": sim_time, "message": "A szimulátor MQTT-kapcsolata és feliratkozása helyreállt."})
            connected = bus.ready.is_set() and not disconnect_until
            if not paused:
                messages = world.step(dt, connected=bool(connected))
                if connected:
                    for topic, payload in deferred_events + messages:
                        bus.publish(topic, payload)
                    deferred_events.clear()
                else:
                    deferred_events.extend((t, p) for t, p in messages if t == "events")
            now = time.monotonic()
            if connected and now - last_snapshot >= 0.1:
                snapshot = world.snapshot()
                snapshot.update(paused=paused, speed=speed, connected=bool(connected), seed=seed,
                                worker_mqtt={"published": bus.published, "received": bus.received})
                bus.publish("snapshot", snapshot)
                last_snapshot = now
            next_tick += dt / speed
            delay = next_tick - time.monotonic()
            if delay < -0.2:
                next_tick = time.monotonic()
            else:
                time.sleep(max(0.001, delay))
    finally:
        bus.close()


def coordinator(config, run_id, strategy="nearest"):
    from .coordinator import Coordinator
    controller = Coordinator(config, run_id, strategy)
    bus = Bus(config["mqtt"], run_id, "coordinator",
              ["requests/#", "telemetry/+", "sensors/parking", "events"])
    bus.start()
    last_snapshot = 0
    try:
        while True:
            try:
                topic, payload, received = bus.inbox.get(timeout=0.03)
                for outgoing, command in controller.handle(topic, payload, received):
                    bus.publish(outgoing, command)
                for audit in controller.drain_audit():
                    bus.publish("audit", audit)
            except queue.Empty:
                pass
            if time.monotonic() - last_snapshot > 0.1:
                state = controller.snapshot()
                state["sim_time"] = controller.sim_time
                bus.publish("coordinator", state)
                last_snapshot = time.monotonic()
    finally:
        bus.close()
