"""Bounded summaries of saved incident episodes and diagnostic host UTC intervals.

The intervals describe observer bookkeeping, never physical downtime. Resolved
and acknowledgment intervals need a recorded end; active episodes are not aged
to the current clock. CSV protects spreadsheet cells with an apostrophe prefix;
the JSON export preserves the original saved episode payloads.
"""
from datetime import datetime, timezone
import json
import math

from .operations import RULES


REPORT_SCOPE = "episode_prefix_current_lifecycle_snapshot"
CSV_FORMULA_ESCAPE = "apostrophe_prefix_for_formula_or_control_leading_cells"
COUNT_FIELDS = ("total", "active", "resolved", "acknowledged", "unacknowledged")
DURATION_FIELDS = ("confirmation", "resolved_active", "acknowledgment")
CSV_FIELDS = (
    "run_id", "until_id", "captured_at_utc", "id", "rule_key", "rule", "subject", "car_id",
    "title", "severity", "advice", "active", "acknowledged", "revision", "opened_utc",
    "last_seen_utc", "resolved_utc", "acknowledged_utc", "note", "acknowledged_from_revision",
    "opening_transport_sample_id", "opening_utc", "opening_sim_time", "opening_telemetry_until_id",
    "opening_events_until_id", "latest_transport_sample_id", "latest_utc", "latest_sim_time",
    "latest_telemetry_until_id", "latest_events_until_id", "measurements_json",
    "confirmation_s", "resolved_active_s", "acknowledgment_s",
)


def utc_interval_s(start, end):
    """Accept only explicit timezone-aware timestamps and finite, nonnegative intervals."""
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        first = datetime.fromisoformat(start)
        last = datetime.fromisoformat(end)
        if first.tzinfo is None or last.tzinfo is None or first.utcoffset() is None or last.utcoffset() is None:
            return None
        seconds = (last.astimezone(timezone.utc) - first.astimezone(timezone.utc)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def episode_durations(item):
    opening = item.get("opening")
    return dict(
        confirmation=utc_interval_s(opening.get("utc") if isinstance(opening, dict) else None, item.get("opened_utc")),
        resolved_active=utc_interval_s(item.get("opened_utc"), item.get("resolved_utc")) if item.get("active") is False else None,
        acknowledgment=utc_interval_s(item.get("opened_utc"), item.get("acknowledged_utc")) if item.get("acknowledged") is True else None,
    )


class DurationStatistics:
    def __init__(self):
        self.valid_count = self.excluded_count = 0
        self.min_s = self.max_s = self.mean_s = None

    def add(self, seconds):
        if seconds is None:
            self.excluded_count += 1
            return
        self.valid_count += 1
        self.min_s = seconds if self.min_s is None else min(self.min_s, seconds)
        self.max_s = seconds if self.max_s is None else max(self.max_s, seconds)
        self.mean_s = seconds if self.mean_s is None else self.mean_s + (seconds - self.mean_s) / self.valid_count

    def payload(self):
        return dict(valid_count=self.valid_count, excluded_count=self.excluded_count,
            min_s=self.min_s, max_s=self.max_s, mean_s=self.mean_s)


class ReportAccumulator:
    """Constant-size state: five rule buckets and three duration accumulators."""
    def __init__(self):
        self.counts = dict.fromkeys(COUNT_FIELDS, 0)
        self.rules = {rule: dict(rule=rule, title=definition["title"], **dict.fromkeys(COUNT_FIELDS, 0))
            for rule, definition in RULES.items()}
        self.rules["other"] = dict(rule="other", title="Egyéb incidens", **dict.fromkeys(COUNT_FIELDS, 0))
        self.durations = {field: DurationStatistics() for field in DURATION_FIELDS}

    def add(self, item):
        rule = item.get("rule")
        bucket = self.rules.get(rule, self.rules["other"]) if isinstance(rule, str) else self.rules["other"]
        fields = ("total", "active" if item.get("active") is True else "resolved",
            "acknowledged" if item.get("acknowledged") is True else "unacknowledged")
        for field in fields:
            self.counts[field] += 1
            bucket[field] += 1
        durations = episode_durations(item)
        self.durations["confirmation"].add(durations["confirmation"])
        if item.get("active") is False:
            self.durations["resolved_active"].add(durations["resolved_active"])
        if item.get("acknowledged") is True:
            self.durations["acknowledgment"].add(durations["acknowledgment"])

    def payload(self):
        return dict(counts=self.counts, durations={field: statistics.payload()
            for field, statistics in self.durations.items()}, rules=list(self.rules.values()))


def csv_safe_cell(value):
    """Escape formula markers and leading whitespace/control before CSV quoting."""
    if value is None:
        return None
    text = str(value)
    if text and (text[0] in "=+-@" or text[0].isspace() or ord(text[0]) < 32 or ord(text[0]) == 127):
        return "'" + text
    return text


def episode_csv_row(item, rule_key, report):
    row = {field: item.get(field) for field in CSV_FIELDS}
    row.update(run_id=report["run_id"], until_id=report["until_id"],
        captured_at_utc=report["captured_at_utc"], rule_key=rule_key)
    for name in ("opening", "latest"):
        evidence = item.get(name)
        for field in ("transport_sample_id", "utc", "sim_time", "telemetry_until_id", "events_until_id"):
            row[name + "_" + field] = evidence.get(field) if isinstance(evidence, dict) else None
    measurements = item.get("measurements")
    row["measurements_json"] = json.dumps(measurements, ensure_ascii=False, separators=(",", ":"), allow_nan=False) if measurements is not None else None
    row.update({field + "_s": seconds for field, seconds in episode_durations(item).items()})
    return {field: csv_safe_cell(value) for field, value in row.items()}
