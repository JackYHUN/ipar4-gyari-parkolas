"""Run explicitly: python -m tests.mqtt_smoke.

Starts its own isolated broker and worker processes on a free loopback port.
This is an actual MQTT test, distinct from deterministic model experiments.
"""
import json
import io
from pathlib import Path
import socket
import time
import zipfile
from factory_yard.server import Application, ROOT


def wait_for(app, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = app.state()
        if predicate(last):
            return last
        time.sleep(0.03)
    raise AssertionError(f"Időtúllépés: {last.get('sim_time')}, {last.get('metrics')}, {last.get('warnings')}")


def main():
    output = ROOT / "results" / ("smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "config" / "default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        config["mqtt"]["port"] = probe.getsockname()[1]
    config["simulation"].update(production_limit=6, parking_dwell=1000)
    config_path = output / "config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    app = Application(config, config_path, seed=123, speed=8)
    checks = []
    try:
        app.start()
        wait_for(app, lambda s: s.get("cars") and any(c["speed"] > 0.5 for c in s["cars"]))
        checks.append("Publikált kérésből a külön koordinátor parkolóengedélyt ad; autó elindul")
        pause_request = app.control({"action": "pause"})
        paused = wait_for(app, lambda s: s.get("paused"))
        time.sleep(0.25)
        assert app.state()["sim_time"] == paused["sim_time"], "A szünet nem állította meg a modell óráját"
        assert any(r["event_type"] == "control_applied" and r["details"].get("control_id") == pause_request["control_id"]
                   for r in app.logs(category="control")["items"]), "A tényleges alkalmazási nyugta hiányzik"
        checks.append("Szünet: a modell ideje nem változik")
        app.control({"action": "resume"})
        wait_for(app, lambda s: not s.get("paused", True))
        app.control({"action": "sensor_failure", "seconds": 12})
        wait_for(app, lambda s: any(c["state"] == "sensor_stop" and c["speed"] == 0 for c in s.get("cars", [])))
        checks.append("Érzékelőkiesés: frissességi korlát után helyi megállás")
        wait_for(app, lambda s: not s.get("warnings") and any(c["speed"] > 0.5 for c in s.get("cars", [])))
        app.control({"action": "disconnect", "seconds": 12})
        wait_for(app, lambda s: not s.get("connected"), timeout=5)
        wait_for(app, lambda s: s.get("connected"), timeout=8)
        checks.append("Valódi szimulátor-MQTT-kapcsolat megszakad és újrafeliratkozással helyreáll")
        finished = wait_for(app, lambda s: s.get("metrics", {}).get("completed", 0) == 6, timeout=55)
        assert finished["metrics"]["collisions"] == 0, "A referenciaellenőrzés ütközést talált"
        checks.append("Hat autó tényleges MQTT-döntések után beparkol, modellezett ütközés nélkül")
        app.control({"action": "block_slot", "slot_id": "P01"})
        wait_for(app, lambda s: any(p["id"] == "P01" and p["state"] == "blocked" for p in s.get("parking", [])))
        app.control({"action": "block_slot", "slot_id": "P01"})
        wait_for(app, lambda s: any(p["id"] == "P01" and p["state"] != "blocked" for p in s.get("parking", [])))
        checks.append("Parkoló lezárása és feloldása a publikus érzékelésen keresztül követhető")
        decisions = app.logs(category="decision", limit=500)["items"]
        assert any(r["event_type"] == "parking_granted" and r["details"].get("reservation_id") for r in decisions)
        states = app.logs(category="state", limit=500)["items"]
        assert any(r["event_type"] == "state_changed" and r["car_id"] for r in states)
        assert app.car_history("CAR001")["telemetry"], "A fogadott autótelemetria idősora hiányzik"
        assert any(r["event_type"] == "mqtt_recovered" for r in app.logs(category="fault")["items"])
        checks.append("Tartós döntésnapló: foglalásazonosító, állapotváltás, alkalmazási nyugta és hibahelyreállás tényleges MQTT-n érkezett")
        export = app.export("zip")
        with zipfile.ZipFile(io.BytesIO(export)) as archive:
            assert archive.testzip() is None
            assert json.loads(archive.read("metadata.json"))["run_id"] == app.run_id
            assert archive.read("telemetry.csv")
        (output / "journal-export.zip").write_bytes(export)
        checks.append("Futás ZIP-exportja ellenőrzött, aktuális metaadatokat, naplót és telemetriát tartalmaz")
        records = [json.loads(line) for line in (app.run_dir / f"{app.run_id}-messages.jsonl").read_text(encoding="utf-8").splitlines()]
        categories = {r["topic"].split("/", 2)[2] for r in records}
        assert "requests/parking" in categories and "sensors/parking" in categories
        assert any(t.startswith("commands/") for t in categories)
        assert any(t.startswith("telemetry/") for t in categories)
        report = dict(status="PASS", transport="real MQTT 3.1.1 over loopback TCP", run_id=app.run_id,
                      records=len(records), checks=checks, metrics=finished["metrics"],
                      mqtt=finished["mqtt"], message_log=str(app.run_dir / f"{app.run_id}-messages.jsonl"))
        report["logging"] = app.journal.stats()
        report["journal_export"] = str(output / "journal-export.zip")
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        app.close()


if __name__ == "__main__":
    main()
