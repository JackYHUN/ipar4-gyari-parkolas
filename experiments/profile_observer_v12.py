"""Paired memory-only replay of saved MQTT receipts through observer ingestion.

This isolates Python encoding/compression and in-memory SQLite work. It is not
a live MQTT throughput test and excludes filesystem I/O, WAL/fsync, callback
decoding, browser polling and scheduler contention. The immutable pre-change
source is hashed before loading; both paths receive identical saved packets.
"""
import argparse
import cProfile
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import pstats
import statistics
import time
from unittest.mock import patch

from factory_yard import __version__
from factory_yard import journal as current
from factory_yard.server import Application, ROOT

BASELINE_SHA256 = "236ec40483049210d94cf8243d34d41988dc789a931a6eb7fea18638286b75ee"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_baseline(path):
    if sha256(path) != BASELINE_SHA256:
        raise ValueError("The immutable pre-change journal source hash differs")
    spec = importlib.util.spec_from_file_location("journal_v11_profile_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_packets(folder, run_id, count):
    packets, origins, raw_lines = [], [], []
    selected_digest = hashlib.sha256()
    paths = sorted(folder.glob(f"{run_id}-messages*.jsonl"))
    for path in paths:
        with path.open("rb") as source:
            for line_number, raw in enumerate(source, 1):
                message = json.loads(raw)
                if message["payload"].get("run_id") != run_id:
                    continue
                if not message["payload"].get("received_utc"):
                    raise ValueError("Saved callback receipt UTC is required for exact byte parity")
                if not message["topic"].startswith(f"yard/{run_id}/"):
                    raise ValueError("Saved topic must match the replay run")
                packets.append(message)
                origins.append({"file": path.name, "line": line_number})
                raw_lines.append(raw)
                selected_digest.update(raw)
                if len(packets) == count:
                    return packets, origins, selected_digest.hexdigest(), raw_lines
    raise ValueError(f"Only {len(packets)} saved packets available, {count} required")


def read_fixture(path, run_id, count):
    packets, origins, raw_lines = [], [], []
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as source:
        for line_number, raw in enumerate(source, 1):
            message = json.loads(raw)
            if message["payload"].get("run_id") != run_id or not message["payload"].get("received_utc"):
                raise ValueError("Portable fixture must preserve the selected run and original receipt UTC")
            if not message["topic"].startswith(f"yard/{run_id}/"):
                raise ValueError("Portable fixture topic does not match its run")
            packets.append(message)
            origins.append(dict(file=path.name, line=line_number))
            raw_lines.append(raw)
            digest.update(raw)
            if len(packets) == count:
                return packets, origins, digest.hexdigest(), raw_lines
    raise ValueError(f"Portable fixture has only {len(packets)} rows, {count} required")


class RawSink(io.StringIO):
    def __init__(self, digest=None):
        super().__init__()
        self.digest = digest

    def write(self, text):
        if self.digest is not None:
            self.digest.update(text.encode("utf-8"))
        return super().write(text)


def replay(module, packets, config, *, trace=False, parity=False):
    app = Application(config, ROOT / "config/default.json")
    app.run_id = packets[0]["payload"]["run_id"]
    app.run_dir = Path(".")  # Every Path.open is intercepted during replay.
    app.journal = module.Journal(Path(":memory:"), app.run_id)
    raw_digest = hashlib.sha256() if parity else None
    profile = cProfile.Profile() if trace else None
    try:
        # The bounded input fits a single raw segment. Do not let a profile
        # accidentally call rotation/unlink against a filesystem path.
        app.detail_limit_bytes = app.raw_segment_bytes = 2 ** 40
        with patch.object(Path, "open", side_effect=lambda *args, **kwargs: RawSink(raw_digest)), \
             patch.object(Path, "unlink", side_effect=AssertionError("profile filesystem mutation")):
            if profile:
                profile.enable()
            started = time.perf_counter()
            for message in packets:
                topic = message["topic"].split("/", 2)[2]
                app._accept(topic, message["payload"], message["received_ns"])
            app.journal.flush()
            elapsed = time.perf_counter() - started
            if profile:
                profile.disable()
        evidence = None
        if parity:
            tables = {}
            for name in ("telemetry", "trip_samples", "trip_perception", "trip_cars", "records", "metadata"):
                order = "car_id" if name == "trip_cars" else "key" if name == "metadata" else "id"
                tables[name] = [tuple(row) for row in app.journal._connection.execute(f"SELECT * FROM {name} ORDER BY {order}")]
            evidence = dict(tables=tables, raw_sha256=raw_digest.hexdigest(),
                received=app.received, stats=app.journal.stats())
        top = []
        if profile:
            stats = pstats.Stats(profile)
            for (filename, line, function), (primitive, calls, self_s, cumulative_s, _) in sorted(
                    stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:20]:
                top.append(dict(file=Path(filename).name, line=line, function=function,
                    primitive_calls=primitive, calls=calls, self_s=self_s, cumulative_s=cumulative_s))
        return elapsed, evidence, top
    finally:
        app.journal.close()


def evidence_digest(rows):
    # Include compressed sensor blobs as bytes, not only their decoded JSON.
    digest = hashlib.sha256()
    for row in rows:
        for value in row:
            raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False,
                allow_nan=False, separators=(",", ":")).encode("utf-8")
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="results/profiling-v12/journal-before.py")
    parser.add_argument("--input-dir", default="results/live-20261009-143213-a629e7")
    parser.add_argument("--input-run", default="c894ca257cbe")
    parser.add_argument("--fixture", default="results/profiling-v12/received-fixture-c894ca257cbe.jsonl.gz")
    parser.add_argument("--counts", default="400,1000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", default="results/profiling-v12/observer-profile.json")
    args = parser.parse_args()
    counts = [int(text) for text in args.counts.split(",")]
    if not counts or any(value < 1 or value > 2000 for value in counts) or not 1 <= args.repeats <= 10:
        raise ValueError("Bounded profiles require 1–2000 packets and 1–10 repetitions")
    baseline_path = (ROOT / args.baseline).resolve()
    baseline = load_baseline(baseline_path)
    fixture_path = (ROOT / args.fixture).resolve()
    fixture_path.relative_to((ROOT / "results").resolve())
    if fixture_path.exists():
        packets, origins, dataset_sha, raw_lines = read_fixture(fixture_path, args.input_run, max(counts))
        source_kind = "portable_exact_saved_receipt_fixture"
    else:
        packets, origins, dataset_sha, raw_lines = read_packets((ROOT / args.input_dir).resolve(), args.input_run, max(counts))
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_bytes(gzip.compress(b"".join(raw_lines), compresslevel=9, mtime=0))
        source_kind = "historical_raw_receipt_prefix_copied_to_portable_fixture"
    config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
    results = []
    for count in counts:
        selected = packets[:count]
        before_time, before, before_trace = replay(baseline, selected, config, trace=True, parity=True)
        after_time, after, after_trace = replay(current, selected, config, trace=True, parity=True)
        if before != after:
            raise AssertionError("Archive tables / compressed bytes / raw receipts differ")
        durations = {"before": [], "after": []}
        # Alternate which implementation runs first, keeping external-load and
        # warm-cache bias smaller than a one-sided before/after campaign.
        for iteration in range(args.repeats):
            order = (("before", baseline), ("after", current)) if iteration % 2 == 0 else (("after", current), ("before", baseline))
            for label, module in order:
                elapsed, _, _ = replay(module, selected, config)
                durations[label].append(elapsed)
        medians = {label: statistics.median(values) for label, values in durations.items()}
        results.append(dict(packet_count=count, parity="PASS", received=before["received"],
            byte_parity={name: dict(rows=len(rows), sha256=evidence_digest(rows)) for name, rows in before["tables"].items()},
            raw_sha256=before["raw_sha256"], repetitions=args.repeats, unprofiled_wall_s=durations,
            median_wall_s=medians, median_wall_reduction_percent=(1 - medians["after"] / medians["before"]) * 100,
            traced_wall_s=dict(before=before_time, after=after_time),
            traces=dict(before=before_trace, after=after_trace)))
    report = dict(status="PASS", executed_at_utc=datetime.now(timezone.utc).isoformat(),
        model_version=__version__, python=platform.python_version(), platform=platform.platform(),
        scope="paired_saved_receipt_observer_ingestion_memory_only",
        source_scope="same current server for both; only journal implementation differs",
        before_journal=dict(path=str(baseline_path.relative_to(ROOT)), sha256=sha256(baseline_path)),
        source_sha256={str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path) for path in (
            ROOT / "factory_yard/journal.py", ROOT / "factory_yard/server.py", Path(__file__).resolve())},
        input=dict(run_id=args.input_run, historical_directory=args.input_dir, source_kind=source_kind,
            fixture=str(fixture_path.relative_to(ROOT)).replace("\\", "/"),
            fixture_sha256=sha256(fixture_path), fixture_compressed_bytes=fixture_path.stat().st_size,
            fixture_selected_uncompressed_bytes=sum(len(raw) for raw in raw_lines), max_packet_count=len(packets),
            selected_raw_sha256=dataset_sha, order="saved segment filename ascending, lines ascending; same FIFO prefix for both paths",
            first=origins[0], last=origins[-1]),
        limitations=["No live MQTT transport, callback decoding, disk I/O, WAL/fsync, browser polling or scheduler-load validation",
            "Saved input originated in a twelve-car run; this replay does not reproduce its production rate or prove sustainable capacity",
            "No packets are coalesced, dropped, reordered or given new receipt clocks; same sequence is supplied to both paths",
            "Wall times depend on local machine load; traces include profiler overhead and are not used for the median comparison"],
        cases=results)
    output = (ROOT / args.output).resolve()
    output.relative_to((ROOT / "results").resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(status=report["status"], model_version=__version__, output=str(output),
        cases=[{key: item[key] for key in ("packet_count", "parity", "median_wall_s", "median_wall_reduction_percent")} for item in results]),
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
