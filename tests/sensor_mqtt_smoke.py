"""Verify sampled sensor diagnostics, failure and recovery through real MQTT.

Run explicitly: python -m tests.sensor_mqtt_smoke
"""
from datetime import datetime, timezone
import json
import math
import socket
import threading
import time
from urllib.request import urlopen

from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server
from tests.mqtt_smoke import wait_for


def main():
    output = ROOT / "results" / ("sensor-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=2, parking_count=12,
        initial_car_count=2, initial_occupied_count=0, production_limit=2, parking_dwell_s=5))
    config["simulation"]["automatic_pedestrians"] = False
    config_path = output / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, config_path, seed=73, speed=4)
    server = thread = None
    checks = []
    car_id = "CAR001"

    def selected(state):
        return next((car for car in state.get("cars", []) if car["id"] == car_id), {})

    def get_state():
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/state", timeout=5) as response:
            return json.loads(response.read())

    try:
        app.start()
        server = create_http_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fresh_state = wait_for(app, lambda state: selected(state).get("sensors", {}).get("fresh")
                              and selected(state)["sensors"].get("guidance", {}).get("available"))
        fresh = selected(fresh_state)["sensors"]
        assert fresh["source"] == "virtual_onboard_bundle"
        assert fresh["environment"]["coverage"] == "radial_360_no_occlusion"
        assert fresh["age_s"] <= fresh["timeout_s"]
        bound = fresh["position_uncertainty_m"]
        for axis in ("x_m", "y_m"):
            assert abs(fresh["pose"][axis] - fresh["reference_pose"][axis]) <= bound + 1e-5
        assert math.isfinite(fresh["guidance"]["lateral_error_m"])
        http_fresh = selected(get_state())["sensors"]
        assert http_fresh["sampled"] and http_fresh["pose"]
        checks.append("Sampled localization, bounded noise and route projection arrive through MQTT and HTTP")

        app.control({"action": "sensor_failure", "seconds": 6})
        fault_state = wait_for(app, lambda state: selected(state).get("sensors", {}).get("fault_active"))
        held = selected(fault_state)["sensors"]
        stale_state = wait_for(app, lambda state: selected(state).get("state") == "sensor_stop"
                              and selected(state).get("speed") == 0
                              and not selected(state).get("sensors", {}).get("fresh", True))
        stale = selected(stale_state)["sensors"]
        assert stale["sample_sim_time_s"] == held["sample_sim_time_s"]
        assert stale["pose"] == held["pose"]
        assert stale["reference_pose"] == held["reference_pose"]
        assert stale["environment"] == held["environment"]
        assert stale["age_s"] > stale["timeout_s"]
        http_stale = selected(get_state())["sensors"]
        assert not http_stale["fresh"] and http_stale["fault_active"]
        checks.append("Sensor outage freezes the sampled observations; increasing age triggers the existing braking stop")

        recovered_state = wait_for(app, lambda state: selected(state).get("sensors", {}).get("fresh")
                                  and not selected(state)["sensors"].get("fault_active")
                                  and selected(state)["sensors"]["sample_sim_time_s"] > held["sample_sim_time_s"])
        recovered = selected(recovered_state)["sensors"]
        checks.append("Recovery supplies a new fresh observation rather than relabelling the old sample")
        app.control({"action": "pause"})
        wait_for(app, lambda state: state.get("paused"))
        observed_frames = []
        for path in app.run_dir.glob(f"{app.run_id}-messages*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                payload = row.get("payload", {})
                if payload.get("car_id") == car_id and payload.get("sensors"):
                    observed_frames.append(row)
        assert observed_frames, "No received sensor-bundle telemetry was recorded"
        assert any(row["payload"]["sensors"].get("guidance", {}).get("available") for row in observed_frames)
        (output / "received-sensor-frames.json").write_text(
            json.dumps(observed_frames[-5:], ensure_ascii=False, indent=2), encoding="utf-8")
        checks.append("Raw received telemetry contains the actual sensor bundle and its source metadata")
        report = dict(status="PASS", executed_at_utc=datetime.now(timezone.utc).isoformat(),
            transport="real MQTT 3.1.1 over loopback TCP + HTTP", run_id=app.run_id,
            checks=checks, fresh=fresh, held=held, stale=stale, recovered=recovered,
            collisions=recovered_state["metrics"]["collisions"],
            recorded_telemetry_frames=len(observed_frames),
            source_frames=str(output / "received-sensor-frames.json"))
        assert report["collisions"] == 0
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items()
                          if key not in {"fresh", "held", "stale", "recovered"}}, ensure_ascii=False, indent=2))
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        app.close()


if __name__ == "__main__":
    main()
