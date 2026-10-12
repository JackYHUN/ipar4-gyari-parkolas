"""Actual TCP/MQTT evidence for editable, larger scenarios and isolated preview.

Run: python -m tests.scenario_mqtt_smoke
"""
import io
import json
import socket
import threading
import time
import zipfile
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from factory_yard.server import Application, ROOT, create_http_server
from factory_yard.scenario import compile_scenario
from tests.mqtt_smoke import wait_for


def main():
    output = ROOT / "results" / ("scenario-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base)
    app = Application(config, ROOT / "config/default.json", seed=123, speed=8)
    server, thread = None, None
    checks = []
    try:
        app.start()
        wait_for(app, lambda s: s.get("connected") and s.get("cars"))
        server = create_http_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        previous_id = app.run_id
        requested = dict(preset="gyor", car_count=10, parking_count=40, initial_car_count=10,
            initial_occupied_count=0, production_limit=10, parking_dwell_s=600,
            production_exit_id="EXIT70", parking_area_id="south")
        with urlopen(url + "/api/scenario?" + urlencode({"scenario": json.dumps(requested)})) as response:
            preview = json.load(response)
        assert app.run_id == previous_id and len(app.config["slots"]) == 24
        assert len(preview["config"]["slots"]) == 40
        checks.append("HTTP preview compiles 40 slots/10 cars/alternate exit and leaves live run unchanged")
        preview["active"]["slots"][0].update(x=346.5, y=367)
        # Last initial car can be moved back on the legal incoming lane.
        preview["active"]["initial_cars"][-1]["x"] = 440
        body = json.dumps(dict(action="configure", scenario=preview["active"])).encode()
        with urlopen(Request(url + "/api/control", data=body, headers={"Content-Type": "application/json"})) as response:
            applied = json.load(response)
        assert applied["run_id"] != previous_id
        state = wait_for(app, lambda s: s.get("connected") and len(s.get("parking", [])) == 40 and len(s.get("cars", [])) == 10)
        assert state["map"]["width"] == 600 and len(state["map"]["buildings"]) >= 12
        assert app.config["slots"][0]["x"] == 346.5
        assert app.config["simulation"]["initial_cars"][-1]["x"] == 440
        checks.append("HTTP configure starts new MQTT namespace and workers use edited physical slots and initial car positions")
        active_id = app.run_id
        bad = dict(action="configure", scenario=dict(car_count=1000))
        try:
            urlopen(Request(url + "/api/control", data=json.dumps(bad).encode(), headers={"Content-Type": "application/json"}))
            raise AssertionError("Invalid scenario accepted")
        except HTTPError as error:
            assert error.code == 400
        assert app.run_id == active_id
        checks.append("Invalid geometry/count request rejected without replacing active run")
        finished = wait_for(app, lambda s: s.get("metrics", {}).get("completed") == 10, timeout=130)
        assert finished["metrics"]["collisions"] == 0
        checks.append("All ten cars park after real MQTT allocation and nine conflict zones, zero model collisions")
        records = app.logs(category="lifecycle", search="scenario_configured", limit=100)["items"]
        assert any(r["event_type"] == "scenario_configured" for r in records)
        assert any(r["event_type"] == "control_applied" and r["details"].get("control_id") == applied["control_id"]
                   for r in app.logs(category="control", limit=100)["items"])
        exported = app.export("zip")
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            assert archive.testzip() is None
            scenario = json.loads(archive.read("scenario.json"))
            recorded = json.loads(archive.read("config.json"))
            _, reproduced = compile_scenario(base, scenario)
            assert reproduced == recorded
        (output / "journal-export.zip").write_bytes(exported)
        checks.append("Export records edited scenario/config/source/telemetry; scenario reconstructs exact worker config")
        report = dict(status="PASS", transport="real MQTT 3.1.1 over loopback TCP", run_id=app.run_id,
            checks=checks, metrics=finished["metrics"], mqtt=finished["mqtt"],
            config=app.config, source_directory=str(app.run_dir), journal_export=str(output / "journal-export.zip"))
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "config"}, ensure_ascii=False, indent=2), flush=True)
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        app.close()


if __name__ == "__main__":
    main()
