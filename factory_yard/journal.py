"""Durable, queryable evidence received from the actual MQTT data path.

Host UTC is the receipt clock; ``sim_time`` is the model's clock in seconds.
Snapshot-embedded event lists are deliberately ignored, because the same events
already arrive on the events topic. Semantic rows are retained for the entire
run. Telemetry retains the newest 200,000 samples across the run, and exposes
its discarded sample count. A separate compact trip archive keeps every received
pose sample on disk for the entire run. A separate compressed archive preserves
every received sensor bundle, including repeated stale observations. It stores
observations, not camera images. Legacy journals can only recover their remaining
received telemetry, and disclose missing sensor coverage without inventing it.

Writes commit every 100 samples or after 0.5 seconds when another MQTT message
arrives, including snapshot heartbeats. Query, export, flush and close always
commit. The SQLite WAL additionally permits external
readers to inspect a flushed journal while the simulator is running.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any
import zlib


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _encode_safe(value: Any) -> str:
    """Encode an already-normalised object with the unchanged archive format."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def _encode(value: Any) -> str:
    return _encode_safe(_json_safe(value))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _limit(value: Any, default: int = 100) -> int:
    try:
        return max(1, min(int(value), 500))
    except (TypeError, ValueError, OverflowError):
        return default


def _level(value: Any) -> str:
    result = str(value or "info").lower()
    return {"warn": "warning", "critical": "error", "fatal": "error"}.get(result, result) if result in (
        "debug", "info", "warning", "warn", "error", "critical", "fatal") else "info"


def _category(value: Any) -> str:
    result = str(value or "lifecycle").lower()
    return {"decisions": "decision", "parking": "decision", "zone": "decision", "protocol": "transport",
            "model": "state", "controls": "control", "faults": "fault"}.get(result, result)


def _operations_match(payload, rule, severity, car_id, needle):
    """SQLite row predicate: exact fields and literal full Unicode casefold search.

    Decode one saved episode at a time inside the query. The cursor returns at
    most one bounded page, even when a matching older episode lies far beyond
    that page. No LIKE wildcard or text from the request becomes SQL syntax.
    """
    item = json.loads(payload)
    if not isinstance(item, dict):
        return 0
    if rule != "all" and item.get("rule") != rule:
        return 0
    if severity != "all" and item.get("severity") != severity:
        return 0
    if car_id and item.get("car_id") != car_id:
        return 0
    if needle and not any(isinstance(item.get(field), str) and needle in item[field].casefold()
            for field in ("title", "advice", "subject", "car_id", "rule", "note")):
        return 0
    return 1


class Journal:
    def __init__(self, path: Path, run_id: str, *, max_telemetry: int = 200_000):
        self.path, self.run_id = Path(path), str(run_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_telemetry = max(1, int(max_telemetry))
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.create_function("operations_match", 5, _operations_match, deterministic=True)
        self._closed, self._pending = False, 0
        self._readonly, self._compact, self._perception, self._transport, self._operations = False, True, True, True, True
        self._last_commit = time.monotonic()
        self._connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT NOT NULL,
                sim_time REAL, level TEXT NOT NULL, category TEXT NOT NULL,
                event_type TEXT NOT NULL, message TEXT NOT NULL, car_id TEXT,
                details TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS records_car ON records(car_id,id);
            CREATE INDEX IF NOT EXISTS records_category ON records(category,id);
            CREATE INDEX IF NOT EXISTS records_level ON records(level,id);
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT NOT NULL,
                car_id TEXT NOT NULL, sim_time REAL, speed REAL, x REAL, y REAL,
                state TEXT NOT NULL, details TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS telemetry_car ON telemetry(car_id,id);
            CREATE INDEX IF NOT EXISTS telemetry_car_time ON telemetry(car_id,sim_time,id);
            CREATE TABLE IF NOT EXISTS trip_samples (
                id INTEGER PRIMARY KEY, utc TEXT NOT NULL, car_id TEXT NOT NULL,
                sim_time REAL, speed REAL, x REAL, y REAL, state TEXT NOT NULL,
                heading REAL, slot_id TEXT, wait_reason TEXT NOT NULL,
                route_progress_m REAL, distance_m REAL, seq INTEGER, gap_before INTEGER NOT NULL,
                time_gap_before_s REAL NOT NULL,
                details TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trip_samples_car ON trip_samples(car_id,id);
            CREATE INDEX IF NOT EXISTS trip_samples_car_time ON trip_samples(car_id,sim_time,id);
            CREATE TABLE IF NOT EXISTS trip_cars (
                car_id TEXT PRIMARY KEY, sample_count INTEGER NOT NULL,
                first_sim_time REAL, last_sim_time REAL, first_utc TEXT, last_utc TEXT,
                last_state TEXT, slot_id TEXT, last_seq INTEGER, sequence_gaps INTEGER NOT NULL,
                time_gap_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trip_perception (
                id INTEGER PRIMARY KEY, car_id TEXT NOT NULL, utc TEXT NOT NULL,
                sim_time REAL, sensor_sim_time REAL, sampled INTEGER NOT NULL,
                fresh INTEGER NOT NULL, uncompressed_bytes INTEGER NOT NULL,
                payload BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trip_perception_car ON trip_perception(car_id,id);
            CREATE TABLE IF NOT EXISTS transport_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT NOT NULL,
                segment INTEGER NOT NULL, elapsed_s REAL NOT NULL,
                received_total INTEGER, dequeued_total INTEGER, queue_messages INTEGER,
                snapshot_age_s REAL, window_s REAL, arrival_rate_hz REAL,
                dequeue_rate_hz REAL, queue_growth_hz REAL, sim_time REAL,
                telemetry_until_id INTEGER NOT NULL, events_until_id INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS transport_samples_segment ON transport_samples(segment,id);
            CREATE TABLE IF NOT EXISTS operations_incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT, rule_key TEXT NOT NULL,
                active INTEGER NOT NULL, acknowledged INTEGER NOT NULL,
                revision INTEGER NOT NULL, opened_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS operations_incidents_status ON operations_incidents(active,acknowledged,id);
        """)
        previous = self._connection.execute("SELECT value FROM metadata WHERE key='run_id'").fetchone()
        if previous and previous["value"] != self.run_id:
            self._connection.close()
            self._closed = True
            raise ValueError("A napló adatbázisa másik futáshoz tartozik")
        self._connection.execute("INSERT OR IGNORE INTO metadata VALUES ('run_id',?)", (self.run_id,))
        self._connection.execute("INSERT OR IGNORE INTO metadata VALUES ('telemetry_total_received','0')")
        self._connection.execute("INSERT OR REPLACE INTO metadata VALUES ('max_telemetry',?)", (str(self.max_telemetry),))
        self._telemetry_total = int(self._connection.execute(
            "SELECT value FROM metadata WHERE key='telemetry_total_received'").fetchone()["value"])
        self._telemetry_retained = self._connection.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
        counters = self._connection.execute("SELECT COUNT(*) records,COALESCE(MAX(id),0) last_id FROM records").fetchone()
        self._records, self._last_id = counters["records"], counters["last_id"]
        migrated = self._connection.execute("SELECT value FROM metadata WHERE key='compact_history_schema'").fetchone()
        if not migrated:
            # Migration is bounded in memory and cannot recreate already deleted
            # legacy samples. Store that limitation before accepting new data.
            retained = self._telemetry_retained
            rows = self._connection.execute("SELECT * FROM telemetry ORDER BY id")
            while batch := rows.fetchmany(500):
                for row in batch:
                    self._archive_sample(dict(row), json.loads(row["details"]))
            self._connection.execute("INSERT INTO metadata VALUES ('compact_history_prior_missing',?)",
                                     (str(max(0, self._telemetry_total - retained)),))
            self._connection.execute("INSERT INTO metadata VALUES ('compact_history_schema','1')")
        self._history_prior_missing = int(self._connection.execute(
            "SELECT value FROM metadata WHERE key='compact_history_prior_missing'").fetchone()["value"])
        self._trip_samples = self._connection.execute("SELECT COUNT(*) FROM trip_samples").fetchone()[0]
        migrated_perception = self._connection.execute("SELECT value FROM metadata WHERE key='perception_history_schema'").fetchone()
        if not migrated_perception:
            # Only retained received payloads can recover old observations. The
            # compact pose archive is never used to generate missing sensors.
            rows = self._connection.execute("SELECT id,utc,car_id,sim_time,details FROM telemetry ORDER BY id")
            while batch := rows.fetchmany(500):
                for row in batch:
                    self._archive_perception(dict(row), json.loads(row["details"]).get("sensors"))
            self._connection.execute("INSERT INTO metadata VALUES ('perception_history_schema','1')")
        counts = self._connection.execute("SELECT COUNT(*),COALESCE(SUM(LENGTH(payload)),0),COALESCE(SUM(uncompressed_bytes),0) FROM trip_perception").fetchone()
        self._perception_samples, self._perception_compressed_bytes, self._perception_uncompressed_bytes = counts
        self._store_perception_counts()
        self._trim_telemetry()
        self._connection.commit()

    @classmethod
    def open_readonly(cls, path: Path, run_id: str, *, snapshot=False):
        """Read saved evidence without migrating, trimming, or touching its metadata."""
        instance = cls.__new__(cls)
        instance.path, instance.run_id = Path(path), str(run_id)
        instance._lock = threading.RLock()
        instance._connection = sqlite3.connect(instance.path.resolve().as_uri() + "?mode=ro", uri=True,
                                                check_same_thread=False, timeout=10)
        instance._connection.row_factory = sqlite3.Row
        instance._connection.create_function("operations_match", 5, _operations_match, deterministic=True)
        instance._closed, instance._pending, instance._readonly = False, 0, True
        # Pin before reading metadata and counters: otherwise a single HTTP
        # response can combine counts from one commit with rows from another.
        if snapshot:
            instance._connection.execute("BEGIN")
        metadata = dict(instance._connection.execute("SELECT key,value FROM metadata"))
        if metadata.get("run_id") != instance.run_id:
            instance._connection.close()
            raise ValueError("A napló adatbázisa másik futáshoz tartozik")
        instance._compact = bool(instance._connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trip_samples'").fetchone())
        instance._perception = bool(instance._connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trip_perception'").fetchone())
        instance._transport = bool(instance._connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transport_samples'").fetchone())
        instance._operations = bool(instance._connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations_incidents'").fetchone())
        instance._perception_samples = int(metadata.get("perception_total_received", 0))
        instance._perception_compressed_bytes = int(metadata.get("perception_compressed_bytes", 0))
        instance._perception_uncompressed_bytes = int(metadata.get("perception_uncompressed_bytes", 0))
        instance.max_telemetry = int(metadata.get("max_telemetry", 200000))
        instance._telemetry_total = int(metadata.get("telemetry_total_received", 0))
        instance._telemetry_retained = instance._connection.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
        instance._trip_samples = instance._connection.execute("SELECT COUNT(*) FROM " + ("trip_samples" if instance._compact else "telemetry")).fetchone()[0]
        instance._history_prior_missing = int(metadata.get("compact_history_prior_missing", max(0, instance._telemetry_total - instance._trip_samples)))
        counters = instance._connection.execute("SELECT COUNT(*) records,COALESCE(MAX(id),0) last_id FROM records").fetchone()
        instance._records, instance._last_id = counters["records"], counters["last_id"]
        instance._last_commit = time.monotonic()
        return instance

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("A napló már le van zárva")

    def begin_snapshot(self) -> None:
        """Pin an independent readonly view while live retention keeps advancing.

        A WAL reader does not stop the writer. Its transaction is released by
        ``close``; callers must keep this connection scoped to their export.
        """
        with self._lock:
            self._ensure_open()
            if not self._readonly:
                raise RuntimeError("Exportpillanatképhez külön olvasókapcsolat szükséges")
            self._connection.execute("BEGIN")
            self._connection.execute("SELECT value FROM metadata WHERE key='run_id'").fetchone()

    def _maybe_commit(self) -> None:
        self._pending += 1
        if self._pending >= 100 or time.monotonic() - self._last_commit >= 0.5:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if self._readonly:
            return
        self._connection.execute("UPDATE metadata SET value=? WHERE key='telemetry_total_received'",
                                 (str(self._telemetry_total),))
        self._store_perception_counts()
        self._connection.commit()
        self._pending = 0
        self._last_commit = time.monotonic()

    def _store_perception_counts(self) -> None:
        self._connection.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", (
            ("perception_total_received", str(self._perception_samples)),
            ("perception_compressed_bytes", str(self._perception_compressed_bytes)),
            ("perception_uncompressed_bytes", str(self._perception_uncompressed_bytes))))

    def _archive_perception(self, sample: dict, sensors: Any, *, safe_sensors: dict | None = None) -> tuple[int, int] | None:
        if not isinstance(sensors, dict):
            return None
        # Ingestion already normalises the rich telemetry details. Reuse its
        # sensor subtree for bytes, but keep the original received object for
        # scalar truthiness rules below. Migration/direct calls retain their
        # original independent normalisation path.
        raw = (_encode_safe(safe_sensors) if safe_sensors is not None else _encode(sensors)).encode("utf-8")
        compressed = zlib.compress(raw, level=3)
        cursor = self._connection.execute("""INSERT OR IGNORE INTO trip_perception
            (id,car_id,utc,sim_time,sensor_sim_time,sampled,fresh,uncompressed_bytes,payload)
            VALUES (?,?,?,?,?,?,?,?,?)""", (sample["id"], sample["car_id"], sample["utc"], sample["sim_time"],
            _number(sensors.get("sample_sim_time_s")), int(bool(sensors.get("sampled"))),
            int(bool(sensors.get("fresh")) and not bool(sensors.get("fault_active"))), len(raw), compressed))
        return (len(compressed), len(raw)) if cursor.rowcount else None

    def record(self, category, event_type, message, *, level="info", car_id=None,
               sim_time=None, details=None, utc=None) -> int:
        with self._lock:
            self._ensure_open()
            if self._readonly:
                raise RuntimeError("Az archivált napló csak olvasható")
            cursor = self._connection.execute("""INSERT INTO records
                (utc,sim_time,level,category,event_type,message,car_id,details)
                VALUES (?,?,?,?,?,?,?,?)""", (str(utc or _utc_now()), _number(sim_time), _level(level),
                _category(category), str(event_type or "event"), str(message or ""),
                str(car_id) if car_id is not None else None, _encode(details or {})))
            self._records += 1
            self._last_id = int(cursor.lastrowid)
            self._maybe_commit()
            return int(cursor.lastrowid)

    TRANSPORT_FIELDS = ("id", "utc", "segment", "elapsed_s", "received_total", "dequeued_total",
        "queue_messages", "snapshot_age_s", "window_s", "arrival_rate_hz", "dequeue_rate_hz",
        "queue_growth_hz", "sim_time", "telemetry_until_id", "events_until_id")

    @classmethod
    def _transport_item(cls, item):
        """Reject malformed persisted diagnostics instead of presenting invented zeros."""
        item = dict(item)
        if set(item) != set(cls.TRANSPORT_FIELDS):
            raise ValueError("A forgalmi archívum mezői hibásak")
        integers = ("id", "segment", "received_total", "dequeued_total", "queue_messages",
                    "telemetry_until_id", "events_until_id")
        nullable = {"received_total", "dequeued_total", "queue_messages"}
        for key in integers:
            value = item[key]
            if value is None and key in nullable:
                continue
            if (not isinstance(value, int) or isinstance(value, bool) or value < (1 if key == "id" else 0)
                    or value > 9_007_199_254_740_991):
                raise ValueError("A forgalmi archívum egészértékei hibásak")
        for key in ("elapsed_s", "snapshot_age_s", "window_s", "arrival_rate_hz", "dequeue_rate_hz",
                    "queue_growth_hz", "sim_time"):
            value = item[key]
            if value is None and key != "elapsed_s":
                continue
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or (key != "queue_growth_hz" and value < 0)):
                raise ValueError("A forgalmi archívum idő- vagy rátaértékei hibásak")
        try:
            timestamp = datetime.fromisoformat(item["utc"].replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("A forgalmi archívum UTC-időpontja hibás") from None
        rates = (item["arrival_rate_hz"], item["dequeue_rate_hz"])
        if item["window_s"] is None:
            if any(value is not None for value in (*rates, item["queue_growth_hz"])):
                raise ValueError("A forgalmi ráta mérési ablaka hiányzik")
        elif not 1 <= item["window_s"] <= 5 or any(value is None for value in rates):
            raise ValueError("A forgalmi ráta mérési ablaka hibás")
        return item

    def record_transport(self, point, *, sim_time=None, utc=None):
        """Persist one actually sampled host observation and its received evidence boundaries."""
        with self._lock:
            self._ensure_open()
            if self._readonly:
                raise RuntimeError("Az archivált napló csak olvasható")
            table = "trip_samples" if self._compact else "telemetry"
            telemetry_ceiling = self._connection.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0]
            snapshot_time = (float(sim_time) if isinstance(sim_time, (int, float))
                and not isinstance(sim_time, bool) and math.isfinite(sim_time) and sim_time >= 0 else None)
            item = dict(point, id=1, utc=str(utc or _utc_now()), sim_time=snapshot_time,
                        telemetry_until_id=telemetry_ceiling, events_until_id=self._last_id)
            self._transport_item(item)
            keys = self.TRANSPORT_FIELDS[1:]
            cursor = self._connection.execute("INSERT INTO transport_samples (" + ",".join(keys) + ") VALUES ("
                + ",".join("?" for _ in keys) + ")", tuple(item[key] for key in keys))
            self._maybe_commit()
            return int(cursor.lastrowid)

    def transport_observation(self, sample_id):
        """Read the just-persisted exact evidence coordinates without a prefix scan."""
        sample_id = self._navigation_integer(sample_id, "forgalmi mintaazonosító", minimum=1)
        with self._lock:
            self._ensure_open()
            row = self._connection.execute("SELECT * FROM transport_samples WHERE id=?", (sample_id,)).fetchone() if self._transport else None
            if row is None:
                raise ValueError("A forgalmi minta nem szerepel az archívumban")
            return self._transport_item(row)

    def operations_active(self):
        with self._lock:
            self._ensure_open()
            if not self._operations:
                return {}
            return {row["rule_key"]: json.loads(row["payload"]) for row in self._connection.execute(
                "SELECT rule_key,payload FROM operations_incidents WHERE active=1")}

    def save_operation(self, rule_key, item, *, transition=None):
        """Persist bounded incident scalars; audit only lifecycle transitions."""
        with self._lock:
            self._ensure_open()
            if self._readonly:
                raise RuntimeError("Az archivált incidensek csak olvashatók")
            item = _json_safe(dict(item))
            values = (int(item["active"]), int(item["acknowledged"]), item["revision"],
                      item["opened_utc"], item["last_seen_utc"])
            if "id" not in item:
                cursor = self._connection.execute("INSERT INTO operations_incidents "
                    "(rule_key,active,acknowledged,revision,opened_utc,last_seen_utc,payload) VALUES (?,?,?,?,?,?,?)",
                    (rule_key, *values, "{}"))
                item["id"] = int(cursor.lastrowid)
            self._connection.execute("UPDATE operations_incidents SET active=?,acknowledged=?,revision=?,opened_utc=?,last_seen_utc=?,payload=? WHERE id=?",
                                     (*values, _encode_safe(item), item["id"]))
            if transition:
                verbs = {"opened": "Megnyílt", "resolved": "Megszűnt", "acknowledged": "Nyugtázva"}
                self.record("operations", "incident_" + transition, verbs[transition] + ": " + item["title"],
                    car_id=item["car_id"], level=item["severity"] if transition == "opened" else "info",
                    sim_time=item["latest"]["sim_time"], details=dict(incident_id=item["id"], rule=item["rule"],
                    revision=item["revision"], active=item["active"], acknowledged=item["acknowledged"],
                    opening=item["opening"], latest=item["latest"], note=item["note"]),
                    utc=item["acknowledged_utc"] if transition == "acknowledged" else item["latest"]["utc"])
            self._maybe_commit()
            return item

    def save_operations_state(self, state):
        with self._lock:
            self._ensure_open()
            if self._readonly:
                raise RuntimeError("Az archivált incidensek csak olvashatók")
            self._connection.execute("INSERT OR REPLACE INTO metadata VALUES ('operations_state',?)", (_encode(state),))
            self._maybe_commit()

    def operations(self, *, limit=50, before=None, status="all", until_id=None,
                   rule=None, severity=None, car_id=None, search=None):
        from .operations import RULES, THRESHOLDS
        count = min(100, self._navigation_integer(limit, "incidens oldalméret", minimum=1))
        boundary = self._navigation_integer(before, "incidens lapozóazonosító", minimum=1)
        requested = self._navigation_integer(until_id, "felső incidensazonosító")
        if not isinstance(status, str) or status not in {"all", "active", "resolved", "unacknowledged", "acknowledged"}:
            raise ValueError("Ismeretlen incidensszűrő")
        include_filters = any(value is not None for value in (rule, severity, car_id, search)) or status in {"resolved", "acknowledged"}
        rule = "all" if rule is None else rule
        severity = "all" if severity is None else severity
        car_id = "" if car_id is None else car_id
        search = "" if search is None else search
        if not isinstance(rule, str) or rule not in {"all", *RULES}:
            raise ValueError("Ismeretlen incidensszabály")
        if not isinstance(severity, str) or severity not in {"all", "warning", "error"}:
            raise ValueError("Ismeretlen incidenssúlyosság")
        if not isinstance(car_id, str) or car_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", car_id):
            raise ValueError("Hibás incidens-autóazonosító")
        if not isinstance(search, str) or len(search) > 128:
            raise ValueError("Az incidenskeresés legfeljebb 128 karakter lehet")
        search = search.strip()
        filters = dict(status=status, rule=rule, severity=severity, car_id=car_id, search=search)
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            maximum = self._connection.execute("SELECT COALESCE(MAX(id),0) FROM operations_incidents").fetchone()[0] if self._operations else 0
            ceiling = maximum if requested is None else requested
            if ceiling > maximum or boundary is not None and boundary > ceiling:
                raise ValueError("Az incidens lapozási határ nem szerepel az archívumban")
            if boundary is not None and (not self._operations or not self._connection.execute(
                    "SELECT 1 FROM operations_incidents WHERE id=?", (boundary,)).fetchone()):
                raise ValueError("Az incidens lapozóazonosító nem szerepel az archívumban")
            saved = self._connection.execute("SELECT value FROM metadata WHERE key='operations_state'").fetchone()
            observed = json.loads(saved[0]) if saved else {}
            summary = dict(total=0, active=0, unacknowledged=0, known_rules=observed.get("known_rules", 0),
                total_rules=observed.get("total_rules", 4), observation_utc=observed.get("observation_utc"),
                quality=observed.get("quality", {}), observation_fresh=None,
                health="unknown", health_reason="Archivált megfigyelés; az aktuális állapot ebből nem állapítható meg.")
            rows, matching_count = [], None
            if self._operations:
                counts = self._connection.execute("SELECT COUNT(*),COALESCE(SUM(active),0),COALESCE(SUM(1-acknowledged),0) FROM operations_incidents").fetchone()
                prefix_count = self._connection.execute("SELECT COUNT(*) FROM operations_incidents WHERE id<=?", (ceiling,)).fetchone()[0]
                summary.update(total=counts[0], active=counts[1], unacknowledged=counts[2], prefix_count=prefix_count,
                               counts_scope="entire_selected_run")
                where, parameters = "id<=?", [ceiling]
                if status == "active":
                    where += " AND active=1"
                elif status == "resolved":
                    where += " AND active=0"
                elif status == "unacknowledged":
                    where += " AND acknowledged=0"
                elif status == "acknowledged":
                    where += " AND acknowledged=1"
                if rule != "all" or severity != "all" or car_id or search:
                    where += " AND operations_match(payload,?,?,?,?)=1"
                    parameters.extend((rule, severity, car_id, search.casefold()))
                if include_filters:
                    matching_count = self._connection.execute("SELECT COUNT(*) FROM operations_incidents WHERE " + where, parameters).fetchone()[0]
                if boundary is not None:
                    where += " AND id<?"
                    parameters.append(boundary)
                rows = self._connection.execute("SELECT payload FROM operations_incidents WHERE " + where + " ORDER BY id DESC LIMIT ?", (*parameters, count + 1)).fetchall()
            if summary["active"] or summary["unacknowledged"]:
                summary.update(health="attention", health_reason="Aktív vagy még nem nyugtázott incidens maradt a megőrzött futásban.")
            items = [json.loads(row[0]) for row in rows[:count]]
            result = dict(schema_version=1, run_id=self.run_id, available=self._operations, thresholds=dict(THRESHOLDS),
                summary=summary, items=items, next_before=items[-1]["id"] if len(rows) > count else None,
                until_id=ceiling, paging_scope="episode membership at until_id; lifecycle state is current within the saved run")
            if include_filters:
                result.update(filters=filters, matching_count=matching_count)
            return result

    def acknowledge_operation(self, incident_id, expected_revision, note=""):
        from .operations import OperationsConflict
        if incident_id is None or expected_revision is None:
            raise ValueError("Az incidensazonosító és az incidensverzió kötelező")
        incident_id = self._navigation_integer(incident_id, "incidensazonosító", minimum=1)
        expected_revision = self._navigation_integer(expected_revision, "incidensverzió", minimum=1)
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("A nyugtázási megjegyzés legfeljebb 500 karakter lehet")
        with self._lock:
            self._ensure_open()
            if self._readonly:
                raise RuntimeError("Az archivált incidens nem nyugtázható")
            row = self._connection.execute("SELECT rule_key,payload FROM operations_incidents WHERE id=?", (incident_id,)).fetchone()
            if row is None:
                raise ValueError("Ismeretlen incidensazonosító")
            item = json.loads(row["payload"])
            if item["acknowledged"] and expected_revision in (item["revision"], item.get("acknowledged_from_revision")):
                return item, True
            if expected_revision != item["revision"]:
                raise OperationsConflict("Az incidens állapota megváltozott; frissítse a listát a nyugtázás előtt")
            item.update(acknowledged=True, acknowledged_utc=_utc_now(), note=note.strip() or None,
                        acknowledged_from_revision=item["revision"], revision=item["revision"] + 1)
            item = self.save_operation(row["rule_key"], item, transition="acknowledged")
            self._flush_locked()
            return item, False

    def iter_operations_export(self):
        """Stream the entire saved incident catalog, with bounded page memory."""
        catalog = self.operations(limit=100)
        header = {key: value for key, value in catalog.items() if key not in {"items", "next_before"}}
        yield (_encode(header)[:-1] + ',"items":[').encode("utf-8")
        first = True
        if self._operations:
            with self._lock:
                rows = self._connection.execute("SELECT payload FROM operations_incidents WHERE id<=? ORDER BY id DESC", (catalog["until_id"],))
                while batch := rows.fetchmany(100):
                    for row in batch:
                        yield (("" if first else ",") + row[0]).encode("utf-8")
                        first = False
        yield b"]}"

    def operations_report(self, *, until_id=None):
        """Aggregate a complete episode prefix without materialising its catalog."""
        from .operations_report import REPORT_SCOPE, ReportAccumulator
        requested = self._navigation_integer(until_id, "felső incidensazonosító")
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            maximum = self._connection.execute("SELECT COALESCE(MAX(id),0) FROM operations_incidents").fetchone()[0] if self._operations else 0
            ceiling = maximum if requested is None else requested
            if ceiling > maximum:
                raise ValueError("Az incidens felső határa nem szerepel az archívumban")
            result = dict(schema_version=1, run_id=self.run_id, available=self._operations,
                until_id=ceiling, captured_at_utc=_utc_now(), scope=REPORT_SCOPE,
                counts=None, recurrent_episodes=None, durations=None, rules=[])
            if not self._operations:
                return result
            recurrent = self._connection.execute("""SELECT COALESCE(SUM(episodes-1),0) FROM (
                SELECT COUNT(*) episodes FROM operations_incidents WHERE id<=? GROUP BY rule_key
            )""", (ceiling,)).fetchone()[0]
            accumulator = ReportAccumulator()
            rows = self._connection.execute("SELECT payload FROM operations_incidents WHERE id<=? ORDER BY id", (ceiling,))
            while batch := rows.fetchmany(100):
                for row in batch:
                    accumulator.add(json.loads(row["payload"]))
            result.update(accumulator.payload(), recurrent_episodes=int(recurrent))
            return result

    def iter_operations_download(self, format="csv", *, until_id=None):
        """Validate eagerly, then stream saved episodes and one matching report.

        The caller keeps a pinned readonly connection open for the iterator's
        entire lifetime. The existing ZIP incidents.json contract is separate.
        """
        from .operations_report import CSV_FIELDS, CSV_FORMULA_ESCAPE, episode_csv_row
        if format not in {"csv", "json"}:
            raise ValueError("Az incidensexport formátuma csv vagy json lehet")
        report = self.operations_report(until_id=until_id)
        if not report["available"]:
            raise ValueError("Ehhez a futáshoz nem készült incidensarchívum")

        def chunks():
            if format == "csv":
                output = io.StringIO(newline="")
                csv.DictWriter(output, fieldnames=CSV_FIELDS).writeheader()
                yield output.getvalue().encode("utf-8-sig")
            else:
                metadata = {key: report[key] for key in ("schema_version", "run_id", "available",
                    "until_id", "captured_at_utc", "scope")}
                metadata.update(csv_formula_escape=CSV_FORMULA_ESCAPE, report=report)
                yield (_encode(metadata)[:-1] + ',"items":[').encode("utf-8")
            first, boundary = True, report["until_id"] + 1
            while True:
                with self._lock:
                    self._ensure_open()
                    rows = self._connection.execute("SELECT id,rule_key,payload FROM operations_incidents "
                        "WHERE id<? AND id<=? ORDER BY id DESC LIMIT 100", (boundary, report["until_id"])).fetchall()
                if not rows:
                    break
                for row in rows:
                    if format == "csv":
                        output = io.StringIO(newline="")
                        csv.DictWriter(output, fieldnames=CSV_FIELDS).writerow(
                            episode_csv_row(json.loads(row["payload"]), row["rule_key"], report))
                        yield output.getvalue().encode("utf-8")
                    else:
                        # Keep the saved JSON unchanged, including notes and Unicode.
                        yield (("" if first else ",") + row["payload"]).encode("utf-8")
                    first = False
                boundary = rows[-1]["id"]
            if format == "json":
                yield b"]}"
        return chunks()

    def transport_history(self, *, after=0, limit=120, until_id=None):
        """Read a frozen diagnostic prefix, bounded pages and a complete-prefix summary."""
        after = self._navigation_integer(after, "forgalmi lapozóazonosító", default=0)
        count = min(500, self._navigation_integer(limit, "forgalmi oldalméret", minimum=1))
        requested = self._navigation_integer(until_id, "felső forgalmi mintaazonosító")
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            maximum = self._connection.execute("SELECT COALESCE(MAX(id),0) FROM transport_samples").fetchone()[0] if self._transport else 0
            ceiling = maximum if requested is None else requested
            if ceiling > maximum or after > ceiling:
                raise ValueError("A forgalmi lapozási határ nem szerepel az archívumban")
            if after and not self._connection.execute("SELECT 1 FROM transport_samples WHERE id=?", (after,)).fetchone():
                raise ValueError("A forgalmi lapozóazonosító nem szerepel az archívumban")
            summary = dict(sample_count=0, segment_count=0, known_queue_count=0, valid_rate_count=0,
                peak_queue_messages=None, peak_arrival_rate_hz=None, first_utc=None, last_utc=None)
            points, segments, previous = [], set(), None
            if self._transport:
                # Bounded RAM even for long runs. Summaries validate the entire
                # frozen prefix, including malformed rows outside the displayed page.
                rows = self._connection.execute("SELECT * FROM transport_samples WHERE id<=? ORDER BY id", (ceiling,))
                while batch := rows.fetchmany(500):
                    for row in batch:
                        item = self._transport_item(row)
                        if previous and (item["segment"] < previous["segment"] or
                                (item["segment"] == previous["segment"] and item["elapsed_s"] <= previous["elapsed_s"])):
                            raise ValueError("A forgalmi archívum szegmenssorrendje hibás")
                        previous = item
                        summary["sample_count"] += 1
                        segments.add(item["segment"])
                        if summary["first_utc"] is None:
                            summary["first_utc"] = item["utc"]
                        summary["last_utc"] = item["utc"]
                        if item["queue_messages"] is not None:
                            summary["known_queue_count"] += 1
                            summary["peak_queue_messages"] = max(summary["peak_queue_messages"] or 0, item["queue_messages"])
                        if item["arrival_rate_hz"] is not None:
                            summary["valid_rate_count"] += 1
                            summary["peak_arrival_rate_hz"] = max(summary["peak_arrival_rate_hz"] or 0, item["arrival_rate_hz"])
                        if item["id"] > after and len(points) <= count:
                            points.append(item)
            summary["segment_count"] = len(segments)
            has_more = len(points) > count
            points = points[:count]
            return dict(schema_version=1, scope="recorded_host_clock_transport_observations", clock="host_perf_counter",
                run_id=self.run_id, available=self._transport,
                availability="recorded" if summary["sample_count"] else "empty" if self._transport else "not_recorded",
                until_id=ceiling, total_count=summary["sample_count"], points=points,
                has_more=has_more, next_after=points[-1]["id"] if has_more else None, summary=summary)

    def transport_context(self, sample_id, *, cars_after="", cars_limit=25,
                          events_before=None, events_limit=25):
        """Correlate one saved host observation with its already received evidence.

        Receipt ID ceilings, rather than simulation timestamps or current car
        summaries, freeze both pages. They describe what the observer had
        received; they do not establish physical simultaneity or causality.
        Scalar poses stay bounded and never decompress archived sensor bundles.
        """
        sample_id = self._navigation_integer(sample_id, "forgalmi mintaazonosító", minimum=1)
        if sample_id is None:
            raise ValueError("A forgalmi mintaazonosító szükséges")
        if cars_limit is None or events_limit is None:
            raise ValueError("A lapozási oldalméret szükséges")
        cars_count = min(100, self._navigation_integer(cars_limit, "autóoldalméret", minimum=1))
        events_count = min(100, self._navigation_integer(events_limit, "eseményoldalméret", minimum=1))
        event_boundary = self._navigation_integer(events_before, "eseménylapozó azonosító", minimum=1)
        if not isinstance(cars_after, str) or (cars_after and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cars_after)):
            raise ValueError("Az autólapozó azonosító hibás")
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            if not self._transport:
                raise ValueError("Ehhez a futáshoz nem készült forgalmi archívum")
            row = self._connection.execute("SELECT * FROM transport_samples WHERE id=?", (sample_id,)).fetchone()
            if row is None:
                raise ValueError("A forgalmi minta nem szerepel az archívumban")
            sample = self._transport_item(row)
            telemetry_ceiling, event_ceiling = sample["telemetry_until_id"], sample["events_until_id"]
            table = "trip_samples" if self._compact else "telemetry"
            if cars_after and not self._connection.execute(
                    f"SELECT 1 FROM {table} WHERE car_id=? AND id<=? LIMIT 1", (cars_after, telemetry_ceiling)).fetchone():
                raise ValueError("Az autólapozó azonosító nem szerepel a kiválasztott minta előzményében")
            if event_boundary is not None and not self._connection.execute(
                    "SELECT 1 FROM records WHERE id=? AND id<=?", (event_boundary, event_ceiling)).fetchone():
                raise ValueError("Az eseménylapozó azonosító nem szerepel a kiválasztott minta előzményében")
            total = self._connection.execute(
                f"SELECT COUNT(DISTINCT car_id) FROM {table} WHERE id<=?", (telemetry_ceiling,)).fetchone()[0]
            extra = ("heading,slot_id,wait_reason,seq,gap_before,time_gap_before_s" if self._compact else
                     "NULL heading,NULL slot_id,NULL wait_reason,NULL seq,NULL gap_before,NULL time_gap_before_s")
            rows = self._connection.execute(f"""SELECT id,car_id,utc,sim_time,x,y,speed,state,{extra},details
                FROM {table} WHERE id IN (
                    SELECT MAX(id) FROM {table} WHERE id<=? AND car_id>? GROUP BY car_id)
                ORDER BY car_id LIMIT ?""", (telemetry_ceiling, cars_after, cars_count + 1)).fetchall()
            vehicles = []
            for row in rows[:cars_count]:
                item = _json_safe(dict(row))
                details = json.loads(item.pop("details"))
                item["position_source"] = details.get("position_source") if isinstance(details, dict) else None
                vehicles.append(item)
            if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item["car_id"]) for item in vehicles):
                raise ValueError("Az archivált autóazonosító hibás")
            more = len(rows) > cars_count
            events = self.query(limit=events_count, before=event_boundary, until_id=event_ceiling)
            return dict(schema_version=1, scope="received_evidence_at_transport_sample", run_id=self.run_id,
                sample=sample, telemetry_until_id=telemetry_ceiling, events_until_id=event_ceiling,
                vehicles=dict(items=vehicles, total_count=total, has_more=more,
                    next_after=vehicles[-1]["car_id"] if more else None,
                    source=table if self._compact else "legacy_retained_telemetry",
                    complete_received_archive=self._compact and self._history_prior_missing == 0,
                    prior_missing_samples_global=self._history_prior_missing),
                events={key: events[key] for key in ("items", "total", "next_before")})

    def iter_transport_export(self, format="csv", *, until_id=None):
        """Stream exactly the frozen diagnostic prefix; JSON carries summary and schema."""
        if format not in {"csv", "json"}:
            raise ValueError("A forgalmi archívum formátuma csv vagy json lehet")
        page = self.transport_history(limit=500, until_id=until_id)
        if format == "csv":
            output = io.StringIO(newline="")
            csv.DictWriter(output, fieldnames=("run_id", *self.TRANSPORT_FIELDS)).writeheader()
            yield output.getvalue().encode("utf-8-sig")
        else:
            metadata = {key: value for key, value in page.items() if key not in ("points", "has_more", "next_after")}
            yield (_encode(metadata)[:-1] + ',"points":[').encode("utf-8")
        first, boundary = True, 0
        while self._transport:
            with self._lock:
                self._ensure_open()
                rows = self._connection.execute("SELECT * FROM transport_samples WHERE id>? AND id<=? ORDER BY id LIMIT 500",
                    (boundary, page["until_id"])).fetchall()
            if not rows:
                break
            for row in rows:
                item = self._transport_item(row)
                if format == "csv":
                    output = io.StringIO(newline="")
                    csv.DictWriter(output, fieldnames=("run_id", *self.TRANSPORT_FIELDS)).writerow(dict(run_id=self.run_id, **item))
                    yield output.getvalue().encode("utf-8")
                else:
                    yield (("" if first else ",") + _encode(item)).encode("utf-8")
                first = False
            boundary = rows[-1]["id"]
        if format == "json":
            yield b"]}"

    def _trim_telemetry(self) -> None:
        excess = self._telemetry_retained - self.max_telemetry
        if excess > 0:
            self._connection.execute("DELETE FROM telemetry WHERE id IN (SELECT id FROM telemetry ORDER BY id LIMIT ?)",
                                     (excess,))
            self._telemetry_retained -= excess

    def _archive_sample(self, sample: dict, details: dict) -> None:
        previous = self._connection.execute("SELECT last_seq,last_sim_time FROM trip_cars WHERE car_id=?",
                                            (sample["car_id"],)).fetchone()
        sequence = _number(details.get("seq"))
        sequence = int(sequence) if sequence is not None and sequence == int(sequence) else None
        previous_seq = previous["last_seq"] if previous else None
        gap = max(0, sequence - previous_seq - 1) if sequence is not None and previous_seq is not None else 0
        period = _number(details.get("period_s")) or 0.1
        delta = (sample["sim_time"] - previous["last_sim_time"]) if previous and sample["sim_time"] is not None and previous["last_sim_time"] is not None else 0
        time_gap = delta if delta > period * 1.5 + 1e-6 else 0
        compact = {key: details.get(key) for key in ("source", "position_source", "position_uncertainty_m", "period_s",
                   "nominal_period_s", "sample_interval_s", "sampling",
                   "message_id", "producer_seq", "reservation_id") if key in details}
        compact["mqtt"] = details.get("mqtt", {})
        if isinstance(details.get("control"), dict):
            compact["control"] = details["control"]
        self._connection.execute("""INSERT INTO trip_samples
            (id,utc,car_id,sim_time,speed,x,y,state,heading,slot_id,wait_reason,route_progress_m,distance_m,seq,gap_before,time_gap_before_s,details)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            sample["id"], sample["utc"], sample["car_id"], sample["sim_time"], sample["speed"], sample["x"],
            sample["y"], sample["state"], _number(details.get("heading")), details.get("slot_id"),
            str(details.get("wait_reason", "")), _number(details.get("route_progress_m")),
            _number(details.get("distance_m")), sequence, gap, time_gap, _encode(compact)))
        self._connection.execute("""INSERT INTO trip_cars
            (car_id,sample_count,first_sim_time,last_sim_time,first_utc,last_utc,last_state,slot_id,last_seq,sequence_gaps,time_gap_count)
            VALUES (?,1,?,?,?,?,?,?,?,?,?) ON CONFLICT(car_id) DO UPDATE SET
            sample_count=sample_count+1,last_sim_time=excluded.last_sim_time,last_utc=excluded.last_utc,
            last_state=excluded.last_state,slot_id=excluded.slot_id,
            last_seq=COALESCE(excluded.last_seq,trip_cars.last_seq),sequence_gaps=sequence_gaps+excluded.sequence_gaps,
            time_gap_count=time_gap_count+excluded.time_gap_count""", (
            sample["car_id"], sample["sim_time"], sample["sim_time"], sample["utc"], sample["utc"],
            sample["state"], details.get("slot_id"), sequence, gap, int(time_gap > 0)))

    @staticmethod
    def _details(topic: str, payload: dict, received_ns: int, latency_ms: float, utc: str) -> dict:
        explicit = payload.get("details")
        result = dict(explicit) if isinstance(explicit, dict) else {}
        result.update({key: value for key, value in payload.items() if key not in (
            "details", "message", "category", "level", "event_type", "type", "car_id", "sim_time")})
        result["mqtt"] = {"topic": topic, "received_ns": received_ns,
                          "latency_ms": _number(latency_ms), "received_utc": utc}
        return result

    def ingest(self, topic, payload, received_ns, latency_ms) -> int | None:
        if not isinstance(payload, dict) or payload.get("run_id", self.run_id) != self.run_id:
            return None
        with self._lock:
            self._ensure_open()
            # Heartbeats keep the durability bound even when a paused model no
            # longer generates state transitions or telemetry samples.
            if self._pending and time.monotonic() - self._last_commit >= 0.5:
                self._flush_locked()
        topic = str(topic)
        if topic in ("snapshot", "coordinator") or topic.startswith("sensors/"):
            return None
        # The adapter captures UTC at callback receipt. Observer queueing or a
        # concurrent export must not shift the reported receipt time forward.
        # Direct model/unit-test calls retain the host-time fallback.
        utc = payload.get("received_utc") or _utc_now()
        utc = str(utc)
        details = self._details(topic, payload, received_ns, latency_ms, utc)
        car_id = payload.get("car_id")
        if topic.startswith("telemetry/") or topic.startswith("diagnostics/perception/"):
            car_id = car_id or (topic.rsplit("/", 1)[-1] if topic.startswith("diagnostics/perception/") else topic.partition("/")[2])
            if not car_id:
                return None
            with self._lock:
                self._ensure_open()
                if self._readonly:
                    raise RuntimeError("Az archivált napló csak olvasható")
                safe_details = _json_safe(details)
                cursor = self._connection.execute("""INSERT INTO telemetry
                    (utc,car_id,sim_time,speed,x,y,state,details) VALUES (?,?,?,?,?,?,?,?)""",
                    (utc, str(car_id), _number(payload.get("sim_time")), _number(payload.get("speed")),
                     _number(payload.get("x")), _number(payload.get("y")), str(payload.get("state", "unknown")),
                     _encode_safe(safe_details)))
                self._telemetry_total += 1
                self._telemetry_retained += 1
                sample = dict(id=int(cursor.lastrowid), utc=utc, car_id=str(car_id),
                    sim_time=_number(payload.get("sim_time")), speed=_number(payload.get("speed")),
                    x=_number(payload.get("x")), y=_number(payload.get("y")), state=str(payload.get("state", "unknown")))
                self._archive_sample(sample, details)
                safe_sensors = safe_details.get("sensors")
                # JSON packets have string keys. For historical/direct Python
                # calls, a different key can stringify to "sensors" and win
                # normalisation's key collision. It must not replace the
                # independently archived original sensor object.
                if any(not isinstance(key, str) and str(key) == "sensors" for key in details):
                    safe_sensors = None
                sizes = self._archive_perception(sample, details.get("sensors"),
                    safe_sensors=safe_sensors)
                if sizes:
                    self._perception_samples += 1
                    self._perception_compressed_bytes += sizes[0]
                    self._perception_uncompressed_bytes += sizes[1]
                self._trip_samples += 1
                self._trim_telemetry()
                self._maybe_commit()
                return int(cursor.lastrowid)
        if not (topic in ("events", "audit", "control", "diagnostics") or
                topic.startswith(("audit/", "commands/", "requests/"))):
            return None
        event_type = payload.get("event_type") or payload.get("type") or payload.get("kind") or payload.get("action")
        if topic == "control":
            category, event_type = "control", event_type or "control_requested"
            message = payload.get("message") or f"Vezérlési kérés: {event_type}"
        elif topic.startswith("commands/"):
            category, event_type = "decision", event_type or "command"
            message = payload.get("message") or payload.get("reason") or {
                "parking_grant": "Parkolófoglalás kiadva", "zone_grant": "Konfliktuszóna-engedély kiadva"
            }.get(event_type, f"Koordinátori parancs: {event_type}")
        elif topic.startswith("requests/"):
            category, event_type = "decision", event_type or f"{topic.partition('/')[2]}_request"
            message = payload.get("message") or f"Erőforráskérés: {topic.partition('/')[2]}"
        else:
            event_type = event_type or "event"
            category = payload.get("category") or (
                "state" if event_type == "state_changed" else
                "fault" if event_type in ("fault", "collision", "sensor_failure", "sensor_restored", "car_frozen",
                                         "car_unfrozen", "connection_lost", "connection_restored") else
                "control" if event_type.startswith("control_") else "lifecycle")
            message = payload.get("message") or payload.get("reason") or str(event_type)
        level = payload.get("level") or ("error" if event_type == "collision" else
                                        "warning" if category == "fault" and event_type not in (
                                            "sensor_restored", "car_unfrozen", "connection_restored") else "info")
        return self.record(category, event_type, message, level=level, car_id=car_id,
                           sim_time=payload.get("sim_time"), details=details, utc=utc)

    @staticmethod
    def _item(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["details"] = json.loads(item["details"])
        return item

    def query(self, limit=100, level="", category="", car_id="", search="", before=None, until_id=None) -> dict:
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            clauses, values = [], []
            for column, value in (("level", level), ("category", category), ("car_id", car_id)):
                if value:
                    normalized = _level(value) if column == "level" else _category(value) if column == "category" else str(value)
                    clauses.append(f"{column}=?")
                    values.append(normalized[:128])
            if search:
                escaped = str(search)[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append("(message LIKE ? ESCAPE '\\' OR event_type LIKE ? ESCAPE '\\' OR details LIKE ? ESCAPE '\\')")
                values.extend([f"%{escaped}%"] * 3)
            if until_id is not None:
                ceiling = self._navigation_integer(until_id, "felső eseményazonosító")
                clauses.append("id<=?")
                values.append(ceiling)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = self._connection.execute("SELECT COUNT(*) FROM records" + where, values).fetchone()[0]
            if before is not None:
                try:
                    boundary = int(before)
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("A napló lapozási azonosítója egész szám legyen") from None
                clauses.append("id<?")
                values.append(boundary)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            count = _limit(limit)
            rows = self._connection.execute("SELECT * FROM records" + where + " ORDER BY id DESC LIMIT ?",
                                            values + [count + 1]).fetchall()
            more = len(rows) > count
            items = [self._item(row) for row in rows[:count]]
            return {"run_id": self.run_id, "items": items, "next_before": items[-1]["id"] if more else None,
                    "total": total}

    def car_history(self, car_id, limit=200) -> dict:
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            car_id = str(car_id)[:128]
            samples = self._connection.execute("""SELECT id,utc,sim_time,speed,x,y,state FROM telemetry
                WHERE car_id=? ORDER BY id DESC LIMIT ?""", (car_id, _limit(limit, 200))).fetchall()
            events = self._connection.execute("SELECT * FROM records WHERE car_id=? ORDER BY id DESC LIMIT ?",
                                              (car_id, _limit(limit, 200))).fetchall()
            return {"run_id": self.run_id, "car_id": car_id, "telemetry": [dict(row) for row in reversed(samples)],
                    "events": [self._item(row) for row in events]}

    def stats(self) -> dict:
        with self._lock:
            self._ensure_open()
            return {"records": self._records, "last_id": self._last_id, "telemetry_records": self._telemetry_retained,
                                "telemetry_total_received": self._telemetry_total,
                                "telemetry_count_definition": "received pose telemetry plus observer-only perception/controller diagnostics during sensor faults",
                                "telemetry_truncated": max(0, self._telemetry_total - self._telemetry_retained),
                                "telemetry_limit": self.max_telemetry, "semantic_retention": "entire_run",
                                "trip_samples": self._trip_samples, "trip_retention": "entire_received_run_on_disk",
                                "trip_prior_missing_samples": self._history_prior_missing,
                                "perception_samples": self._perception_samples,
                                "perception_compressed_bytes": self._perception_compressed_bytes,
                                "perception_uncompressed_bytes": self._perception_uncompressed_bytes,
                                "perception_retention": "entire_received_run_on_disk" if self._perception else "legacy_retained_received_telemetry_only",
                                "perception_image_kind": "schematic_reconstruction_from_saved_sensor_observations",
                                "camera_recording": False,
                                "commit_interval_s": 0.5, "commit_batch_size": 100}

    def cars(self, search="", limit=100, before="") -> dict:
        """An indexed catalogue includes vehicles no longer in the live snapshot."""
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            clauses, values = [], []
            if search:
                escaped = str(search)[:128].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append("car_id LIKE ? ESCAPE '\\'")
                values.append(f"%{escaped}%")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            source = self._catalog_source()
            total = self._connection.execute("SELECT COUNT(*) FROM " + source + where, values).fetchone()[0]
            if before:
                clauses.append("car_id>?")
                values.append(str(before))
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            count = _limit(limit)
            rows = self._connection.execute("SELECT car_id AS id,* FROM " + source + where +
                " ORDER BY car_id LIMIT ?", values + [count + 1]).fetchall()
            cars = [dict(row) for row in rows[:count]]
            return dict(run_id=self.run_id, cars=cars, total=total,
                        next_before=cars[-1]["id"] if len(rows) > count else None)

    def _catalog_source(self):
        if self._compact:
            samples = "SELECT * FROM trip_cars"
            missing = "NOT EXISTS (SELECT 1 FROM trip_cars t WHERE t.car_id=records.car_id)"
        else:
            samples = """SELECT car_id,COUNT(*) sample_count,MIN(sim_time) first_sim_time,MAX(sim_time) last_sim_time,
            MIN(utc) first_utc,MAX(utc) last_utc,
            (SELECT state FROM telemetry t WHERE t.car_id=telemetry.car_id ORDER BY id DESC LIMIT 1) last_state,
            NULL slot_id,NULL last_seq,NULL sequence_gaps,NULL time_gap_count FROM telemetry GROUP BY car_id"""
            missing = "NOT EXISTS (SELECT 1 FROM telemetry t WHERE t.car_id=records.car_id)"
        return f"""({samples} UNION ALL SELECT car_id,0,MIN(sim_time),MAX(sim_time),MIN(utc),MAX(utc),
            'unknown',NULL,NULL,0,0 FROM records WHERE car_id IS NOT NULL AND {missing} GROUP BY car_id)"""

    def _sample_select(self, table):
        if self._perception:
            return f"SELECT t.*,p.payload AS _sensor_payload FROM {table} t LEFT JOIN trip_perception p ON p.id=t.id"
        if self._compact:
            return f"SELECT t.*,r.details AS _legacy_sensor_details FROM {table} t LEFT JOIN telemetry r ON r.id=t.id"
        return f"SELECT t.* FROM {table} t"

    def _trip_item(self, row):
        stored = dict(row)
        compressed = stored.pop("_sensor_payload", None)
        legacy = stored.pop("_legacy_sensor_details", None)
        item = self._item(stored)
        if not self._compact:
            details = item["details"]
            item.update({key: details.get(key) for key in ("heading", "slot_id", "route_progress_m", "distance_m", "seq")})
            item.update(wait_reason=str(details.get("wait_reason", "")), gap_before=0, time_gap_before_s=0)
        sensors = json.loads(zlib.decompress(compressed).decode("utf-8")) if compressed is not None else None
        if not self._perception:
            received = json.loads(legacy) if legacy is not None else item["details"]
            if isinstance(received.get("sensors"), dict):
                sensors = received["sensors"]
        item["sensors"] = sensors
        control = item["details"].get("control")
        item["control"] = control if isinstance(control, dict) else None
        return item

    def _perception_summary(self, car_id, ceiling, sample_count):
        if self._perception:
            stats = self._connection.execute("""SELECT COUNT(*),COALESCE(SUM(sampled=1 AND fresh=0),0),
                COUNT(DISTINCT CASE WHEN sampled=1 THEN sensor_sim_time END)
                FROM trip_perception WHERE car_id=? AND id<=?""", (car_id, ceiling)).fetchone()
            first = self._connection.execute("SELECT sim_time,sensor_sim_time,utc FROM trip_perception WHERE car_id=? AND id<=? ORDER BY id LIMIT 1", (car_id, ceiling)).fetchone()
            last = self._connection.execute("SELECT sim_time,sensor_sim_time,utc FROM trip_perception WHERE car_id=? AND id<=? ORDER BY id DESC LIMIT 1", (car_id, ceiling)).fetchone()
            total = self._connection.execute("SELECT COUNT(*) FROM trip_perception WHERE car_id=?", (car_id,)).fetchone()[0]
            count, stale, distinct = stats
            first, last = dict(first) if first else {}, dict(last) if last else {}
        else:
            # A readonly old run may still contain actual rich sensor payloads.
            # Read these only; dropped old observations remain unavailable.
            count = stale = total = 0
            distinct_times, first, last = set(), {}, {}
            for row in self._connection.execute("SELECT id,utc,sim_time,details FROM telemetry WHERE car_id=? ORDER BY id", (car_id,)):
                sensors = json.loads(row["details"]).get("sensors")
                if not isinstance(sensors, dict):
                    continue
                total += 1
                if row["id"] > ceiling:
                    continue
                count += 1
                sampled = bool(sensors.get("sampled"))
                stale += int(sampled and (not sensors.get("fresh") or bool(sensors.get("fault_active"))))
                sensor_time = _number(sensors.get("sample_sim_time_s"))
                if sampled and sensor_time is not None:
                    distinct_times.add(sensor_time)
                last = dict(sim_time=row["sim_time"], sensor_sim_time=sensor_time, utc=row["utc"])
                if not first:
                    first = dict(last)
            distinct = len(distinct_times)
        return dict(perception_samples=count, total_archived_perception_samples=total,
            perception_coverage=count / sample_count if sample_count else None,
            perception_complete_received_archive=bool(sample_count) and count == sample_count and self._history_prior_missing == 0,
            perception_distinct_samples=distinct, perception_stale_frames=stale,
            perception_first_sim_time=first.get("sim_time"), perception_last_sim_time=last.get("sim_time"),
            perception_first_sensor_sim_time=first.get("sensor_sim_time"), perception_last_sensor_sim_time=last.get("sensor_sim_time"),
            perception_first_utc=first.get("utc"), perception_last_utc=last.get("utc"),
            perception_retention="entire_received_run_on_disk" if self._perception else "legacy_retained_received_telemetry_only",
            perception_count_definition="received telemetry bundles; repeated stale observations are retained",
            perception_image_kind="schematic_reconstruction_from_saved_sensor_observations", camera_recording=False,
            control_kind="received ideal_route_controller diagnostics, separate from sensor observations",
            perception_pose_context="x/y are received reported/reference positions; fault observer poses are labelled position_source=post_step_simulation_reference; sensor pose/sample clock remains independent")

    def trip_history(self, car_id, *, after=0, limit=2000, events_before=None, until_id=None,
                     events_until_id=None) -> dict:
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            car_id = str(car_id)[:128]
            try:
                boundary, count = int(after), max(1, min(2000, int(limit)))
                event_boundary = int(events_before) if events_before is not None else None
                ceiling = int(until_id) if until_id is not None else None
            except (TypeError, ValueError, OverflowError):
                raise ValueError("A történet lapozási azonosítója egész szám legyen") from None
            table = "trip_samples" if self._compact else "telemetry"
            requested_event_ceiling = self._navigation_integer(events_until_id, "felső eseményazonosító")
            event_maximum = self._connection.execute(
                "SELECT COALESCE(MAX(id),0) FROM records WHERE car_id=?", (car_id,)).fetchone()[0]
            event_ceiling = event_maximum if requested_event_ceiling is None else min(event_maximum, requested_event_ceiling)
            summary_row = self._connection.execute("SELECT * FROM " + self._catalog_source() + " WHERE car_id=?", (car_id,)).fetchone()
            summary = dict(summary_row) if summary_row else dict(car_id=car_id, sample_count=0, sequence_gaps=0, time_gap_count=0)
            if ceiling is None:
                ceiling = self._connection.execute(f"SELECT COALESCE(MAX(id),0) FROM {table} WHERE car_id=?", (car_id,)).fetchone()[0]
            summary["total_archived_sample_count"] = summary["sample_count"]
            # Freeze both the page range and its summary while a live car keeps
            # producing samples between successive HTTP requests.
            if ceiling < self._connection.execute(f"SELECT COALESCE(MAX(id),0) FROM {table} WHERE car_id=?", (car_id,)).fetchone()[0]:
                summary["sample_count"] = self._connection.execute(f"SELECT COUNT(*) FROM {table} WHERE car_id=? AND id<=?", (car_id, ceiling)).fetchone()[0]
                last = self._connection.execute(self._sample_select(table) + " WHERE t.car_id=? AND t.id<=? ORDER BY t.id DESC LIMIT 1", (car_id, ceiling)).fetchone()
                if last:
                    item = self._trip_item(last)
                    summary.update(last_sim_time=item["sim_time"], last_utc=item["utc"], last_state=item["state"], slot_id=item.get("slot_id"))
                else:
                    summary.update(first_sim_time=None, last_sim_time=None, first_utc=None, last_utc=None, last_state=None, slot_id=None)
                if self._compact:
                    gaps = self._connection.execute("SELECT COALESCE(SUM(gap_before),0),COALESCE(SUM(time_gap_before_s>0),0) FROM trip_samples WHERE car_id=? AND id<=?", (car_id, ceiling)).fetchone()
                    summary.update(sequence_gaps=gaps[0], time_gap_count=gaps[1])
                    sequence = self._connection.execute("SELECT seq FROM trip_samples WHERE car_id=? AND id<=? AND seq IS NOT NULL ORDER BY id DESC LIMIT 1", (car_id, ceiling)).fetchone()
                    summary["last_seq"] = sequence["seq"] if sequence else None
            rows = self._connection.execute(self._sample_select(table) + " WHERE t.car_id=? AND t.id>? AND t.id<=? ORDER BY t.id LIMIT ?",
                (car_id, boundary, ceiling, count + 1)).fetchall()
            samples = [self._trip_item(row) for row in rows[:count]]
            if not self._compact:
                previous = self._connection.execute(f"SELECT * FROM {table} WHERE car_id=? AND id<=? ORDER BY id DESC LIMIT 1", (car_id, boundary)).fetchone()
                previous = self._trip_item(previous) if previous else None
                for item in samples:
                    if previous:
                        if item.get("seq") is not None and previous.get("seq") is not None:
                            item["gap_before"] = max(0, int(item["seq"]) - int(previous["seq"]) - 1)
                        delta = item["sim_time"] - previous["sim_time"] if item["sim_time"] is not None and previous["sim_time"] is not None else 0
                        if delta > (item["details"].get("period_s") or 0.1) * 1.5 + 1e-6:
                            item["time_gap_before_s"] = delta
                    previous = item
            period_row = self._connection.execute(f"SELECT details FROM {table} WHERE car_id=? ORDER BY id LIMIT 1", (car_id,)).fetchone()
            summary.update(complete_received_archive=self._history_prior_missing == 0,
                prior_missing_samples_global=self._history_prior_missing,
                sample_period_s=json.loads(period_row["details"]).get("period_s") if period_row else None,
                units={"sim_time": "s (simulation)", "utc": "UTC (host receipt)", "speed": "m/s", "x": "m", "y": "m"},
                retention="entire_received_run_on_disk", mqtt_lossless=False)
            summary.update(self._perception_summary(car_id, ceiling, summary["sample_count"]))
            events = self.query(limit=500, car_id=car_id, before=event_boundary, until_id=event_ceiling)
            return dict(run_id=self.run_id, car_id=car_id, telemetry=samples, events=events["items"], until_id=ceiling,
                events_until_id=event_ceiling,
                next_after=samples[-1]["id"] if len(rows) > count else None,
                next_events_before=events["next_before"], event_total=events["total"], summary=summary)

    @staticmethod
    def _navigation_integer(value, name, *, default=None, minimum=0):
        """Keep cursor values exact in both SQLite and a JavaScript client."""
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"A {name} egész szám legyen")
        text = str(value)
        if not text or not text.isascii() or not text.isdecimal():
            raise ValueError(f"A {name} nemnegatív egész szám legyen")
        result = int(text)
        if result < minimum or result > 9_007_199_254_740_991:
            raise ValueError(f"A {name} a megengedett tartományon kívül esik")
        return result

    def trip_history_page(self, car_id, *, direction="start", cursor=None, sim_time=None,
                          limit=2000, until_id=None, events_until_id=None):
        """Return a bounded received-record window and an exact saved time target.

        Windows and ordinals use ascending receipt IDs, including diagnostics
        during sensor faults. Time seeks select the greatest saved simulation
        timestamp at/before the request, breaking ties by the latest receipt ID.
        Only returned sensor payloads are decompressed. Readonly legacy archives
        are queried without creating indices or changing their contents.
        """
        direction = str(direction)
        if direction not in {"start", "end", "after", "before", "time"}:
            raise ValueError("Ismeretlen útnapló-lapozási irány")
        count = self._navigation_integer(limit, "mintakeret", minimum=1)
        if count is None:
            raise ValueError("A mintakeret pozitív egész szám legyen")
        count = min(2000, count)
        boundary = self._navigation_integer(cursor, "lapozási azonosító")
        requested_ceiling = self._navigation_integer(until_id, "felső mintaazonosító")
        requested_event_ceiling = self._navigation_integer(events_until_id, "felső eseményazonosító")
        if direction in {"after", "before"}:
            if boundary is None or (direction == "before" and boundary == 0):
                raise ValueError("A lapozáshoz mintaazonosító szükséges")
        elif boundary is not None:
            raise ValueError("Ehhez a keresési irányhoz nem adható lapozási azonosító")
        requested_time = None
        if direction == "time":
            if isinstance(sim_time, bool):
                raise ValueError("A keresett szimulációs idő nemnegatív, véges szám legyen")
            requested_time = _number(sim_time)
            if requested_time is None or requested_time < 0:
                raise ValueError("A keresett szimulációs idő nemnegatív, véges szám legyen")
        elif sim_time is not None:
            raise ValueError("Szimulációs idő csak idő szerinti kereséshez adható meg")

        with self._lock:
            self._ensure_open()
            self._flush_locked()
            car_id = str(car_id)[:128]
            table = "trip_samples" if self._compact else "telemetry"
            maximum = self._connection.execute(
                f"SELECT COALESCE(MAX(id),0) FROM {table} WHERE car_id=?", (car_id,)).fetchone()[0]
            ceiling = maximum if requested_ceiling is None else min(maximum, requested_ceiling)
            if boundary:
                known = self._connection.execute(
                    f"SELECT 1 FROM {table} WHERE car_id=? AND id=? AND id<=?",
                    (car_id, boundary, ceiling)).fetchone()
                if not known:
                    raise ValueError("A lapozási azonosító nem ehhez a járműhöz vagy rögzített tartományhoz tartozik")
            # The old API supplies backward-compatible archive completeness and
            # sensor statistics; ask beyond the fixed prefix so it reads no rich
            # sample page before the actual bounded selection below.
            result = self.trip_history(car_id, after=ceiling, limit=1, until_id=ceiling,
                                       events_until_id=requested_event_ceiling)
            where, values, descending = "t.car_id=? AND t.id<=?", [car_id, ceiling], False
            seek = None
            target_id = None
            if direction in {"after", "before"}:
                where += " AND t.id" + (">?" if direction == "after" else "<?")
                values.append(boundary)
                descending = direction == "before"
            elif direction == "end":
                descending = True
            elif direction == "time":
                target = self._connection.execute(
                    f"SELECT id,sim_time FROM {table} WHERE car_id=? AND id<=? AND sim_time<=? "
                    "ORDER BY sim_time DESC,id DESC LIMIT 1", (car_id, ceiling, requested_time)).fetchone()
                resolution = "predecessor"
                if target is None:
                    target = self._connection.execute(
                        f"SELECT id,sim_time FROM {table} WHERE car_id=? AND id<=? AND sim_time IS NOT NULL "
                        "ORDER BY sim_time,id LIMIT 1", (car_id, ceiling)).fetchone()
                    resolution = "earliest_fallback" if target else "no_timed_sample"
                if target:
                    target_id = target["id"]
                    where += " AND t.id>=?"
                    values.append(target_id)
                seek = dict(requested_sim_time=requested_time, resolved_sim_time=target["sim_time"] if target else None,
                            resolution=resolution, predecessor_found=resolution == "predecessor")
            rows = self._connection.execute(self._sample_select(table) + " WHERE " + where +
                " ORDER BY t.id " + ("DESC" if descending else "ASC") + " LIMIT ?", values + [count]).fetchall()
            if descending:
                rows = list(reversed(rows))
            samples = [self._trip_item(row) for row in rows]
            # Legacy telemetry has no stored gap columns. Recover a page's first
            # gap from its actual preceding received row, never across a fake
            # page boundary. This is the same rule used by full-history export.
            if not self._compact and samples:
                previous = self._connection.execute(self._sample_select(table) +
                    " WHERE t.car_id=? AND t.id<? ORDER BY t.id DESC LIMIT 1", (car_id, samples[0]["id"])).fetchone()
                previous = self._trip_item(previous) if previous else None
                for item in samples:
                    if previous:
                        if item.get("seq") is not None and previous.get("seq") is not None:
                            item["gap_before"] = max(0, int(item["seq"]) - int(previous["seq"]) - 1)
                        delta = item["sim_time"] - previous["sim_time"] if item["sim_time"] is not None and previous["sim_time"] is not None else 0
                        if delta > (item["details"].get("period_s") or .1) * 1.5 + 1e-6:
                            item["time_gap_before_s"] = delta
                    previous = item
            total = self._connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE car_id=? AND id<=?", (car_id, ceiling)).fetchone()[0]
            first_id, last_id = (samples[0]["id"], samples[-1]["id"]) if samples else (None, None)
            prior = self._connection.execute(f"SELECT COUNT(*) FROM {table} WHERE car_id=? AND id<? AND id<=?",
                (car_id, first_id, ceiling)).fetchone()[0] if samples else 0
            previous_before = first_id if prior else None
            next_after = last_id if samples and prior + len(samples) < total else None
            if direction != "time" and samples:
                target_id = last_id if descending else first_id
            result.update(telemetry=samples, next_after=next_after, target_id=target_id, seek=seek,
                window=dict(start_ordinal=prior + 1 if samples else 0, end_ordinal=prior + len(samples),
                    total_count=total, page_limit=count, first_id=first_id, last_id=last_id,
                    previous_before_id=previous_before, next_after_id=next_after,
                    order="receipt_id_ascending"))
            return result

    def trip_overview(self, car_id, *, until_id=None, limit=4000):
        """Bounded outline from exact stored scalar positions, without sensors.

        The entire frozen prefix is scanned in receipt order. Skipped vertices
        can omit bends, but any skipped discontinuity breaks the next displayed
        line. An outline is navigation context, never reconstructed telemetry.
        """
        count = self._navigation_integer(limit, "áttekintő pontkeret", minimum=2)
        if count is None:
            raise ValueError("Az áttekintő pontkeret legalább kettő legyen")
        count = min(4000, count)
        requested_ceiling = self._navigation_integer(until_id, "felső mintaazonosító")
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            car_id = str(car_id)[:128]
            table = "trip_samples" if self._compact else "telemetry"
            maximum = self._connection.execute(
                f"SELECT COALESCE(MAX(id),0) FROM {table} WHERE car_id=?", (car_id,)).fetchone()[0]
            ceiling = maximum if requested_ceiling is None else min(maximum, requested_ceiling)
            total = self._connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE car_id=? AND id<=?", (car_id, ceiling)).fetchone()[0]
            stride = 1 if total <= count else (total if count == 2 else math.ceil(total / (count - 2)))
            if self._compact:
                select = ("SELECT id,utc,sim_time,x,y,heading,speed,state,slot_id,seq,gap_before,"
                          "time_gap_before_s FROM trip_samples")
            else:
                # SQLite extracts only scalar legacy fields. The Python reader
                # neither decodes rich details nor synthesises missing sensors.
                select = ("SELECT id,utc,sim_time,x,y,speed,state,"
                          "json_extract(details,'$.heading') heading,"
                          "json_extract(details,'$.slot_id') slot_id,"
                          "json_extract(details,'$.seq') seq,"
                          "json_extract(details,'$.period_s') period_s FROM telemetry")
            rows = self._connection.execute(select + " WHERE car_id=? AND id<=? ORDER BY id",
                                             (car_id, ceiling))
            points, pending_reasons = [], set()
            previous, last_valid, bounds = None, None, None
            valid_count = break_count = 0
            for ordinal, row in enumerate(rows, 1):
                point = dict(row)
                if not self._compact:
                    seq, previous_seq = _number(point.get("seq")), _number(previous.get("seq")) if previous else None
                    point["gap_before"] = max(0, int(seq) - int(previous_seq) - 1) if seq is not None and previous_seq is not None else 0
                    delta = (point["sim_time"] - previous["sim_time"]) if previous and point["sim_time"] is not None and previous["sim_time"] is not None else 0
                    period = _number(point.pop("period_s", None)) or .1
                    point["time_gap_before_s"] = delta if delta > period * 1.5 + 1e-6 else 0
                reasons = set()
                seq = _number(point.get("seq"))
                prior_seq = _number(previous.get("seq")) if previous else None
                if previous and seq is not None and prior_seq is not None and seq <= prior_seq:
                    reasons.add("sequence_regression" if seq < prior_seq else "sequence_duplicate")
                if point.get("gap_before", 0) > 0:
                    reasons.add("sequence_gap")
                if point.get("time_gap_before_s", 0) > 0:
                    reasons.add("time_gap")
                timestamp = _number(point.get("sim_time"))
                prior_time = _number(previous.get("sim_time")) if previous else None
                if previous and (timestamp is None or prior_time is None):
                    reasons.add("missing_time")
                elif previous and timestamp < prior_time:
                    reasons.add("time_regression")
                x, y = _number(point.get("x")), _number(point.get("y"))
                if x is None or y is None:
                    reasons.add("invalid_position")
                if reasons:
                    break_count += 1
                    pending_reasons.update(reasons)
                previous = dict(point)
                if x is None or y is None:
                    continue
                valid_count += 1
                bounds = dict(min_x=x, min_y=y, max_x=x, max_y=y) if bounds is None else dict(
                    min_x=min(bounds["min_x"], x), min_y=min(bounds["min_y"], y),
                    max_x=max(bounds["max_x"], x), max_y=max(bounds["max_y"], y))
                point.update(x=x, y=y, sim_time=timestamp, heading=_number(point.get("heading")),
                             speed=_number(point.get("speed")),
                             break_before=bool(points and pending_reasons),
                             break_reasons=sorted(pending_reasons))
                last_valid = point
                if not points or (count > 2 and ordinal % stride == 0):
                    points.append(point)
                    pending_reasons.clear()
            if last_valid is not None and (not points or points[-1]["id"] != last_valid["id"]):
                points.append(last_valid)
            return dict(schema_version=1, run_id=self.run_id, car_id=car_id,
                source="received_compact_positions" if self._compact else "legacy_retained_received_positions",
                scope="frozen_received_trip_prefix", order="receipt_id_ascending", until_id=ceiling,
                total_count=total, valid_position_count=valid_count, point_count=len(points),
                invalid_position_count=total - valid_count,
                stride=stride, max_points=count, downsampled=len(points) < valid_count,
                detected_break_count=break_count, bounds=bounds, points=points,
                break_count_definition="received rows with a discontinuity; not displayed segment count",
                complete_received_archive=self._history_prior_missing == 0,
                prior_missing_samples_global=self._history_prior_missing,
                approximation="exact received vertices; intermediate bends may be omitted; gaps are not joined",
                position_context="received reported/reference positions; observer fault rows may be post-step simulation references",
                units={"sim_time": "s (simulation)", "utc": "UTC (host receipt)", "x": "m", "y": "m"})

    def trip_mission(self, car_id, *, until_id=None, events_until_id=None):
        """Summarise a frozen received prefix from scalar evidence only.

        Durations hold the left sample's reported state over a contiguous,
        positive model-clock interval. They are estimates, never exact event
        durations or causal explanations. SQL groups arbitrary labels and
        returns only the largest twenty; Python does not retain a full trip,
        a growing label dictionary, or compressed sensor payloads.
        """
        requested_ceiling = self._navigation_integer(until_id, "felső mintaazonosító")
        requested_events = self._navigation_integer(events_until_id, "felső eseményazonosító")
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            car_id = str(car_id)[:128]
            table = "trip_samples" if self._compact else "telemetry"
            maximum = self._connection.execute(
                f"SELECT COALESCE(MAX(id),0) FROM {table} WHERE car_id=?", (car_id,)).fetchone()[0]
            ceiling = maximum if requested_ceiling is None else min(maximum, requested_ceiling)
            event_max = self._connection.execute(
                "SELECT COALESCE(MAX(id),0) FROM records WHERE car_id=?", (car_id,)).fetchone()[0]
            event_ceiling = event_max if requested_events is None else min(event_max, requested_events)
            event_count = self._connection.execute(
                "SELECT COUNT(*) FROM records WHERE car_id=? AND id<=?", (car_id, event_ceiling)).fetchone()[0]
            safe_details = "CASE WHEN json_valid(details) THEN details ELSE '{}' END"
            def scalar(key):
                return f"json_extract({safe_details},'$.{key}')"
            common = "id,utc,sim_time,x,y,speed,state"
            if self._compact:
                source = (f"SELECT {common},heading,slot_id,wait_reason,distance_m,seq,gap_before,time_gap_before_s,"
                          f"{scalar('period_s')} period_s FROM trip_samples WHERE car_id=? AND id<=?")
            else:
                source = (f"SELECT {common},{scalar('heading')} heading,{scalar('slot_id')} slot_id,"
                          f"COALESCE({scalar('wait_reason')},'') wait_reason,{scalar('distance_m')} distance_m,"
                          f"{scalar('seq')} seq,0 gap_before,0 time_gap_before_s,{scalar('period_s')} period_s "
                          "FROM telemetry WHERE car_id=? AND id<=?")
            parameters = (car_id, ceiling)
            # No rich details or sensor blobs are selected. Each Python row is
            # released before the next arrives. SQLite may use temporary disk
            # storage for exact GROUP BY / DISTINCT operations on long runs.
            rows = self._connection.execute(source + " ORDER BY id", parameters)
            first = last = previous = None
            sample_count = interval_count = valid_intervals = excluded = 0
            valid_duration = unknown_duration = 0.0
            exclusions = {}
            valid_counters = missing_counters = negative_counters = counter_regressions = 0
            prior_counter = None
            def nonnegative(value):
                if not isinstance(value, (int, float)):
                    return None
                value = _number(value)
                return value if value is not None and value >= 0 else None
            def sequence(value):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return None
                value = nonnegative(value)
                return int(value) if value is not None and value <= 9_007_199_254_740_991 and value == int(value) else None
            def endpoint(row):
                return {key: row.get(key) for key in ("id", "utc", "sim_time", "x", "y", "speed", "heading",
                    "state", "slot_id", "wait_reason", "distance_m", "seq")}
            for stored in rows:
                row = dict(stored)
                sample_count += 1
                if first is None:
                    first = endpoint(row)
                last = endpoint(row)
                counter = nonnegative(row.get("distance_m"))
                if counter is None:
                    missing_counters += 1
                    negative_counters += int((_number(row.get("distance_m")) or 0) < 0)
                else:
                    valid_counters += 1
                    if prior_counter is not None and counter < prior_counter:
                        counter_regressions += 1
                    prior_counter = counter
                if previous is not None:
                    interval_count += 1
                    reasons = set()
                    timestamp, prior_time = nonnegative(row["sim_time"]), nonnegative(previous["sim_time"])
                    delta = timestamp - prior_time if timestamp is not None and prior_time is not None else None
                    if delta is None:
                        reasons.add("missing_or_invalid_clock")
                    elif delta < 0:
                        reasons.add("clock_regression")
                    elif delta == 0:
                        reasons.add("zero_clock_interval")
                    seq, prior_seq = sequence(row["seq"]), sequence(previous["seq"])
                    if seq is None or prior_seq is None:
                        reasons.add("missing_or_invalid_sequence")
                    elif seq < prior_seq:
                        reasons.add("sequence_regression")
                    elif seq == prior_seq:
                        reasons.add("sequence_duplicate")
                    elif seq != prior_seq + 1:
                        reasons.add("sequence_gap")
                    if (_number(row.get("gap_before")) or 0) > 0:
                        reasons.add("sequence_gap")
                    period = nonnegative(row.get("period_s")) if isinstance(row.get("period_s"), (int, float)) else None
                    period = period or .1
                    if (_number(row.get("time_gap_before_s")) or 0) > 0 or (delta is not None and delta > period * 1.5 + 1e-6):
                        reasons.add("time_gap")
                    if reasons:
                        excluded += 1
                        for reason in reasons:
                            exclusions[reason] = exclusions.get(reason, 0) + 1
                        if delta is not None and delta > 0:
                            unknown_duration += delta
                    else:
                        valid_intervals += 1
                        valid_duration += delta
                previous = row

            # This uses the same conservative interval rule as the streaming
            # totals. Arbitrary labels are aggregated in SQLite, not Python.
            finite = "BETWEEN 0 AND 1.7976931348623157e308"
            cte = f"""WITH source AS ({source}), edges AS (
                SELECT *,LEAD(id) OVER w next_id,LEAD(sim_time) OVER w next_time,
                    LEAD(seq) OVER w next_seq,LEAD(gap_before) OVER w next_gap,
                    LEAD(time_gap_before_s) OVER w next_time_gap,LEAD(period_s) OVER w next_period
                FROM source WINDOW w AS (ORDER BY id)), scored AS (
                SELECT *,CASE WHEN next_id IS NOT NULL
                    AND typeof(sim_time) IN ('integer','real') AND sim_time {finite}
                    AND typeof(next_time) IN ('integer','real') AND next_time {finite} AND next_time>sim_time
                    AND typeof(seq) IN ('integer','real') AND seq BETWEEN 0 AND 9007199254740991 AND seq=CAST(seq AS INTEGER)
                    AND typeof(next_seq) IN ('integer','real') AND next_seq BETWEEN 0 AND 9007199254740991 AND next_seq=seq+1
                    AND COALESCE(next_gap,0)<=0 AND COALESCE(next_time_gap,0)<=0
                    AND next_time-sim_time <= (CASE WHEN typeof(next_period) IN ('integer','real')
                        AND next_period>0 AND next_period<=1.7976931348623157e308 THEN next_period ELSE .1 END)*1.5+1e-6
                    THEN next_time-sim_time ELSE 0 END estimated_s FROM edges) """
            def groups(column):
                grouped = self._connection.execute(cte + f"SELECT {column} label,COUNT(*) sample_count,"
                    f"SUM(estimated_s) estimated_s FROM scored GROUP BY {column} "
                    "ORDER BY estimated_s DESC,sample_count DESC,label ASC LIMIT 20", parameters).fetchall()
                result = [{column if column == "state" else "reason": item["label"],
                    "sample_count": item["sample_count"], "estimated_s": item["estimated_s"]} for item in grouped]
                return result, dict(sample_count=sample_count - sum(item["sample_count"] for item in result),
                    estimated_s=max(0.0, valid_duration - sum(item["estimated_s"] for item in result)))
            states, other_states = groups("state")
            waits, other_waits = groups("wait_reason")
            first_time = nonnegative(first["sim_time"]) if first else None
            last_time = nonnegative(last["sim_time"]) if last else None
            span = last_time - first_time if first_time is not None and last_time is not None and last_time >= first_time else None
            first_counter = nonnegative(first["distance_m"]) if first else None
            last_counter = nonnegative(last["distance_m"]) if last else None
            distance_ok = bool(sample_count and missing_counters == 0 and counter_regressions == 0)
            distance_reason = ("no_received_samples" if not sample_count else "missing_or_invalid_counter" if missing_counters
                else "counter_regression" if counter_regressions else "received_monotonic_counter_endpoints")
            perception = dict(available=self._perception, received_frames=0, fresh_frames=0, stale_frames=0,
                unsampled_frames=0, missing_frames=sample_count if self._perception else None,
                unique_sample_clocks=None, max_age_s=None,
                age_clock="telemetry_sim_time_minus_saved_sensor_sim_time", invalid_age_frames=0,
                complete_received_archive=False, retention="scalar_perception_index" if self._perception else "legacy_scalar_index_unavailable",
                freshness_definition="saved sampled=1 and fresh=1; fault-active bundles were indexed stale",
                coverage_definition="missing frames are received position rows without a scalar sensor index; legacy missing count is unknown, not inferred from retained rich telemetry",
                unique_definition="distinct valid nonnegative saved sensor sample clocks where sampled=1; repeated cached bundles count once")
            if self._perception:
                condition = ("p.sampled=1 AND typeof(p.sim_time) IN ('integer','real') "
                    "AND typeof(p.sensor_sim_time) IN ('integer','real') "
                    "AND p.sim_time BETWEEN 0 AND 1.7976931348623157e308 "
                    "AND p.sensor_sim_time BETWEEN 0 AND p.sim_time")
                stats = self._connection.execute(f"""SELECT COUNT(*) received,
                    COALESCE(SUM(p.sampled=1 AND p.fresh=1),0) fresh,
                    COALESCE(SUM(p.sampled=1 AND p.fresh=0),0) stale,
                    COALESCE(SUM(p.sampled=0),0) unsampled,
                    COUNT(DISTINCT CASE WHEN p.sampled=1 AND typeof(p.sensor_sim_time) IN ('integer','real')
                        AND p.sensor_sim_time BETWEEN 0 AND 1.7976931348623157e308 THEN p.sensor_sim_time END) unique_clocks,
                    MAX(CASE WHEN {condition} THEN p.sim_time-p.sensor_sim_time END) max_age,
                    COALESCE(SUM(CASE WHEN p.sampled=1 AND NOT COALESCE(({condition}),0) THEN 1 ELSE 0 END),0) invalid_age
                    FROM trip_perception p JOIN {table} t ON p.id=t.id AND p.car_id=t.car_id
                    WHERE t.car_id=? AND t.id<=?""", parameters).fetchone()
                perception.update(received_frames=stats["received"], fresh_frames=stats["fresh"], stale_frames=stats["stale"],
                    unsampled_frames=stats["unsampled"], missing_frames=max(0, sample_count-stats["received"]),
                    unique_sample_clocks=stats["unique_clocks"], max_age_s=stats["max_age"], invalid_age_frames=stats["invalid_age"],
                    complete_received_archive=bool(sample_count) and stats["received"] == sample_count and self._history_prior_missing == 0)
            return _json_safe(dict(schema_version=1, run_id=self.run_id, car_id=car_id,
                scope="frozen_received_trip_prefix", until_id=ceiling, events_until_id=event_ceiling,
                receipt=dict(sample_count=sample_count, first=first, last=last,
                    complete_received_archive=self._history_prior_missing == 0,
                    prior_missing_samples_global=self._history_prior_missing,
                    retention="entire_received_run_on_disk" if self._compact else "legacy_retained_received_telemetry_only"),
                timing=dict(clock="simulation", first_sim_time=first_time, last_sim_time=last_time, span_s=span,
                    span_definition="last valid endpoint clock minus first; not the sum of known state durations",
                    valid_estimated_duration_s=valid_duration, unknown_positive_duration_s=unknown_duration,
                    interval_count=interval_count, valid_interval_count=valid_intervals, excluded_interval_count=excluded,
                    exclusions=exclusions, exclusion_definition="intervals may have several exclusion reasons",
                    estimation="sample_and_hold_left_endpoint_on_contiguous_receipts"),
                states=states, wait_reasons=waits,
                aggregation=dict(state_limit=20, wait_reason_limit=20, states_other=other_states,
                    wait_reasons_other=other_waits, selection="largest_estimated_duration_then_received_count"),
                distance=dict(source="received_model_odometry_counter", available=distance_ok,
                    value_m=last_counter-first_counter if distance_ok else None,
                    first_counter_m=first_counter, last_counter_m=last_counter, valid_counter_count=valid_counters,
                    missing_counter_count=missing_counters, negative_counter_count=negative_counters,
                    regression_count=counter_regressions, reason=distance_reason),
                perception=perception, events=dict(received_count=event_count),
                proximity_minimum=dict(available=False, reason="not_retained_in_scalar_index"),
                inference_note="State and wait-reason durations are sample-and-hold estimates on positive contiguous received intervals; excluded intervals and the final sample receive no duration. Reported reasons do not prove causality. Odometry is a received model counter, not independent physical measurement. No camera images or missing sensor observations are reconstructed."))

    def iter_trip_export(self, car_id, format="csv", *, context=None):
        """Stream a fixed stored prefix in small batches, independent of UI paging."""
        if format not in {"csv", "json"}:
            raise ValueError("Az útnapló formátuma csv vagy json lehet")
        car_id = str(car_id)[:128]
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            sample_table = "trip_samples" if self._compact else "telemetry"
            sample_max = self._connection.execute(f"SELECT COALESCE(MAX(id),0) FROM {sample_table} WHERE car_id=?", (car_id,)).fetchone()[0]
            event_max = self._connection.execute("SELECT COALESCE(MAX(id),0) FROM records WHERE car_id=?", (car_id,)).fetchone()[0]
            summary = self.trip_history(car_id, limit=1, until_id=sample_max)["summary"]
            summary.update(recorded_until_id=sample_max, events_until_id=event_max)
        fields = ("run_id", "car_id", "record_type", "id", "utc", "sim_time", "x", "y", "speed", "heading", "state",
                  "slot_id", "wait_reason", "route_progress_m", "distance_m", "seq", "gap_before", "time_gap_before_s", "event_type", "message", "sensors", "control", "details")
        if format == "json":
            header = dict(run_id=self.run_id, car_id=car_id, summary=summary)
            if context:
                header.update(context)
            yield (_encode(header)[:-1] + ',"telemetry":[').encode("utf-8")
        else:
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            yield output.getvalue().encode("utf-8-sig")
        for table, ceiling, kind in ((sample_table, sample_max, "telemetry"), ("records", event_max, "event")):
            boundary, first = 0, True
            previous_sample = None
            if format == "json" and kind == "event":
                yield b'],"events":['
            while boundary < ceiling:
                with self._lock:
                    select = self._sample_select(table) if kind == "telemetry" else f"SELECT t.* FROM {table} t"
                    rows = self._connection.execute(select + " WHERE t.car_id=? AND t.id>? AND t.id<=? ORDER BY t.id LIMIT 500",
                                                    (car_id, boundary, ceiling)).fetchall()
                if not rows:
                    break
                for row in rows:
                    item = self._trip_item(row) if kind == "telemetry" else self._item(row)
                    if kind == "telemetry" and not self._compact:
                        if previous_sample:
                            if item.get("seq") is not None and previous_sample.get("seq") is not None:
                                item["gap_before"] = max(0, int(item["seq"]) - int(previous_sample["seq"]) - 1)
                            delta = item["sim_time"] - previous_sample["sim_time"] if item["sim_time"] is not None and previous_sample["sim_time"] is not None else 0
                            if delta > (item["details"].get("period_s") or 0.1) * 1.5 + 1e-6:
                                item["time_gap_before_s"] = delta
                        previous_sample = dict(item)
                    if format == "json":
                        yield (("" if first else ",") + _encode(item)).encode("utf-8")
                    else:
                        output = io.StringIO(newline="")
                        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
                        if kind == "event":
                            item.update({key: item["details"].get(key, "") for key in ("x", "y", "speed", "heading", "state", "slot_id", "wait_reason")})
                        item["details"] = _encode(item["details"])
                        item["sensors"] = _encode(item.get("sensors"))
                        item["control"] = _encode(item.get("control"))
                        writer.writerow(dict(run_id=self.run_id, record_type=kind, **item))
                        yield output.getvalue().encode("utf-8")
                    first = False
                boundary = rows[-1]["id"]
        if format == "json":
            yield b"]}"

    def iter_records_export(self, format="jsonl"):
        """Yield bounded batches from a fixed semantic journal prefix."""
        if format not in ("jsonl", "csv"):
            raise ValueError("A naplóexport formátuma jsonl vagy csv lehet")
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            ceiling = self._connection.execute("SELECT COALESCE(MAX(id),0) FROM records").fetchone()[0]
        fields = ("run_id", "id", "utc", "sim_time", "level", "category", "event_type", "message", "car_id", "details")
        if format == "csv":
            output = io.StringIO(newline="")
            csv.DictWriter(output, fieldnames=fields).writeheader()
            yield output.getvalue().encode("utf-8-sig")
        boundary = 0
        while boundary < ceiling:
            with self._lock:
                self._ensure_open()
                rows = self._connection.execute("SELECT * FROM records WHERE id>? AND id<=? ORDER BY id LIMIT 100",
                                                (boundary, ceiling)).fetchall()
            if not rows:
                break
            for row in rows:
                if format == "csv":
                    output = io.StringIO(newline="")
                    csv.DictWriter(output, fieldnames=fields).writerow({"run_id": self.run_id, **dict(row)})
                    yield output.getvalue().encode("utf-8")
                else:
                    yield (_encode({"run_id": self.run_id, **self._item(row)}) + "\n").encode("utf-8")
            boundary = rows[-1]["id"]

    def export(self, format="jsonl") -> bytes:
        """Compatibility API; HTTP/ZIP exports consume the bounded iterator."""
        return b"".join(self.iter_records_export(format))

    def iter_telemetry_export(self):
        """Stream retained received measurements, with explicit clocks and units.

        ``sim_time`` is seconds, ``speed`` m/s, and ``x``/``y`` metres. The
        complete MQTT/source/uncertainty fields remain in the details column.
        Retention completeness is disclosed by stats, which the ZIP includes.
        """
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            ceiling = self._connection.execute("SELECT COALESCE(MAX(id),0) FROM telemetry").fetchone()[0]
        fields = ("run_id", "id", "utc", "sim_time", "car_id", "seq", "speed", "x", "y", "state",
                  "source", "message_id", "producer_seq", "topic", "latency_ms", "details")
        output = io.StringIO(newline="")
        csv.DictWriter(output, fieldnames=fields).writeheader()
        yield output.getvalue().encode("utf-8-sig")
        boundary = 0
        while boundary < ceiling:
            with self._lock:
                self._ensure_open()
                rows = self._connection.execute("SELECT * FROM telemetry WHERE id>? AND id<=? ORDER BY id LIMIT 100",
                                                (boundary, ceiling)).fetchall()
            if not rows:
                break
            for row in rows:
                item = dict(row)
                details = json.loads(item["details"])
                transport = details.get("mqtt", {})
                output = io.StringIO(newline="")
                writer = csv.DictWriter(output, fieldnames=fields)
                writer.writerow({"run_id": self.run_id, **item,
                    **{key: details.get(key, "") for key in ("seq", "source", "message_id", "producer_seq")},
                    "topic": transport.get("topic", ""), "latency_ms": transport.get("latency_ms", "")})
                yield output.getvalue().encode("utf-8")
            boundary = rows[-1]["id"]

    def export_telemetry(self) -> bytes:
        return b"".join(self.iter_telemetry_export())

    def export_boundaries(self) -> dict:
        """Report the receipt prefix used by an already pinned export reader."""
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            table = "trip_samples" if self._compact else "telemetry"
            return dict(
                records_until_id=self._connection.execute("SELECT COALESCE(MAX(id),0) FROM records").fetchone()[0],
                telemetry_until_id=self._connection.execute("SELECT COALESCE(MAX(id),0) FROM telemetry").fetchone()[0],
                trips_until_id=self._connection.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0],
                records=self._records, retained_telemetry_samples=self._telemetry_retained,
                complete_trip_samples=self._trip_samples, perception_samples=self._perception_samples,
                complete_trip_member="complete_trips.jsonl",
                transport_schema_version=1, transport_available=self._transport,
                transport_until_id=self._connection.execute("SELECT COALESCE(MAX(id),0) FROM transport_samples").fetchone()[0] if self._transport else 0,
                transport_members=["transport.csv", "transport.json"],
                operations_schema_version=1, operations_available=self._operations,
                operations_until_id=self._connection.execute("SELECT COALESCE(MAX(id),0) FROM operations_incidents").fetchone()[0] if self._operations else 0,
                operations_member="incidents.json",
                consistency="fixed received prefix in an independent SQLite WAL read transaction")

    def iter_complete_trip_export(self):
        """Yield every received archived pose and exact saved observation bundle."""
        with self._lock:
            self._ensure_open()
            self._flush_locked()
            table = "trip_samples" if self._compact else "telemetry"
            ceiling = self._connection.execute(f"SELECT COALESCE(MAX(id),0) FROM {table}").fetchone()[0]
        boundary = 0
        while boundary < ceiling:
            with self._lock:
                self._ensure_open()
                rows = self._connection.execute(self._sample_select(table) +
                    " WHERE t.id>? AND t.id<=? ORDER BY t.id LIMIT 100", (boundary, ceiling)).fetchall()
            if not rows:
                break
            for row in rows:
                yield (_encode(dict(run_id=self.run_id, **self._trip_item(row))) + "\n").encode("utf-8")
            boundary = rows[-1]["id"]

    def flush(self) -> None:
        with self._lock:
            self._ensure_open()
            self._flush_locked()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._flush_locked()
                if not self._readonly:
                    self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._connection.close()
                self._closed = True
