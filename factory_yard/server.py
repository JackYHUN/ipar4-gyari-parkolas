"""MQTT display subscriber + loopback HTTP API and worker supervision."""
import copy
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
import queue
import random
import re
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .mqtt_bus import Bus
from .journal import Journal
from .transport_monitor import TransportMonitor
from .operations import OperationsMonitor, OperationsConflict, THRESHOLDS
from . import __version__
from .scenario import scenario_payload, compile_scenario

ROOT = Path(__file__).resolve().parents[1]
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NAMESPACE)


def _perception_download(data):
    """Validate a passive, browser-rendered frame without writing any files."""
    kind, content = data.get("format"), data.get("content", "")
    if kind not in {"json", "svg"}:
        raise ValueError("Az érzékelőminta formátuma json vagy svg lehet")
    if not isinstance(content, str) or not 0 < len(content.encode("utf-8")) <= 128 * 1024:
        raise ValueError("Az érzékelőminta mérete legfeljebb 128 KiB lehet")
    supplied = str(data.get("filename", ""))
    basename = supplied.replace("\\", "/").rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem).strip("._-")[:120] or "erzekelo_minta"
    filename = stem + "." + kind
    if kind == "json":
        def reject_constant(_):
            raise ValueError("A JSON-minta véges számokat tartalmazhat")
        try:
            document = json.loads(content, parse_constant=reject_constant)
            if not isinstance(document, dict):
                raise ValueError("A JSON-minta objektum legyen")
            raw = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        except RecursionError:
            raise ValueError("Túl mélyen beágyazott JSON-minta") from None
        return raw, "application/json; charset=utf-8", filename
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", content, re.IGNORECASE):
        raise ValueError("Az SVG nem tartalmazhat DTD-t vagy entitásdeklarációt")
    try:
        document = ET.fromstring(content)
    except ET.ParseError:
        raise ValueError("Érvénytelen SVG-minta") from None
    if document.tag != "{" + SVG_NAMESPACE + "}svg":
        raise ValueError("Az SVG gyökéreleme névteres svg legyen")
    geometry = {
        "svg": {"x", "y", "width", "height", "viewBox"}, "g": set(),
        "rect": {"x", "y", "width", "height", "rx", "ry"},
        "circle": {"cx", "cy", "r"}, "path": {"d"},
        "line": {"x1", "y1", "x2", "y2"}, "text": {"x", "y"},
        "title": set(), "metadata": set(),
    }
    presentation = {"fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
        "font-family", "font-size", "font-weight", "text-anchor", "paint-order", "opacity",
        "role", "aria-label", "data-sample-time", "data-fresh"}
    text_values = {"stroke-linecap": {"round", "butt", "square"},
        "text-anchor": {"start", "middle", "end"}, "paint-order": {"stroke", "normal"},
        "role": {"img"}, "data-fresh": {"true", "false"}}
    numeric = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
    stack, count = [(document, 1)], 0
    while stack:
        element, depth = stack.pop()
        count += 1
        if count > 2000 or depth > 12:
            raise ValueError("Túl összetett SVG-minta")
        namespace, _, tag = element.tag.removeprefix("{").partition("}")
        if namespace != SVG_NAMESPACE or tag not in geometry:
            raise ValueError("Az SVG csak a sematikus nézet passzív rajzelemeit tartalmazhatja")
        for attribute, value in element.attrib.items():
            if attribute not in geometry[tag] | presentation:
                raise ValueError("Nem engedélyezett SVG-attribútum")
            if attribute in {"fill", "stroke"}:
                valid = bool(re.fullmatch(r"none|#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value))
            elif attribute in text_values:
                valid = value in text_values[attribute]
            elif attribute == "font-family":
                valid = bool(re.fullmatch(r"[A-Za-z0-9 ,'-]{1,100}", value))
            elif attribute == "aria-label":
                valid = len(value) <= 1024
            elif attribute == "d":
                valid = bool(re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]*", value))
            elif attribute in {"viewBox", "stroke-dasharray"}:
                values = value.replace(",", " ").split()
                valid = (attribute != "viewBox" or len(values) == 4) and all(numeric.fullmatch(v) for v in values)
            else:
                valid = bool(numeric.fullmatch(value))
            if not valid:
                raise ValueError("Nem engedélyezett SVG-attribútumérték")
        stack.extend((child, depth + 1) for child in element)
    # Reserialization discards XML processing instructions, including stylesheet
    # references, and preserves text/JSON metadata through ordinary XML escaping.
    raw = ET.tostring(document, encoding="utf-8", xml_declaration=True)
    return raw, "image/svg+xml; charset=utf-8", filename


class _SnapshotStream:
    """Close an eagerly opened archive snapshot on exhaustion or cancellation."""
    def __init__(self, iterator, context):
        self.iterator, self.context = iter(iterator), context

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.iterator)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.context is not None:
            context, self.context = self.context, None
            try:
                if hasattr(self.iterator, "close"):
                    self.iterator.close()
            finally:
                context.__exit__(None, None, None)

    def __del__(self):
        self.close()


class Application:
    OBSERVER_BATCH_MESSAGES = 128
    OBSERVER_BATCH_SECONDS = .025

    def __init__(self, config, config_path, seed=42, strategy="nearest", speed=1, external=False):
        self.config, self.config_path = config, config_path
        self.seed, self.strategy, self.speed = seed, strategy, speed
        self.external, self.children, self.files = external, [], []
        self.broker_process, self.bus, self.closed = None, None, False
        self.lock, self.control_lock = threading.RLock(), threading.RLock()
        self.latest, self.central = {}, {}
        self.last_snapshot_ns, self.received, self.latencies = 0, 0, []
        self.fault_started_ns = 0
        self.log_segment, self.log_bytes = 0, 0
        self.session_detail_bytes, self.recording_limited = 0, False
        self.detail_limit_bytes = int(config.get("logging", {}).get("detail_limit_mib", 100) * 1024 * 1024)
        self.raw_segment_bytes = max(1, min(self.detail_limit_bytes,
            int(config.get("logging", {}).get("raw_segment_mib", 25) * 1024 * 1024)))
        self.raw_retained_bytes, self.raw_removed_bytes, self.raw_removed_segments, self.raw_oversize_dropped = 0, 0, 0, 0
        self._raw_segments = []
        self.run_id = ""
        self.run_dir = None
        self.journal, self.metadata = None, {}
        self.role_files = {}
        self._observed_connection, self._reported_invalid = None, 0
        self._reported_stopped = set()
        self._telemetry_truncated_logged = False
        self._observer_stats = self._empty_observer_stats()
        self._observer_waits = deque(maxlen=1000)
        self._transport_monitor = TransportMonitor()
        self._operations_monitor = OperationsMonitor()

    @classmethod
    def _empty_observer_stats(cls):
        return dict(batches=0, dequeued_messages=0, last_batch_messages=0,
            last_batch_wall_ms=0.0, max_batch_wall_ms=0.0, backlog_messages=None,
            peak_backlog_messages=0, last_queue_wait_ms=None, queue_wait_p95_ms=None,
            queue_wait_sample_count=0, queue_wait_window_limit=1000, queue_wait_invalid_clocks=0,
            queue_wait_definition="received callback -> whole-message ingestion start; excludes ingestion duration",
            batch_message_budget=cls.OBSERVER_BATCH_MESSAGES,
            batch_wall_budget_ms=cls.OBSERVER_BATCH_SECONDS * 1000,
            budget_definition="checked after each complete message; one ingestion or health check can exceed the budget",
            backlog_definition="queue.qsize() at batch completion; approximate while MQTT callback is active")

    def _spawn(self, role, **kwargs):
        old_handle = self.role_files.get(role)
        if old_handle:
            old_handle.close()
            self.files.remove(old_handle)
        log = self.run_dir / f"{role}.log"
        handle = log.open("a", encoding="utf-8")
        self.files.append(handle)
        self.role_files[role] = handle
        args = [sys.executable, "-m", "factory_yard", role, "--config", str(self.config_path), "--run-id", self.run_id]
        for key, value in kwargs.items():
            args += ["--" + key.replace("_", "-"), str(value)]
        env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        process = subprocess.Popen(args, cwd=ROOT, stdout=handle, stderr=handle, env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return process

    def _port_open(self):
        try:
            with socket.create_connection((self.config["mqtt"]["host"], self.config["mqtt"]["port"]), timeout=0.2):
                return True
        except OSError:
            return False

    def start(self):
        self.run_dir = ROOT / "results" / ("live-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.run_dir / "startup-config.json"
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        if not self.external:
            if self._port_open():
                raise RuntimeError("Az MQTT-port foglalt. Másik port vagy --external-broker szükséges.")
            self.broker_process = self._spawn("broker")
        for _ in range(80):
            if self._port_open():
                break
            if self.broker_process and self.broker_process.poll() is not None:
                raise RuntimeError(f"A broker leállt; napló: {self.run_dir / 'broker.log'}")
            time.sleep(0.1)
        else:
            raise RuntimeError("A broker nem érhető el")
        self._new_run()
        threading.Thread(target=self._observe, daemon=True).start()

    @staticmethod
    def _stop(process):
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _new_run(self):
        with self.lock:
            for p in self.children:
                self._stop(p)
            self.children.clear()
            if self.bus:
                self.bus.close()
            previous_run_id = self.run_id or None
            if self.journal:
                self.journal.record("lifecycle", "run_finished", "A futás lezárult; naplói megmaradnak.",
                                    sim_time=self.latest.get("sim_time"))
                self.journal.close()
            self.run_id = uuid.uuid4().hex[:12]
            # Each worker reads exactly the configuration recorded for this run.
            self.config_path = self.run_dir / f"{self.run_id}-config.json"
            self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
            self.latest, self.central = {}, {}
            self.last_snapshot_ns, self.received, self.latencies = 0, 0, []
            self.fault_started_ns = 0
            self.log_segment, self.log_bytes = 0, 0
            self.session_detail_bytes, self.recording_limited = 0, False
            self.raw_retained_bytes, self.raw_removed_bytes, self.raw_removed_segments, self.raw_oversize_dropped = 0, 0, 0, 0
            self._raw_segments = []
            self._observed_connection, self._reported_invalid = None, 0
            self._reported_stopped.clear()
            self._telemetry_truncated_logged = False
            self._observer_stats = self._empty_observer_stats()
            self._observer_waits.clear()
            self._transport_monitor.reset()
            self._operations_monitor.reset()
            self.journal = Journal(self.run_dir / f"{self.run_id}-journal.sqlite3", self.run_id)
            self.bus = Bus(self.config["mqtt"], self.run_id, "display", ["#"])
            self.bus.start()
            metadata = dict(run_id=self.run_id, seed=self.seed, strategy=self.strategy,
                            speed=self.speed, config=self.config, model_version=__version__,
                            started_at_utc=datetime.now(timezone.utc).isoformat(),
                            history_schema=2, perception_history_schema=1, transport_history_schema=1,
                            operations_history_schema=1, operations_thresholds=dict(THRESHOLDS),
                            perception_retention="entire_received_run_on_disk",
                            perception_image_kind="schematic_reconstruction_from_saved_sensor_observations",
                            camera_recording=False,
                            previous_run_id=previous_run_id,
                            units={"sim_time": "s (simulation)", "speed": "m/s", "position": "m", "latency": "ms (host perf_counter)"},
                            source_sha256={str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                                for folder in ("factory_yard", "config", "web") for p in (ROOT / folder).rglob("*")
                                if p.is_file() and p.suffix in (".py", ".json", ".html", ".css", ".js")},
                            transport_clock={"name": "time.perf_counter_ns",
                                "implementation": time.get_clock_info("perf_counter").implementation,
                                "resolution_s": time.get_clock_info("perf_counter").resolution,
                                "same_host": True})
            (self.run_dir / f"{self.run_id}-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self.metadata = metadata
            self.journal.record("lifecycle", "run_started", "Új MQTT-futás indult.", sim_time=0,
                                details={"seed": self.seed, "strategy": self.strategy, "speed": self.speed, "version": __version__})
        self.children.append(self._spawn("coordinator", strategy=self.strategy))
        # Workers report their subscribed state over MQTT before model startup.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.lock:
                for topic, payload, received in self.bus.drain():
                    self._accept(topic, payload, received)
            if self.central:
                break
            if self.children[0].poll() is not None:
                raise RuntimeError("A koordinátor nem indult el; lásd a coordinator.log fájlt")
            time.sleep(0.02)
        else:
            raise RuntimeError("A koordinátor MQTT-visszajelzése nem érkezett meg")
        self.children.append(self._spawn("simulator", seed=self.seed, speed=self.speed))

    def _accept(self, topic, payload, received):
        if payload.get("run_id") != self.run_id:
            return
        self.received += 1
        delay = max(0, (received - payload.get("sent_ns", received)) / 1e6)
        self.latencies.append(delay)
        self.latencies = self.latencies[-1000:]
        if self.journal:
            self.journal.ingest(topic, payload, received, delay)
        if topic == "snapshot":
            self.latest, self.last_snapshot_ns = payload, received
            observed_speed = payload.get("speed")
            if not isinstance(observed_speed, bool) and observed_speed in (1, 2, 4, 8):
                self.speed = observed_speed
            if payload.get("sent_ns", 0) > self.fault_started_ns:
                self.fault_started_ns = 0
        elif topic == "coordinator":
            self.central = payload
        elif topic == "events" and payload.get("type") == "fault":
            self.fault_started_ns = max(self.fault_started_ns, payload.get("sent_ns", received))
        elif topic == "events" and payload.get("type") == "control_applied" and payload.get("action") == "speed":
            parameters = payload.get("parameters")
            observed_speed = parameters.get("value") if isinstance(parameters, dict) else None
            if not isinstance(observed_speed, bool) and observed_speed in (1, 2, 4, 8):
                self.speed = observed_speed
        record = dict(topic=f"yard/{self.run_id}/{topic}", received_ns=received,
                      transport_latency_ms=delay, payload=payload)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        line_bytes = len(line.encode("utf-8"))
        self.session_detail_bytes += line_bytes
        # The raw MQTT ring retains recent traffic, while semantic events and
        # compact trip samples remain in SQLite for the entire received run.
        if line_bytes > self.detail_limit_bytes:
            self.raw_oversize_dropped += 1
            self.recording_limited = True
            if self.journal and self.raw_oversize_dropped == 1:
                self.journal.record("transport", "raw_message_oversize", "Egy MQTT-üzenet nagyobb a teljes nyers naplókeretnél; a tömör útnapló továbbra is mentődik.",
                    level="warning", sim_time=payload.get("sim_time"), details={"message_bytes": line_bytes, "limit_bytes": self.detail_limit_bytes})
            return
        segment_limit = max(1, min(self.raw_segment_bytes, self.detail_limit_bytes))
        if self.log_bytes and self.log_bytes + line_bytes > segment_limit:
            self.log_segment += 1
            self.log_bytes = 0
        suffix = "" if not self.log_segment else f"-{self.log_segment:03d}"
        path = self.run_dir / f"{self.run_id}-messages{suffix}.jsonl"
        if not self._raw_segments or self._raw_segments[-1][0] != path:
            self._raw_segments.append([path, 0])
        with path.open("a", encoding="utf-8") as log:
            log.write(line)
        self.log_bytes += line_bytes
        self.raw_retained_bytes += line_bytes
        self._raw_segments[-1][1] += line_bytes
        removed = 0
        while self.raw_retained_bytes > self.detail_limit_bytes and len(self._raw_segments) > 1:
            old_path, size = self._raw_segments.pop(0)
            # Only filenames constructed for this run inside its resolved
            # directory can ever be removed by retention.
            if old_path.resolve().parent != self.run_dir.resolve():
                raise RuntimeError("A nyers napló útvonala kívül esik a futás könyvtárán")
            old_path.unlink(missing_ok=True)
            self.raw_retained_bytes -= size
            self.raw_removed_bytes += size
            self.raw_removed_segments += 1
            removed += 1
        if removed:
            self.recording_limited = True
            details = self.raw_logging_stats()
            (self.run_dir / f"{self.run_id}-RAW_LOG_RETENTION.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
            if self.journal:
                self.journal.record("transport", "raw_log_rotated", "A nyers MQTT-napló körkörös kerete a legújabb üzeneteket őrzi; a teljes fogadott útnapló megmarad.",
                    sim_time=payload.get("sim_time"), details=details)

    def raw_logging_stats(self):
        return dict(detailed_raw_limited=self.recording_limited, raw_policy="recent_per_run_ring",
            raw_limit_bytes=self.detail_limit_bytes, raw_segment_bytes=self.raw_segment_bytes,
            raw_retained_bytes=self.raw_retained_bytes, raw_total_received_bytes=self.session_detail_bytes,
            raw_removed_segments=self.raw_removed_segments, raw_removed_bytes=self.raw_removed_bytes,
            raw_oversize_dropped=self.raw_oversize_dropped,
            raw_segments=[dict(file=p.name, bytes=size) for p, size in self._raw_segments])

    def _observe_batch(self):
        """Release the application lock even if MQTT keeps replenishing inbox.

        The iterator removes only the next packet at ``next``. Ending a batch
        leaves every remaining packet queued in original receipt order; reset
        cannot switch the bus while a whole-message ingestion holds the lock.
        """
        with self.lock:
            started = time.perf_counter()
            count, exhausted = 0, False
            messages = self.bus.drain()
            for _ in range(self.OBSERVER_BATCH_MESSAGES):
                try:
                    topic, payload, received = next(messages)
                except StopIteration:
                    break
                ingestion_started_ns = time.perf_counter_ns()
                wait_ms = ((ingestion_started_ns - received) / 1e6
                    if isinstance(received, int) and not isinstance(received, bool)
                    and 0 <= received <= ingestion_started_ns else None)
                self._observer_stats["last_queue_wait_ms"] = wait_ms
                if wait_ms is not None:
                    self._observer_waits.append(wait_ms)
                    self._observer_stats["queue_wait_sample_count"] = len(self._observer_waits)
                else:
                    self._observer_stats["queue_wait_invalid_clocks"] += 1
                self._accept(topic, payload, received)
                count += 1
                if time.perf_counter() - started >= self.OBSERVER_BATCH_SECONDS:
                    exhausted = True
                    break
            else:
                exhausted = True
            self._health()
            elapsed_ms = (time.perf_counter() - started) * 1000
            inbox = getattr(self.bus, "inbox", None)
            backlog = inbox.qsize() if isinstance(inbox, queue.Queue) else None
            stats = self._observer_stats
            stats["batches"] += 1
            stats["dequeued_messages"] += count
            stats.update(last_batch_messages=count, last_batch_wall_ms=elapsed_ms, backlog_messages=backlog)
            stats["max_batch_wall_ms"] = max(stats["max_batch_wall_ms"], elapsed_ms)
            if backlog is not None:
                stats["peak_backlog_messages"] = max(stats["peak_backlog_messages"], backlog)
            self._sample_transport(time.perf_counter_ns())
            return bool(backlog) if backlog is not None else exhausted

    def _observe(self):
        while not self.closed:
            busy = self._observe_batch()
            # Give waiting HTTP/control threads an opportunity to acquire the
            # lock, while a nonempty queue keeps almost all processing capacity.
            time.sleep(.001 if busy else .015)

    def _health(self):
        """Persist lifecycle changes once, instead of polluting every HTTP poll."""
        if not self.journal:
            return
        stats = self.journal.stats()
        if stats.get("telemetry_truncated", 0) and not self._telemetry_truncated_logged:
            self.journal.record("transport", "telemetry_window_rotated", "A részletes telemetria ablaka gördül; a teljes fogadott út tömör archívuma megmarad.",
                                sim_time=self.latest.get("sim_time"), details=stats)
            self._telemetry_truncated_logged = True
        if self.latest:
            connected = bool(self.bus.connected.is_set() and not self.fault_started_ns and
                             time.perf_counter_ns() - self.last_snapshot_ns < 750_000_000)
            if connected != self._observed_connection:
                inbox = getattr(self.bus, "inbox", None)
                health_backlog = inbox.qsize() if isinstance(inbox, queue.Queue) else None
                self.journal.record("transport", "stream_live" if connected else "stream_stale",
                    "Friss szimulátorállapot érkezik MQTT-n." if connected else "A szimulátor MQTT-állapota elavult vagy a kapcsolat megszakadt.",
                    level="info" if connected else "warning", sim_time=self.latest.get("sim_time"),
                    details={"last_snapshot_age_s": (time.perf_counter_ns() - self.last_snapshot_ns) / 1e9,
                             "transport": self._transport_status(time.perf_counter_ns()),
                             "last_queue_wait_ms": self._observer_stats["last_queue_wait_ms"],
                             "backlog_messages": health_backlog,
                             "backlog_definition": "queue.qsize() sampled for this health event; approximate while MQTT callback is active"})
                self._observed_connection = connected
        if self.bus.invalid != self._reported_invalid:
            self.journal.record("transport", "invalid_message", "Az MQTT-kliens hibás vagy idegen futáshoz tartozó üzenetet utasított el.",
                                level="warning", sim_time=self.latest.get("sim_time"),
                                details={"total": self.bus.invalid, "new": self.bus.invalid - self._reported_invalid})
            self._reported_invalid = self.bus.invalid
        for child in self.children:
            exit_code = child.poll()
            if exit_code is not None and child.pid not in self._reported_stopped:
                self.journal.record("lifecycle", "worker_stopped", "Egy háttérfolyamat váratlanul leállt.", level="error",
                                    sim_time=self.latest.get("sim_time"), details={"pid": child.pid, "exit_code": exit_code})
                self._reported_stopped.add(child.pid)

    def _transport_status(self, now_ns):
        callback = getattr(self.bus, "receipt_status", None)
        status = callback(now_ns) if callable(callback) else None
        status = status if isinstance(status, dict) else {}
        def age(name):
            value = status.get(name)
            return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and 0 <= value < float("inf")) else None
        count = status.get("callback_received_messages")
        connection = getattr(self.bus, "connected", None)
        return dict(broker_connected=bool(connection and connection.is_set()),
            communication_fault_active=bool(self.fault_started_ns),
            callback_received_messages=count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None,
            last_message_age_s=age("last_message_age_s"), last_snapshot_age_s=age("last_snapshot_age_s"),
            snapshot_received=status.get("snapshot_received") if isinstance(status.get("snapshot_received"), bool) else None,
            age_clock="host_perf_counter_receipt")

    def _sample_transport(self, now_ns, transport=None):
        if not self._transport_monitor.due(now_ns):
            return
        transport = self._transport_status(now_ns) if transport is None else transport
        inbox = getattr(self.bus, "inbox", None)
        backlog = inbox.qsize() if isinstance(inbox, queue.Queue) else None
        age = ((now_ns - self.last_snapshot_ns) / 1e9
               if self.latest and now_ns >= self.last_snapshot_ns else None)
        point = self._transport_monitor.observe(now_ns, transport.get("callback_received_messages"),
            self._observer_stats["dequeued_messages"], backlog, age)
        if point is not None and self.journal:
            sample_id = self.journal.record_transport(point, sim_time=self.latest.get("sim_time"))
            sample = self.journal.transport_observation(sample_id)
            self._operations_monitor.observe(self.journal, now_ns=now_ns, sample=sample,
                transport=transport, snapshot=self.latest, processed_age_s=age,
                workers=[dict(pid=child.pid, exit_code=child.poll()) for child in self.children])

    def _operations_summary(self, now_ns):
        if not self.journal:
            return dict(available=False, thresholds=dict(THRESHOLDS), summary=dict(active=0,
                unacknowledged=0, total=0, health="unknown", health_reason="Az első rögzített megfigyelésre várunk."))
        result = self.journal.operations(limit=1)
        summary = result["summary"]
        observed = self._operations_monitor.summary(now_ns)
        summary.update(observed)
        if summary["active"] or summary["unacknowledged"]:
            summary.update(health="attention", health_reason="Aktív vagy még nem nyugtázott incidens igényel figyelmet.")
        elif (observed["observation_fresh"] and observed["known_rules"] == observed["total_rules"]
                and all(value == "healthy" for value in observed["quality"].values())):
            summary.update(health="healthy", health_reason="A négy ellenőrzés friss megfigyelése ismert; nincs megnyitott vagy nyugtázásra váró incidens.")
        else:
            summary.update(health="unknown", health_reason="A megfigyelés hiányos, elavult, az indulási türelmi időn belül van, vagy egy első hibás minta megerősítésére vár.")
        return dict(available=result["available"], thresholds=result["thresholds"], summary=summary)

    def _observer_status(self):
        stats = dict(self._observer_stats)
        waits = sorted(self._observer_waits)
        stats["queue_wait_p95_ms"] = waits[int((len(waits) - 1) * .95)] if waits else None
        return stats

    @staticmethod
    def _recovery_status(connected, transport, has_processed_snapshot=True):
        callback_age = transport.get("last_snapshot_age_s")
        available = bool(has_processed_snapshot and not connected and transport.get("broker_connected") is True
            and not transport.get("communication_fault_active")
            and isinstance(callback_age, (int, float)) and not isinstance(callback_age, bool)
            and 0 <= callback_age <= .75)
        return dict(pause_available=available, slow_available=available,
            reason=("Friss MQTT-állapot érkezik, de a kijelzett állapot elavult. Csak szünet és 1× lassítás kérhető."
                    if available else "Helyreállító szünet és lassítás csak friss MQTT-állapotérkezés mellett kérhető."))

    def state(self):
        with self.lock:
            now_ns = time.perf_counter_ns()
            transport = self._transport_status(now_ns)
            self._sample_transport(now_ns, transport)
            capacity = self._transport_monitor.snapshot(now_ns)
            observer = self._observer_status()
            operations = self._operations_summary(now_ns)
            if not self.latest:
                return {"run_id": self.run_id, "sim_time": 0, "connected": False,
                        "strategy": self.strategy, "seed": self.seed, "loading": True,
                        "mqtt": {"transport": transport, "observer": observer, "capacity": capacity, "last_message_age_s": None},
                        "control_recovery": self._recovery_status(False, transport, False),
                        "operations": operations,
                        "cars": [], "parking": [], "events": [], "metrics": {}, "warnings": ["A szimulátor első MQTT-állapotára várunk."]}
            state = copy.deepcopy(self.latest)
            age = (now_ns - self.last_snapshot_ns) / 1e9
            state.update(run_id=self.run_id, strategy=self.strategy, seed=self.seed,
                         max_active=self.config["simulation"].get("max_active", 6),
                         connected=transport["broker_connected"] and age < 0.75 and not self.fault_started_ns)
            state["parking"] = copy.deepcopy(self.central.get("slots", state.get("parking", [])))
            state["zones"] = copy.deepcopy(self.central.get("zones", []))
            state["warnings"] = list(state.get("warnings", [])) + self.central.get("warnings", [])
            if self.raw_oversize_dropped:
                state["warnings"].append("Egyes túlméretes nyers MQTT-üzenetek nem fértek a keretbe; az útnapló ettől függetlenül mentődik.")
            recovery = self._recovery_status(state["connected"], transport)
            state["control_recovery"] = recovery
            if age >= 0.75:
                state["warnings"].append("A kijelzett állapot lemaradt a friss MQTT-érkezésekhez képest; szünet vagy 1× lassítás kérhető."
                    if recovery["pause_available"] else "A kijelzett szimulátorállapot elavult; a friss MQTT-állapot visszajelzése hiányzik.")
            elif self.fault_started_ns:
                state["warnings"].append("Kommunikációs hibainjektálás: a szimulátor visszakapcsolódására várunk.")
            for p in self.children:
                if p.poll() is not None:
                    state["warnings"].append("Egy háttérfolyamat leállt. A munkamenet naplóját ellenőrizni kell.")
            ordered = sorted(self.latencies)
            state["mqtt"] = dict(published=state.get("worker_mqtt", {}).get("published", 0) + self.bus.published,
                                 received=self.received, latency_ms=sum(ordered) / len(ordered) if ordered else 0,
                                 latency_p95_ms=ordered[int((len(ordered)-1)*0.95)] if ordered else 0,
                                 last_message_age_s=age, invalid=self.bus.invalid,
                                 observer=observer, transport=transport, capacity=capacity,
                                 clock_resolution_ms=time.get_clock_info("perf_counter").resolution * 1000)
            state["logging"] = dict(self.journal.stats() if self.journal else {}, **self.raw_logging_stats())
            state["operations"] = operations
            return state

    def operations(self, run_id, **filters):
        run_id = self._identifier(run_id, "futásazonosító")
        with self._history_reader(run_id) as (reader, _):
            result = reader.operations(**filters)
        with self.lock:
            result["current_live"] = result["run_id"] == self.run_id
            if result["current_live"]:
                current = self._operations_summary(time.perf_counter_ns())
                # Counts and health describe the entire selected run, while
                # until_id freezes only the paged episode membership.
                result["summary"].update({key: value for key, value in current["summary"].items()
                    if key not in {"total", "active", "unacknowledged"}})
                result["summary"]["health_scope"] = "entire_current_run"
        return result

    def operations_report(self, run_id, *, until_id=None):
        run_id = self._identifier(run_id, "futásazonosító")
        try:
            with self._history_reader(run_id) as (reader, _):
                return reader.operations_report(until_id=until_id)
        except sqlite3.DatabaseError as error:
            raise ValueError("A megőrzött incidensarchívum nem olvasható") from error

    def operations_export(self, run_id, format="csv", *, until_id=None):
        run_id = self._identifier(run_id, "futásazonosító")
        if format not in {"csv", "json"}:
            raise ValueError("Az incidensexport formátuma csv vagy json lehet")
        context = self._history_reader(run_id)
        reader, _ = context.__enter__()
        try:
            iterator = reader.iter_operations_download(format, until_id=until_id)
        except BaseException:
            context.__exit__(*sys.exc_info())
            raise
        return _SnapshotStream(iterator, context)

    def acknowledge_operation(self, data):
        if set(data) - {"run_id", "id", "expected_revision", "note"}:
            raise ValueError("Ismeretlen nyugtázási mező")
        run_id = self._identifier(data.get("run_id"), "futásazonosító")
        with self.lock:
            if run_id != self.run_id:
                raise OperationsConflict("A kiválasztott futás már nem az aktuális futás; archivált incidens nem nyugtázható")
            if not self.journal:
                raise RuntimeError("A futás incidensnaplója még nem indult el")
            item, duplicate = self.journal.acknowledge_operation(data.get("id"), data.get("expected_revision"), data.get("note", ""))
            self._operations_monitor.acknowledged(item)
            return dict(run_id=run_id, item=item, already_acknowledged=duplicate)

    def logs(self, **filters):
        with self.lock:
            return self.journal.query(**filters) if self.journal else {"run_id": self.run_id, "items": [], "total": 0, "next_before": None}

    def scenario(self, requested=None):
        with self.lock:
            seed = requested.get("preview_seed", self.seed) if isinstance(requested, dict) else self.seed
            if isinstance(seed, bool) or not isinstance(seed, (int, float)) or not 0 <= seed < 2**32 or seed != int(seed):
                raise ValueError("Hibás előnézeti véletlenmag")
            payload = scenario_payload(self.config, requested if requested is not None else self.config.get("scenario"))
            payload["preview_seed"] = int(seed)
            payload["preview_occupied_slots"] = random.Random(int(seed)).sample(
                [s["id"] for s in payload["config"]["slots"]], payload["active"]["initial_occupied_count"])
            return payload

    def car_history(self, car_id):
        if not isinstance(car_id, str) or len(car_id) > 64:
            raise ValueError("Hibás autóazonosító")
        with self.lock:
            history = self.journal.car_history(car_id) if self.journal else {"run_id": self.run_id, "car_id": car_id, "telemetry": [], "events": []}
            history["current"] = next((copy.deepcopy(c) for c in self.latest.get("cars", []) if c["id"] == car_id), None)
            return history

    @staticmethod
    def _identifier(value, label):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError(f"Hibás {label}")
        return value

    def _run_files(self):
        """Discover metadata only; no raw logs or historical sample rows are loaded."""
        paths = set((ROOT / "results").glob("live-*/*-metadata.json"))
        if self.run_dir:
            paths.update(self.run_dir.glob("*-metadata.json"))
        found = {}
        for path in paths:
            run_id = path.name.removesuffix("-metadata.json")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
                journal = path.with_name(f"{run_id}-journal.sqlite3")
                if journal.is_file():
                    found[run_id] = (path, journal)
        if self.journal:
            found[self.run_id] = (None, self.journal.path)
        return found

    def runs(self, limit=100, before=""):
        count = min(200, max(1, int(limit)))
        with self.lock:
            current_id, files, current_metadata = self.run_id, self._run_files(), copy.deepcopy(self.metadata)
        items = []
        for run_id, (metadata_path, _) in files.items():
            try:
                metadata = current_metadata if metadata_path is None else json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            config = metadata.get("config", {})
            layout = config.get("layout", config.get("world", {}).get("layout", {}))
            items.append(dict(run_id=run_id, active=run_id == current_id,
                started_at_utc=metadata.get("started_at_utc", ""), seed=metadata.get("seed"),
                strategy=metadata.get("strategy"), layout_name=layout.get("name", layout.get("label", config.get("scenario", {}).get("preset", "Korábbi térkép"))),
                compact_history_available=bool(metadata.get("history_schema"))))
        items.sort(key=lambda item: (item["active"], item["started_at_utc"], item["run_id"]), reverse=True)
        total = len(items)
        if before:
            index = next((i for i, item in enumerate(items) if item["run_id"] == before), None)
            if index is None:
                raise ValueError("Ismeretlen futáslapozási azonosító")
            items = items[index + 1:]
        return dict(current_run_id=current_id, runs=items[:count], total=total,
            next_before=items[count - 1]["run_id"] if len(items) > count else None)

    @contextmanager
    def _history_reader(self, run_id=None):
        with self.lock:
            run_id = self._identifier(run_id or self.run_id, "futásazonosító")
            entry = self._run_files().get(run_id)
            if not entry:
                raise ValueError("A futás megőrzött naplója nem található")
            path, journal_path = entry
            if run_id == self.run_id and self.journal:
                self.journal.flush()
                metadata = copy.deepcopy(self.metadata)
                metadata.setdefault("config", copy.deepcopy(self.config))
            else:
                metadata = json.loads(path.read_text(encoding="utf-8"))
            reader = Journal.open_readonly(journal_path, run_id, snapshot=True)
        try:
            yield reader, metadata
        finally:
            reader.close()

    @staticmethod
    def _history_map(config):
        world, vehicle = config.get("world", {}), config.get("vehicle", {})
        return dict(width=world.get("width", 140), height=world.get("height", 90),
            network=config.get("network"), parking_areas=config.get("parking_areas", world.get("parking_areas", [])),
            production_exits=config.get("production_exits", world.get("production_exits", [])),
            layout=config.get("layout", world.get("layout", {})),
            roads=world.get("roads", [[[14, 58], [122, 58], [122, 24], [34, 24]]]),
            buildings=world.get("buildings", [{"x": 2, "y": 66, "w": 30, "h": 18, "label": "Gyártósor"}]),
            crossings=config.get("crossings", []), slots=config.get("slots", []), zones=config.get("zones", []),
            vehicle_length=vehicle.get("length", 4.5), vehicle_width=vehicle.get("width", 1.8))

    def cars(self, run_id=None, **filters):
        with self._history_reader(run_id) as (reader, _):
            result = reader.cars(**filters)
        with self.lock:
            active = {car["id"] for car in self.latest.get("cars", [])} if result["run_id"] == self.run_id else set()
        for car in result["cars"]:
            car["active"] = car["id"] in active
        return result

    def trip_history(self, car_id, run_id=None, **filters):
        car_id = self._identifier(car_id, "autóazonosító")
        with self._history_reader(run_id) as (reader, metadata):
            result = reader.trip_history(car_id, **filters)
        config = metadata.get("config", {})
        result["map"] = self._history_map(config)
        result["config"] = config
        if result["summary"].get("sample_period_s") is None:
            result["summary"]["sample_period_s"] = config.get("simulation", {}).get("telemetry_period")
        with self.lock:
            result["current"] = next((copy.deepcopy(car) for car in self.latest.get("cars", []) if car["id"] == car_id), None) if result["run_id"] == self.run_id else None
        return result

    def trip_export(self, car_id, run_id=None, format="csv"):
        car_id = self._identifier(car_id, "autóazonosító")
        if format not in {"csv", "json"}:
            raise ValueError("Az útnapló formátuma csv vagy json lehet")
        # Keep the independent read connection alive across streamed chunks;
        # resetting the live run cannot close an in-flight archive download.
        def chunks():
            with self._history_reader(run_id) as (reader, metadata):
                config = metadata.get("config", {})
                context = dict(metadata=metadata, config=config, map=self._history_map(config))
                yield from reader.iter_trip_export(car_id, format, context=context)
        return chunks()

    def trip_history_page(self, car_id, run_id=None, *, include_overview=False, include_mission=False, **filters):
        car_id = self._identifier(car_id, "autóazonosító")
        if include_overview not in (False, True, "0", "1"):
            raise ValueError("Az áttekintés kapcsolója 0 vagy 1 legyen")
        include_overview = include_overview in (True, "1")
        if include_mission not in (False, True, "0", "1"):
            raise ValueError("Az útösszesítés kapcsolója 0 vagy 1 legyen")
        include_mission = include_mission in (True, "1")
        with self._history_reader(run_id) as (reader, metadata):
            result = reader.trip_history_page(car_id, **filters)
            if include_overview:
                result["overview"] = reader.trip_overview(car_id, until_id=result["until_id"])
            if include_mission:
                result["mission"] = reader.trip_mission(car_id, until_id=result["until_id"],
                    events_until_id=result["events_until_id"])
        config = metadata.get("config", {})
        result.update(map=self._history_map(config), config=config, metadata=metadata)
        if result["summary"].get("sample_period_s") is None:
            result["summary"]["sample_period_s"] = config.get("simulation", {}).get("telemetry_period")
        with self.lock:
            result["current"] = next((copy.deepcopy(car) for car in self.latest.get("cars", [])
                if car["id"] == car_id), None) if result["run_id"] == self.run_id else None
        return result

    def trip_overview(self, car_id, run_id=None, **filters):
        car_id = self._identifier(car_id, "autóazonosító")
        with self._history_reader(run_id) as (reader, metadata):
            result = reader.trip_overview(car_id, **filters)
        config = metadata.get("config", {})
        result.update(map=self._history_map(config), config=config, metadata=metadata)
        return result

    def trip_mission(self, car_id, run_id=None, **filters):
        car_id = self._identifier(car_id, "autóazonosító")
        with self._history_reader(run_id) as (reader, _):
            return reader.trip_mission(car_id, **filters)

    def transport_history(self, run_id=None, **filters):
        try:
            with self._history_reader(run_id) as (reader, _):
                return reader.transport_history(**filters)
        except sqlite3.DatabaseError as error:
            raise ValueError("A megőrzött forgalmi archívum nem olvasható") from error

    def transport_export(self, run_id=None, format="csv", *, until_id=None):
        if format not in {"csv", "json"}:
            raise ValueError("A forgalmi archívum formátuma csv vagy json lehet")
        def chunks():
            try:
                with self._history_reader(run_id) as (reader, _):
                    yield from reader.iter_transport_export(format, until_id=until_id)
            except sqlite3.DatabaseError as error:
                raise ValueError("A megőrzött forgalmi archívum nem olvasható") from error
        return chunks()

    def transport_context(self, run_id=None, **filters):
        try:
            with self._history_reader(run_id) as (reader, metadata):
                result = reader.transport_context(**filters)
            config = metadata.get("config", {})
            result.update(map=self._history_map(config), config=config, metadata=metadata)
            return result
        except sqlite3.DatabaseError as error:
            raise ValueError("A megőrzött forgalmi mintához tartozó adatok nem olvashatók") from error

    @contextmanager
    def export_archive(self):
        """Build a complete ZIP with bounded RAM, without stopping the observer.

        The readonly WAL transaction protects retained rows during compression.
        Its lifetime is scoped to building the ZIP, and ends before downloading.
        Large compressed output spills into an automatically deleted temp file.
        """
        with self.lock:
            if not self.journal:
                raise RuntimeError("A futás naplója még nem indult el")
            state = self.state()
            self.journal.flush()
            run_id = self.run_id
            reader = Journal.open_readonly(self.journal.path, run_id)
            try:
                reader.begin_snapshot()
                metadata = copy.deepcopy(self.metadata)
                config = copy.deepcopy(self.config)
                metadata.update(exported_at_utc=datetime.now(timezone.utc).isoformat(),
                    logging=dict(reader.stats(), **self.raw_logging_stats()),
                    export_snapshot=reader.export_boundaries(),
                    transport_history_schema=1,
                    export_scope="Semantic decision/event/control journal, retained rich telemetry, complete received per-car observations/controller diagnostics, and recorded host-clock transport diagnostics; raw MQTT snapshots excluded")
            except BaseException:
                reader.close()
                raise
        try:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as output:
                try:
                    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                        archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
                        archive.writestr("config.json", json.dumps(config, ensure_ascii=False, indent=2))
                        if config.get("scenario"):
                            archive.writestr("scenario.json", json.dumps(config["scenario"], ensure_ascii=False, indent=2))
                        archive.writestr("state.json", json.dumps(state, ensure_ascii=False, indent=2))
                        for name, chunks in (
                                ("events.jsonl", reader.iter_records_export("jsonl")),
                                ("events.csv", reader.iter_records_export("csv")),
                                ("telemetry.csv", reader.iter_telemetry_export()),
                                ("complete_trips.jsonl", reader.iter_complete_trip_export()),
                                ("transport.csv", reader.iter_transport_export("csv")),
                                ("transport.json", reader.iter_transport_export("json")),
                                ("incidents.json", reader.iter_operations_export())):
                            with archive.open(name, "w", force_zip64=True) as member:
                                for chunk in chunks:
                                    member.write(chunk)
                        archive.writestr("README.txt", "A CSV UTF-8 kódolású. utc: gép UTC órája, sim_time: szimulációs másodperc.\n"
                            "telemetry.csv: a gördülő részletes ablakban őrzött, MQTT-n fogadott minták; m/s és m.\n"
                            "complete_trips.jsonl: a teljes fogadott útarchívum, az eredeti sensors és control mezőkkel; a nyers MQTT forgalom nem része a csomagnak.\n"
                            "transport.csv / transport.json: rögzített gépi órájú MQTT-forgalmi diagnosztika; elapsed_s szegmensenként indul, ráták üzenet/s, queue_messages közelítő sorhossz.\n"
                            "telemetry_until_id és events_until_id: az adott diagnosztikakor már feldolgozott bizonyíték határai; sim_time az utolsó feldolgozott modellállapot ideje, nem a forgalmi mérés órája.\n"
                            "incidents.json: a teljes megőrzött incidenskatalógus, külön aktív/megszűnt és nyugtázott állapottal. A nyugtázás kezelői naplóbejegyzés, nem járműparancs.\n"
                            "A rögzített exporthatárok, darabszámok, megőrzési korlátok és forrás-ellenőrzőösszegek a metadata.json fájlban szerepelnek.\n"
                            "A config.json a futás konfigurációja; minden fájl ugyanabból a fogadott pillanatképből készül.\n"
                            "A szenzoradatból rajzolt nézet sematikus rekonstrukció, nem kamerafelvétel; hiányzó megfigyelések nem kerülnek pótlásra.\n")
                finally:
                    # Release the WAL snapshot before a slow HTTP client reads
                    # the already finished file; no live rows remain pinned.
                    reader.close()
                length = output.tell()
                output.seek(0)
                yield output, run_id, length
        finally:
            reader.close()

    def export(self, format):
        if format not in {"csv", "jsonl", "zip"}:
            raise ValueError("A formátum csv, jsonl vagy zip lehet")
        if format == "zip":
            # Keep the convenience API used by small model/system tests. The
            # HTTP handler streams export_archive instead of materialising it.
            with self.export_archive() as (output, _, _):
                return output.read()
        with self.lock:
            if not self.journal:
                raise RuntimeError("A futás naplója még nem indult el")
            return self.journal.export(format)

    def control(self, data):
        """Accepted publication and observed application are distinct evidence."""
        with self.control_lock:
            data = dict(data)
            # Recovery admission is server evidence, never client-provided
            # metadata. Remove it before either logging or deciding whether
            # the current state qualifies for the restricted recovery path.
            data.pop("recovery_request", None)
            data["control_id"] = uuid.uuid4().hex[:16]
            with self.lock:
                if self.journal:
                    self.journal.record("control", "control_requested", "Kezelőművelet kérése.", sim_time=self.latest.get("sim_time"),
                                        car_id=data.get("car_id"), details=data)
            try:
                self._control(data)
            except (ValueError, KeyError, TypeError, RuntimeError) as error:
                with self.lock:
                    if self.journal:
                        self.journal.record("control", "control_rejected", f"Kezelőművelet elutasítva: {error}", level="warning",
                                            sim_time=self.latest.get("sim_time"), car_id=data.get("car_id"), details=data)
                raise
            with self.lock:
                if self.journal:
                    applied = data.get("action") in {"reset", "configure"}
                    self.journal.record("control", "control_applied" if applied else "control_published",
                                        "Új futás indítása alkalmazva." if applied else "Kezelőparancs átadva az MQTT-közvetítőnek.",
                                        sim_time=self.latest.get("sim_time"), car_id=data.get("car_id"), details=data)
            return {"control_id": data["control_id"], "run_id": self.run_id}

    def _control(self, data):
        if "recovery_request" in data:
            # Also protect direct private calls without mutating their input.
            # Public control() already removed this reserved client field.
            data = dict(data)
            data.pop("recovery_request", None)
        action = data.get("action")
        if action not in {"pause", "resume", "reset", "configure", "speed", "pedestrian", "block_slot", "sensor_failure", "disconnect", "freeze_car"}:
            raise ValueError("Ismeretlen művelet")
        if action == "configure":
            # Compile completely before stopping the existing run. Invalid edits
            # leave the running physical process and its resource owners intact.
            active, config = compile_scenario(self.config, data.get("scenario"))
            seed = int(data.get("seed", self.seed))
            strategy = data.get("strategy", self.strategy)
            if not 0 <= seed < 2**32 or strategy not in ("fixed", "nearest"):
                raise ValueError("Hibás mag vagy stratégia")
            with self.lock:
                self.config = config
                self.seed, self.strategy = seed, strategy
            self._new_run()
            if self.journal:
                self.journal.record("lifecycle", "scenario_configured", "A validált térkép és terhelés új futásban alkalmazva.",
                                    sim_time=0, details={"control_id": data["control_id"], "scenario": active,
                                                        "layout": config["layout"]})
            return
        if action == "reset":
            seed = int(data.get("seed", self.seed))
            strategy = data.get("strategy", self.strategy)
            if not 0 <= seed < 2**32 or strategy not in ("fixed", "nearest"):
                raise ValueError("Hibás mag vagy stratégia")
            with self.control_lock:
                self.seed, self.strategy = seed, strategy
                self._new_run()
            return
        control_state = self.state()
        if not control_state.get("connected"):
            recovery = control_state.get("control_recovery", {})
            recover_allowed = (action == "pause" and recovery.get("pause_available") is True) or (
                action == "speed" and not isinstance(data.get("value"), bool)
                and data.get("value") == 1 and recovery.get("slow_available") is True)
            if not recover_allowed:
                raise ValueError("A szimulátor MQTT-állapota nem friss; várd meg a helyreállást vagy indíts új futást")
            data["recovery_request"] = dict(
                reason=recovery["reason"], processed_snapshot_age_s=control_state["mqtt"]["last_message_age_s"],
                callback_snapshot_age_s=control_state["mqtt"]["transport"]["last_snapshot_age_s"],
                clock="host_perf_counter_receipt", allowed_actions="pause or speed=1 only")
        if action == "speed":
            if isinstance(data.get("value"), bool) or data.get("value") not in (1, 2, 4, 8):
                raise ValueError("A sebesség 1, 2, 4 vagy 8 lehet")
        if action == "pedestrian" and data.get("crossing_id") not in {c["id"] for c in self.config["crossings"]}:
            raise ValueError("Ismeretlen zebra")
        if action == "block_slot" and data.get("slot_id") not in {s["id"] for s in self.config["slots"]}:
            raise ValueError("Ismeretlen hely")
        if action in {"disconnect", "sensor_failure", "freeze_car"}:
            data["seconds"] = float(data.get("seconds", 8))
            if not 0 < data["seconds"] <= 60:
                raise ValueError("Az időtartam 0–60 szimulációs másodperc közötti lehet")
        if action == "disconnect" and self.state().get("paused"):
            raise ValueError("A kommunikációs hibához előbb folytasd a szimulációt")
        if action == "freeze_car" and data.get("car_id") not in {c["id"] for c in self.state().get("cars", [])}:
            raise ValueError("Ismeretlen jármű")
        if not self.bus.connected.is_set():
            raise ValueError("A kijelző MQTT-kapcsolata nem él")
        if action == "disconnect":
            with self.lock:
                self.fault_started_ns = time.perf_counter_ns()
        try:
            info = self.bus.publish("control", data, qos=1)
            info.wait_for_publish(timeout=2)
            if not info.is_published():
                raise RuntimeError("Az MQTT-közvetítő nem igazolta vissza a kezelőparancsot")
        except RuntimeError:
            if action == "disconnect":
                with self.lock:
                    self.fault_started_ns = 0
            raise

    def close(self):
        self.closed = True
        for p in self.children:
            self._stop(p)
        self._stop(self.broker_process)
        if self.bus:
            self.bus.close()
        with self.lock:
            if self.journal:
                self.journal.record("lifecycle", "application_closed", "A program leállt; az adatbázis kiírása megtörtént.",
                                    sim_time=self.latest.get("sim_time"))
                self.journal.close()
                self.journal = None
        for f in self.files:
            f.close()


def create_http_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _discard_request_body(self):
            """Boundedly drain rejected POST data before closing the connection.

            On Windows, closing a socket with unread input can reset the peer
            before it receives our error response. Never buffer a rejected
            payload or wait indefinitely for a client that stops sending it.
            """
            try:
                declared = int(self.headers.get("Content-Length", 0))
            except (ValueError, TypeError):
                return
            remaining = min(max(0, declared - self._request_body_read), 1024 * 1024)
            previous_timeout = self.connection.gettimeout()
            deadline = time.monotonic() + 0.5
            try:
                while remaining:
                    timeout = deadline - time.monotonic()
                    if timeout <= 0:
                        break
                    self.connection.settimeout(timeout)
                    chunk = self.rfile.read1(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass
            finally:
                self.connection.settimeout(previous_timeout)

        def _send(self, status, content, content_type="application/json; charset=utf-8", filename=None):
            raw = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                if content_type.startswith("image/svg+xml"):
                    self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except ConnectionError:
                # Closing/reloading a tab cancels its pending response normally.
                pass

        def _send_stream(self, chunks, content_type, filename, extra_headers=None):
            iterator = iter(chunks)
            # Resolve/validate the saved run before sending successful headers.
            first = next(iterator, b"")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.close_connection = True
            try:
                self.wfile.write(first)
                for chunk in iterator:
                    self.wfile.write(chunk)
            except ConnectionError:
                pass
            finally:
                if hasattr(iterator, "close"):
                    iterator.close()

        def _send_file(self, output, length, content_type, filename):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                while chunk := output.read(256 * 1024):
                    self.wfile.write(chunk)
            except ConnectionError:
                pass

        def do_GET(self):
            request = urlsplit(self.path)
            path, query = request.path, parse_qs(request.query)
            if path == "/api/state":
                self._send(200, app.state())
                return
            try:
                if path in {"/api/scenario", "/api/scenario/export"}:
                    raw = query.get("scenario", [None])[0]
                    if raw and len(raw) > 32768:
                        raise ValueError("Túl hosszú forgatókönyv")
                    requested = json.loads(raw) if raw is not None else None
                    payload = app.scenario(requested)
                    if path.endswith("/export"):
                        strategy = query.get("strategy", [app.strategy])[0]
                        if strategy not in {"nearest", "fixed"}:
                            raise ValueError("Ismeretlen stratégia")
                        exported = dict(schema_version=1, scenario=payload["active"], seed=payload["preview_seed"], strategy=strategy)
                        self._send(200, json.dumps(exported, ensure_ascii=False, indent=2).encode("utf-8"),
                                   filename=f"gyarudvar-{payload['active']['preset']}-{payload['preview_seed']}.json")
                    else:
                        self._send(200, payload)
                    return
                if path == "/api/logs":
                    filters = {key: query.get(key, [""])[0] for key in ("level", "category", "car_id", "search")}
                    if len(filters["search"]) > 256 or any(len(filters[k]) > 64 for k in ("level", "category", "car_id")):
                        raise ValueError("Túl hosszú naplószűrő")
                    filters["limit"] = min(500, max(1, int(query.get("limit", [100])[0])))
                    filters["before"] = int(query["before"][0]) if query.get("before") else None
                    self._send(200, app.logs(**filters))
                    return
                if path == "/api/car":
                    self._send(200, app.car_history(query.get("car_id", [""])[0]))
                    return
                if path == "/api/runs":
                    self._send(200, app.runs(limit=query.get("limit", [100])[0], before=query.get("before", [""])[0]))
                    return
                if path == "/api/operations":
                    # Empty explicit car/search values still select the V18
                    # metadata contract; absent extras preserve older clients.
                    operation_query = parse_qs(request.query, keep_blank_values=True)
                    self._send(200, app.operations(run_id=operation_query.get("run_id", [None])[0],
                        limit=operation_query.get("limit", [50])[0], before=operation_query.get("before", [None])[0],
                        status=operation_query.get("status", ["all"])[0], until_id=operation_query.get("until_id", [None])[0],
                        rule=operation_query.get("rule", [None])[0], severity=operation_query.get("severity", [None])[0],
                        car_id=operation_query.get("car_id", [None])[0], search=operation_query.get("search", [None])[0]))
                    return
                if path == "/api/operations/report":
                    self._send(200, app.operations_report(run_id=query.get("run_id", [None])[0],
                        until_id=query.get("until_id", [None])[0]))
                    return
                if path == "/api/operations/export":
                    from .operations_report import CSV_FORMULA_ESCAPE
                    format = query.get("format", ["csv"])[0]
                    run_id = app._identifier(query.get("run_id", [None])[0], "futásazonosító")
                    chunks = app.operations_export(run_id, format, until_id=query.get("until_id", [None])[0])
                    kind = "text/csv; charset=utf-8" if format == "csv" else "application/json; charset=utf-8"
                    self._send_stream(chunks, kind, f"incidens-naplo-{run_id}.{format}",
                        extra_headers={"X-CSV-Formula-Escape": CSV_FORMULA_ESCAPE} if format == "csv" else None)
                    return
                if path == "/api/transport/history":
                    self._send(200, app.transport_history(run_id=query.get("run_id", [None])[0],
                        after=query.get("after", [0])[0], limit=query.get("limit", [120])[0],
                        until_id=query.get("until_id", [None])[0]))
                    return
                if path == "/api/transport/export":
                    format = query.get("format", ["csv"])[0]
                    run_id = app._identifier(query.get("run_id", [app.run_id])[0], "futásazonosító")
                    chunks = app.transport_export(run_id, format, until_id=query.get("until_id", [None])[0])
                    kind = "text/csv; charset=utf-8" if format == "csv" else "application/json; charset=utf-8"
                    self._send_stream(chunks, kind, f"forgalmi-naplo-{run_id}.{format}")
                    return
                if path == "/api/transport/context":
                    self._send(200, app.transport_context(run_id=query.get("run_id", [None])[0],
                        sample_id=query.get("sample_id", [None])[0], cars_after=query.get("cars_after", [""])[0],
                        cars_limit=query.get("cars_limit", [25])[0], events_before=query.get("events_before", [None])[0],
                        events_limit=query.get("events_limit", [25])[0]))
                    return
                if path == "/api/cars":
                    search = query.get("search", [""])[0]
                    if len(search) > 128:
                        raise ValueError("Túl hosszú autószűrő")
                    self._send(200, app.cars(run_id=query.get("run_id", [None])[0], search=search,
                        limit=query.get("limit", [100])[0], before=query.get("before", [""])[0]))
                    return
                if path == "/api/car/history":
                    self._send(200, app.trip_history(query.get("car_id", [""])[0], run_id=query.get("run_id", [None])[0],
                        after=query.get("after", [0])[0], limit=query.get("limit", [2000])[0],
                        until_id=query.get("until_id", [None])[0], events_before=query.get("events_before", [None])[0],
                        events_until_id=query.get("events_until_id", [None])[0]))
                    return
                if path == "/api/car/history/page":
                    self._send(200, app.trip_history_page(query.get("car_id", [""])[0],
                        run_id=query.get("run_id", [None])[0], direction=query.get("direction", ["start"])[0],
                        cursor=query.get("cursor", [None])[0], sim_time=query.get("sim_time", [None])[0],
                        limit=query.get("limit", [2000])[0], until_id=query.get("until_id", [None])[0],
                        events_until_id=query.get("events_until_id", [None])[0],
                        include_overview=query.get("include_overview", ["0"])[0],
                        include_mission=query.get("include_mission", ["0"])[0]))
                    return
                if path == "/api/car/history/mission":
                    self._send(200, app.trip_mission(query.get("car_id", [""])[0],
                        run_id=query.get("run_id", [None])[0], until_id=query.get("until_id", [None])[0],
                        events_until_id=query.get("events_until_id", [None])[0]))
                    return
                if path == "/api/car/history/overview":
                    self._send(200, app.trip_overview(query.get("car_id", [""])[0],
                        run_id=query.get("run_id", [None])[0], until_id=query.get("until_id", [None])[0],
                        limit=query.get("limit", [4000])[0]))
                    return
                if path == "/api/car/history/export":
                    format = query.get("format", ["csv"])[0]
                    car_id, run_id = query.get("car_id", [""])[0], query.get("run_id", [app.run_id])[0]
                    chunks = app.trip_export(car_id, run_id, format)
                    kind = "text/csv; charset=utf-8" if format == "csv" else "application/json; charset=utf-8"
                    self._send_stream(chunks, kind, f"utnaplo-{run_id}-{car_id}.{format}")
                    return
                if path == "/api/export":
                    format = query.get("format", ["zip"])[0]
                    if format == "zip":
                        with app.export_archive() as (output, run_id, length):
                            self._send_file(output, length, "application/zip", f"yard-{run_id}.zip")
                        return
                    with app.lock:
                        data, run_id = app.export(format), app.run_id
                    kind = {"csv": "text/csv; charset=utf-8", "jsonl": "application/x-ndjson; charset=utf-8", "zip": "application/zip"}[format]
                    self._send(200, data, kind, f"yard-{run_id}.{format}")
                    return
            except (ValueError, TypeError) as error:
                self._send(400, {"error": str(error)})
                return
            except RuntimeError as error:
                self._send(503, {"error": str(error)})
                return
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css"),
                     "/perception.js": ("perception.js", "text/javascript"), "/perception-analysis.js": ("perception-analysis.js", "text/javascript"), "/perception.css": ("perception.css", "text/css"), "/favicon.ico": (None, None)}
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            elif path in files:
                name, kind = files[path]
                try:
                    self._send(200, (ROOT / "web" / name).read_bytes(), kind + "; charset=utf-8")
                except FileNotFoundError:
                    self._send(503, {"error": "A felület még nem érhető el"})
            else:
                self._send(404, {"error": "Nincs ilyen erőforrás"})

        def do_POST(self):
            self._request_body_read = 0
            if self.path not in {"/api/control", "/api/perception/export", "/api/operations/acknowledge"}:
                self._discard_request_body()
                self._send(404, {"error": "Nincs ilyen végpont"})
                return
            # Only a local browser may operate this local simulation.
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                self._discard_request_body()
                self._send(403, {"error": "Idegen böngészőeredet"})
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if self.path == "/api/perception/export":
                    if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
                        raise ValueError("URL-kódolt űrlap szükséges")
                    if not 0 < size <= 256 * 1024:
                        raise ValueError("Az exportkérés mérete legfeljebb 256 KiB lehet")
                    body = self.rfile.read(size)
                    self._request_body_read = len(body)
                    fields = parse_qs(body.decode("utf-8"), keep_blank_values=True,
                                      strict_parsing=True, max_num_fields=3, errors="strict")
                    if set(fields) != {"filename", "format", "content"} or any(len(value) != 1 for value in fields.values()):
                        raise ValueError("A fájlnév, formátum és tartalom egyszer szerepeljen az űrlapon")
                    raw, content_type, filename = _perception_download({key: value[0] for key, value in fields.items()})
                    self._send(200, raw, content_type, filename)
                    return
                if not 0 < size <= 65536:
                    raise ValueError("Hibás kérésméret")
                body = self.rfile.read(size)
                self._request_body_read = len(body)
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError("Objektum szükséges")
                result = app.acknowledge_operation(data) if self.path == "/api/operations/acknowledge" else app.control(data)
                self._send(200, dict(ok=True, **(result or {})))
            except OperationsConflict as error:
                self._discard_request_body()
                self._send(409, {"error": str(error)})
            except (ValueError, KeyError, TypeError) as error:
                self._discard_request_body()
                self._send(400, {"error": str(error)})
            except RuntimeError as error:
                self._send(503, {"error": str(error)})
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = server.server_port
    return server


def serve(app, port=8765):
    server = create_http_server(app, port)
    print(f"Gyári udvar: http://127.0.0.1:{port}\nNaplók: {app.run_dir}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
