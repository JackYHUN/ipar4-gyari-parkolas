"""Measured MQTT arrivals/ingestion with a bounded held-observer campaign.

This verifies diagnostic rates and recovery, not sustainable maximum capacity.
The experiment holds the display subscriber before its lock; MQTT callbacks,
the simulator, coordinator and broker continue normally without packet loss.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from factory_yard import __version__
from factory_yard.scenario import compile_scenario
from factory_yard.server import Application, ROOT, create_http_server


def validate_rates(capacity):
    """Recompute every reported rate from raw counts and actual sample time."""
    history = capacity["history"]
    assert len(history) <= capacity["history_limit"] == 120
    verified = 0
    for index, point in enumerate(history):
        seconds = point["window_s"]
        if seconds is None:
            assert point["arrival_rate_hz"] is None and point["dequeue_rate_hz"] is None
            continue
        assert 1 <= seconds <= 5 and math.isfinite(seconds)
        anchors = [old for old in history[:index] if math.isclose(
            point["elapsed_s"] - old["elapsed_s"], seconds, abs_tol=1e-8)]
        assert len(anchors) == 1
        anchor = anchors[0]
        for counter, rate in (("received_total", "arrival_rate_hz"), ("dequeued_total", "dequeue_rate_hz")):
            expected = (point[counter] - anchor[counter]) / seconds
            assert expected >= 0 and math.isclose(point[rate], expected, rel_tol=1e-10, abs_tol=1e-8)
        if point["queue_growth_hz"] is not None:
            expected = (point["queue_messages"] - anchor["queue_messages"]) / seconds
            assert math.isclose(point["queue_growth_hz"], expected, rel_tol=1e-10, abs_tol=1e-8)
        verified += 1
    return verified


def main():
    names = [p.relative_to(ROOT).as_posix() for p in (ROOT / "factory_yard").glob("*.py")]
    names.extend(("experiments/verify_capacity_v13.py", "config/default.json"))
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    output = ROOT / "results" / ("capacity-smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base["mqtt"]["port"] = probe.getsockname()[1]
    _, config = compile_scenario(base, dict(preset="gyor", car_count=12, parking_count=40,
        initial_car_count=12, initial_occupied_count=0, production_limit=12, parking_dwell_s=600))
    config["simulation"]["automatic_pedestrians"] = False
    config["logging"] = dict(detail_limit_mib=2, raw_segment_mib=.5)
    path = output / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    app = Application(config, path, seed=93, speed=2)
    hold, holding, release = threading.Event(), threading.Event(), threading.Event()
    original_batch = app._observe_batch

    def controlled_batch():
        if hold.is_set():
            holding.set()
            if not release.wait(20):
                raise RuntimeError("Experimental observer hold exceeded its bound")
        return original_batch()

    app._observe_batch = controlled_batch
    server = http = None
    samples, checks, observations, durations = [], [], {}, []
    started = time.monotonic()

    def request(endpoint, data=None):
        req = Request(f"http://127.0.0.1:{server.server_port}" + endpoint,
            data=None if data is None else json.dumps(data).encode("utf-8"),
            headers={} if data is None else {"Content-Type": "application/json"})
        before = time.monotonic()
        try:
            with urlopen(req, timeout=10) as response:
                result = response.status, json.load(response)
        except HTTPError as error:
            result = error.code, json.load(error)
        durations.append((time.monotonic() - before) * 1000)
        return result

    def capture(phase):
        status, state = request("/api/state")
        assert status == 200
        capacity = state["mqtt"]["capacity"]
        samples.append(dict(phase=phase, host_elapsed_s=time.monotonic() - started,
            run_id=state["run_id"], connected=state["connected"], paused=state.get("paused"),
            speed=state.get("speed"), active_car_count=len(state.get("cars", [])),
            capacity=capacity, transport=state["mqtt"]["transport"],
            observer=state["mqtt"]["observer"], processed_snapshot_age_s=state["mqtt"]["last_message_age_s"]))
        validate_rates(capacity)
        return state

    def wait(predicate, phase, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = capture(phase)
            if predicate(state):
                return state
            time.sleep(.1)
        raise AssertionError("Expected condition not observed: " + phase)

    try:
        app.start()
        server = create_http_server(app, 0)
        http = threading.Thread(target=server.serve_forever, daemon=True)
        http.start()
        initial = wait(lambda s: s.get("connected") and len(s.get("cars", [])) == 12, "startup")
        original_run = initial["run_id"]
        for _ in range(7):
            time.sleep(1.05)
            natural = capture("natural")
        assert validate_rates(natural["mqtt"]["capacity"]) >= 3
        assert natural["mqtt"]["capacity"]["arrival_rate_hz"] > 0
        assert natural["mqtt"]["capacity"]["dequeue_rate_hz"] > 0
        checks.append("Actual 12-car MQTT traffic: diagnostic rates match raw counter differences over host-clock windows")
        observations["before_hold"] = samples[-1]

        hold.set()
        assert holding.wait(3)
        for _ in range(7):
            time.sleep(1.05)
            held = capture("observer_held")
        measured = held["mqtt"]["capacity"]
        assert measured["arrival_rate_hz"] > 0 and measured["dequeue_rate_hz"] == 0
        assert measured["queue_growth_hz"] > 0 and measured["queue_messages"] > 0
        assert not held["connected"] and held["control_recovery"]["pause_available"]
        assert held["mqtt"]["transport"]["last_snapshot_age_s"] <= .75
        observations["while_held"] = samples[-1]
        checks.append("HTTP diagnostics continue observing callbacks and growing queue during a bounded display ingestion hold")
        for action in (dict(action="resume"), dict(action="speed", value=2)):
            status, result = request("/api/control", action)
            assert status == 400 and result.get("error")
        ids = []
        for action in (dict(action="speed", value=1), dict(action="pause")):
            status, result = request("/api/control", action)
            assert status == 200 and result.get("control_id")
            ids.append(result["control_id"])
        _, log = request("/api/logs?category=control&limit=50")
        published = [row for row in log["items"] if row["event_type"] == "control_published" and row["details"].get("control_id") in ids]
        assert len(published) == 2
        assert not any(row["event_type"] == "control_applied" and row["details"].get("control_id") in ids for row in log["items"])
        observations["published_receipts_while_held"] = published
        checks.append("Only pause/1x recovery accepted while displayed state is stale; QoS1 publication precedes observed application receipts")

        hold.clear()
        release.set()
        recovered = wait(lambda s: s.get("connected") and s.get("paused") is True and s.get("speed") == 1,
            "draining")
        for _ in range(6):
            time.sleep(1.05)
            recovered = capture("recovered_paused")
        assert recovered["mqtt"]["capacity"]["queue_messages"] < measured["queue_messages"]
        assert any(sample["capacity"]["queue_growth_hz"] is not None and
            sample["capacity"]["queue_growth_hz"] < 0 for sample in samples if sample["phase"] in ("draining", "recovered_paused"))
        _, log = request("/api/logs?category=control&limit=50")
        applied = [row for row in log["items"] if row["event_type"] == "control_applied" and row["details"].get("control_id") in ids]
        assert len(applied) == 2
        observations["applied_receipts_after_release"] = applied
        observations["after_release"] = samples[-1]
        checks.append("After FIFO release backlog declines, signed growth shows draining, and both simulator application receipts are correlated")

        status, reset = request("/api/control", dict(action="reset", seed=93))
        assert status == 200 and reset["run_id"] != original_run
        fresh = capture("new_run")
        history = fresh["mqtt"]["capacity"]["history"]
        assert fresh["run_id"] == reset["run_id"] and history
        assert history[0]["elapsed_s"] == 0 and len(history) <= 2
        assert fresh["mqtt"]["capacity"]["reset_reason"] == "new_run"
        observations["new_run"] = samples[-1]
        checks.append("Actual HTTP run reset clears the measured history and starts a separate host-clock origin")
        assert hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
        ordered = sorted(durations)
        report = dict(status="PASS", model_version=__version__, executed_at_utc=datetime.now(timezone.utc).isoformat(),
            command="python -m experiments.verify_capacity_v13", run_id=original_run,
            transport="actual MQTT3.1.1 loopback broker, independent simulator/coordinator/display and HTTP",
            scope="Finite 12-car host campaign at2x, then experiment-only observer hold and pause/1x recovery. This verifies measured diagnostics, not sustainable maximum throughput or production timing guarantees.",
            elapsed_host_s=time.monotonic() - started, checks=checks, observations=observations,
            samples=samples, sample_count=len(samples),
            http_observations=dict(requests=len(durations), elapsed_ms_median=ordered[len(ordered)//2],
                elapsed_ms_p95=ordered[int((len(ordered)-1)*.95)], definition="actual HTTP calls in this finite campaign, including control/reset; not a latency guarantee"),
            config=config, source_sha256=hashes)
        (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "results/transport-capacity-check-v13.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(dict(status="PASS", checks=len(checks), samples=len(samples), run_id=original_run,
            artifact="results/transport-capacity-check-v13.json")))
    finally:
        hold.clear()
        release.set()
        if server:
            server.shutdown()
            server.server_close()
            http.join(3)
        app.close()


if __name__ == "__main__":
    main()
