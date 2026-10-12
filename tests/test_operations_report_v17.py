"""Bounded prefix reports, diagnostic UTC intervals and immutable incident exports."""
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from factory_yard.journal import Journal
from factory_yard.operations import RULES
from factory_yard.operations_report import CSV_FIELDS, CSV_FORMULA_ESCAPE, ReportAccumulator, utc_interval_s
from factory_yard.server import Application, ROOT, create_http_server


def save_episode(journal, number=1, *, rule="sensor_unavailable", subject="CAR001", active=False,
                 acknowledged=True, opening_utc="2026-10-11T10:00:00Z", opened_utc="2026-10-11T10:00:02Z",
                 resolved_utc="2026-10-11T10:00:07Z", acknowledged_utc="2026-10-11T10:00:05Z", note=None):
    evidence = dict(transport_sample_id=number, telemetry_until_id=number + 10,
        events_until_id=number + 20, sim_time=1.5, utc=opening_utc)
    definition = RULES.get(rule, dict(title="Ismeretlen szabály", severity="warning", advice="Megőrzött"))
    item = dict(rule=rule, subject=subject, car_id=subject, **definition, active=active,
        acknowledged=acknowledged, revision=1, opened_utc=opened_utc, last_seen_utc=resolved_utc or opened_utc,
        resolved_utc=resolved_utc, acknowledged_utc=acknowledged_utc, note=note,
        opening=evidence, latest=dict(evidence, utc=resolved_utc or opened_utc), measurements={"fresh": False})
    return journal.save_operation(rule + ":" + subject, item)


def read_csv(raw):
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


class OperationsReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "run-journal.sqlite3"
        self.journal = Journal(self.path, "run")

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def test_empty_available_report_has_fixed_buckets_and_null_statistics(self):
        report = self.journal.operations_report()
        self.assertTrue(report["available"])
        self.assertEqual(report["until_id"], 0)
        self.assertEqual(report["counts"], dict(total=0, active=0, resolved=0, acknowledged=0, unacknowledged=0))
        self.assertEqual(report["recurrent_episodes"], 0)
        self.assertEqual([row["rule"] for row in report["rules"]], [*RULES, "other"])
        for row in report["rules"]:
            self.assertEqual(row["total"], 0)
        for statistics in report["durations"].values():
            self.assertEqual(statistics, dict(valid_count=0, excluded_count=0, min_s=None, max_s=None, mean_s=None))
        self.assertEqual(report["scope"], "episode_prefix_current_lifecycle_snapshot")
        self.assertIsNotNone(datetime.fromisoformat(report["captured_at_utc"]).utcoffset())

    def test_complete_prefix_over_100_episodes_recurrence_and_exact_exports(self):
        items = []
        for number in range(1, 246):
            # Two recurring subjects, many distinct unknown subjects: five
            # buckets must remain bounded while recurrence groups rule_key.
            rule = "sensor_unavailable" if number <= 220 else "unknown-" + str(number)
            subject = "CAR" + str(number % 2)
            items.append(save_episode(self.journal, number, rule=rule, subject=subject,
                active=number % 2 == 0, acknowledged=number % 3 == 0))
        report = self.journal.operations_report(until_id=233)
        self.assertEqual(report["counts"], dict(total=233, active=116, resolved=117, acknowledged=77, unacknowledged=156))
        self.assertEqual(report["recurrent_episodes"], 218)
        self.assertEqual([row["total"] for row in report["rules"]], [0, 0, 220, 0, 13])
        self.assertEqual(report["durations"]["confirmation"], dict(valid_count=233, excluded_count=0, min_s=2, max_s=2, mean_s=2))
        self.assertEqual(report["durations"]["resolved_active"]["valid_count"], 117)
        self.assertEqual(report["durations"]["acknowledgment"]["valid_count"], 77)
        document = json.loads(b"".join(self.journal.iter_operations_download("json", until_id=233)))
        self.assertEqual(document["items"], list(reversed(items[:233])))
        self.assertEqual(document["report"]["counts"], report["counts"])
        self.assertEqual(document["report"]["captured_at_utc"], document["captured_at_utc"])
        self.assertEqual(document["csv_formula_escape"], CSV_FORMULA_ESCAPE)
        raw = b"".join(self.journal.iter_operations_download("csv", until_id=233))
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw.count(b"\xef\xbb\xbf"), 1)
        rows = read_csv(raw)
        self.assertEqual(len(rows), 233)
        self.assertEqual(tuple(rows[0]), CSV_FIELDS)
        self.assertEqual([int(row["id"]) for row in rows], list(range(233, 0, -1)))
        self.assertEqual(rows[0]["opening_telemetry_until_id"], "243")
        self.assertEqual(rows[0]["confirmation_s"], "2.0")
        self.assertEqual(rows[1]["resolved_active_s"], "")
        self.assertEqual(rows[0]["acknowledgment_s"], "")

    def test_invalid_eligible_intervals_are_excluded_without_aging_active_episodes(self):
        save_episode(self.journal)
        save_episode(self.journal, 2, opening_utc="2026-10-11T10:00:03Z", resolved_utc="2026-10-11T10:00:01Z", acknowledged_utc=True)
        save_episode(self.journal, 3, opening_utc=None, opened_utc="2026-10-11T10:00:02", resolved_utc=None, acknowledged_utc="bad")
        save_episode(self.journal, 4, active=True, acknowledged=False, resolved_utc=None, acknowledged_utc=None)
        report = self.journal.operations_report()
        self.assertEqual(report["durations"]["confirmation"], dict(valid_count=2, excluded_count=2, min_s=2, max_s=2, mean_s=2))
        self.assertEqual(report["durations"]["resolved_active"], dict(valid_count=1, excluded_count=2, min_s=5, max_s=5, mean_s=5))
        self.assertEqual(report["durations"]["acknowledgment"], dict(valid_count=1, excluded_count=2, min_s=3, max_s=3, mean_s=3))
        rows = read_csv(b"".join(self.journal.iter_operations_download("csv")))
        self.assertEqual(rows[0]["resolved_active_s"], "")
        self.assertEqual(rows[0]["acknowledgment_s"], "")
        self.assertEqual(rows[1]["confirmation_s"], "")
        self.assertEqual(rows[2]["resolved_active_s"], "")
        self.assertEqual(rows[2]["acknowledgment_s"], "")

    def test_explicit_utc_offsets_and_nonfinite_boolean_naive_clocks(self):
        self.assertEqual(utc_interval_s("2026-10-11T12:00:00+02:00", "2026-10-11T10:00:04Z"), 4)
        for start, end in ((True, "2026-10-11T10:00:00Z"), (float("inf"), 0),
                ("2026-10-11T10:00:00", "2026-10-11T10:00:04Z"),
                ("2026-10-11T10:00:00Z", "2026-10-11T10:00:00"),
                ("2026-10-11T10:00:04Z", "2026-10-11T10:00:00Z"), ("NaN", "Infinity")):
            with self.subTest(start=start, end=end):
                self.assertIsNone(utc_interval_s(start, end))

    def test_means_extrema_zero_and_eligible_sample_counts(self):
        save_episode(self.journal, opening_utc="2026-10-11T10:00:02Z", resolved_utc="2026-10-11T10:00:02Z", acknowledged_utc="2026-10-11T10:00:02Z")
        save_episode(self.journal, 2, opening_utc="2026-10-11T10:00:00Z", resolved_utc="2026-10-11T10:00:08Z", acknowledged_utc="2026-10-11T10:00:06Z")
        durations = self.journal.operations_report()["durations"]
        self.assertEqual(durations["confirmation"], dict(valid_count=2, excluded_count=0, min_s=0, max_s=2, mean_s=1))
        self.assertEqual(durations["resolved_active"], dict(valid_count=2, excluded_count=0, min_s=0, max_s=6, mean_s=3))
        self.assertEqual(durations["acknowledgment"], dict(valid_count=2, excluded_count=0, min_s=0, max_s=4, mean_s=2))

    def test_prefix_membership_is_fixed_but_lifecycle_is_current_in_saved_run(self):
        first = save_episode(self.journal, active=True, acknowledged=False, resolved_utc=None)
        save_episode(self.journal, 2)
        before = self.journal.operations_report(until_id=1)
        first.update(active=False, resolved_utc="2026-10-11T10:00:09Z")
        self.journal.save_operation("sensor_unavailable:CAR001", first)
        after = self.journal.operations_report(until_id=1)
        self.assertEqual(before["counts"]["active"], 1)
        self.assertEqual(after["counts"], dict(total=1, active=0, resolved=1, acknowledged=0, unacknowledged=1))
        self.assertEqual(after["durations"]["resolved_active"]["mean_s"], 7)
        self.assertEqual(after["recurrent_episodes"], 0)
        empty = self.journal.operations_report(until_id=0)
        self.assertEqual(empty["counts"]["total"], 0)
        self.assertEqual(json.loads(b"".join(self.journal.iter_operations_download("json", until_id=0)))["items"], [])

    def test_csv_preserves_unicode_quotes_newlines_and_escapes_formulas_json_unchanged(self):
        notes = ("Árvíztűrő \"tükör\",\nkövetkező sor", '=HYPERLINK("https://invalid")', "+SUM(1,2)",
            "-1+1", "@SUM(1,2)", "\t=1+1", "\r=1+1", "\x01=1+1", " =1+1")
        items = [save_episode(self.journal, number, note=note) for number, note in enumerate(notes, 1)]
        # The convention protects other text fields too, not only note.
        items[-1].update(title="=title", subject="+subject", rule="@rule", advice="-advice")
        self.journal.save_operation("=rule-key", items[-1])
        rows = read_csv(b"".join(self.journal.iter_operations_download("csv")))
        self.assertEqual([row["note"] for row in reversed(rows)], [notes[0], *("'" + note for note in notes[1:])])
        self.assertEqual(rows[0]["title"], "'=title")
        self.assertEqual(rows[0]["subject"], "'+subject")
        self.assertEqual(rows[0]["rule"], "'@rule")
        self.assertEqual(rows[0]["advice"], "'-advice")
        # Updating a saved episode preserves its original rule_key; directly
        # seed a hostile archived key to verify that column's mitigation too.
        self.journal._connection.execute("UPDATE operations_incidents SET rule_key='=rule-key' WHERE id=?", (items[-1]["id"],))
        rows = read_csv(b"".join(self.journal.iter_operations_download("csv")))
        self.assertEqual(rows[0]["rule_key"], "'=rule-key")
        document = json.loads(b"".join(self.journal.iter_operations_download("json")))
        self.assertEqual(document["items"], list(reversed(items)))
        self.assertEqual(document["items"][0]["note"], notes[-1])

    def test_strict_future_ceiling_format_and_readonly_legacy_without_migration(self):
        save_episode(self.journal)
        for value in (True, -1, 1.2, "1.0", " 1", "١", "NaN", "9007199254740992", 2):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.journal.operations_report(until_id=value)
            with self.subTest(export=value), self.assertRaises(ValueError):
                self.journal.iter_operations_download("json", until_id=value)
        with self.assertRaises(ValueError):
            self.journal.iter_operations_download("png")
        self.journal.close()
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute("DROP TABLE operations_incidents")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "run", snapshot=True)
        try:
            report = reader.operations_report()
            self.assertFalse(report["available"])
            self.assertIsNone(report["counts"])
            self.assertIsNone(report["durations"])
            self.assertIsNone(report["recurrent_episodes"])
            self.assertEqual(report["rules"], [])
            for format in ("csv", "json"):
                with self.subTest(format=format), self.assertRaises(ValueError):
                    reader.iter_operations_download(format)
        finally:
            reader.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)


class OperationsReportHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "api-report"
        self.path = self.app.run_dir / "api-report-journal.sqlite3"
        self.app.journal = Journal(self.path, self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=config)
        self.items = [save_episode(self.app.journal, number) for number in range(1, 124)]
        (self.app.run_dir / "api-report-metadata.json").write_text(json.dumps(self.app.metadata), encoding="utf-8")
        self.server = create_http_server(self.app, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)
        self.app.journal.close()
        self.tmp.cleanup()

    def get(self, suffix):
        with urlopen(self.url + suffix, timeout=5) as response:
            return response.read(), response.headers

    def test_http_report_and_both_export_formats_pin_complete_prefix(self):
        raw, _ = self.get("/api/operations/report?run_id=api-report&until_id=117")
        report = json.loads(raw)
        self.assertEqual(report["counts"]["total"], 117)
        self.assertEqual(report["recurrent_episodes"], 116)
        raw, headers = self.get("/api/operations/export?run_id=api-report&until_id=117&format=json")
        document = json.loads(raw)
        self.assertEqual(document["items"], list(reversed(self.items[:117])))
        self.assertEqual(document["report"]["counts"], report["counts"])
        self.assertIn("api-report.json", headers["Content-Disposition"])
        raw, headers = self.get("/api/operations/export?run_id=api-report&until_id=117&format=csv")
        self.assertEqual(len(read_csv(raw)), 117)
        self.assertEqual(headers["X-CSV-Formula-Escape"], CSV_FORMULA_ESCAPE)

    def test_http_invalid_queries_fail_400_before_download_headers(self):
        for path in ("/api/operations/report", "/api/operations/export", "/api/operations/report?run_id=missing",
                "/api/operations/export?run_id=missing", "/api/operations/export?run_id=../escape",
                "/api/operations/export?run_id=api-report&format=png",
                "/api/operations/export?run_id=api-report&until_id=124",
                "/api/operations/export?run_id=api-report&until_id=NaN",
                "/api/operations/report?run_id=api-report&until_id=124"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 400)
            self.assertIsNone(error.exception.headers.get("Content-Disposition"))
            self.assertIn("error", json.loads(error.exception.read()))
        for run_id, format, until_id in ((None, "json", None), ("missing", "json", None),
                ("api-report", "png", None), ("api-report", "json", 124)):
            with self.subTest(run_id=run_id, format=format), self.assertRaises(ValueError):
                self.app.operations_export(run_id, format, until_id=until_id)

    def test_live_and_archived_streams_snapshot_remain_immutable_across_updates_and_reset(self):
        for archived in (False, True):
            with self.subTest(archived=archived):
                self.app.run_id = "new-run" if archived else "api-report"
                expected = list(reversed(self.app.journal.operations(limit=100, until_id=2)["items"]))
                streams = {format: self.app.operations_export("api-report", format, until_id=2)
                    for format in ("csv", "json")}
                # Snapshot was opened eagerly even before the first yielded
                # byte. Neither later lifecycle writes nor new runs alter it.
                first = self.app.journal.operations(until_id=1)["items"][0]
                first.update(note="changed after snapshot " + str(archived), active=True)
                self.app.journal.save_operation("sensor_unavailable:CAR001", first)
                self.app.journal.flush()
                self.app.run_id = "new-run"
                document = json.loads(b"".join(streams["json"]))
                rows = read_csv(b"".join(streams["csv"]))
                self.assertEqual(document["run_id"], "api-report")
                self.assertEqual(document["items"], list(reversed(expected)))
                self.assertEqual(rows[-1]["note"], expected[0]["note"] or "")
                self.assertEqual(document["report"]["counts"]["active"], sum(item["active"] for item in expected))

    def test_report_uses_one_read_snapshot_during_concurrent_lifecycle_changes(self):
        original = ReportAccumulator.add
        changed = False
        def mutate_during_scan(accumulator, item):
            nonlocal changed
            if not changed:
                changed = True
                later = self.app.journal.operations(until_id=123)["items"][0]
                later.update(active=True, note="after report began")
                self.app.journal.save_operation("sensor_unavailable:CAR001", later)
                self.app.journal.flush()
            original(accumulator, item)
        with patch.object(ReportAccumulator, "add", mutate_during_scan):
            report = self.app.operations_report("api-report")
        self.assertEqual(report["counts"]["active"], 0)
        self.assertEqual(self.app.operations_report("api-report")["counts"]["active"], 1)

    def test_legacy_report_and_export_use_archive_without_changing_database(self):
        self.app.journal.close()
        self.app.run_id = "new-run"
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute("DROP TABLE operations_incidents")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        raw, _ = self.get("/api/operations/report?run_id=api-report")
        report = json.loads(raw)
        self.assertFalse(report["available"])
        self.assertIsNone(report["counts"])
        for format in ("csv", "json"):
            with self.subTest(format=format), self.assertRaises(HTTPError) as error:
                self.get("/api/operations/export?run_id=api-report&format=" + format)
            self.assertEqual(error.exception.code, 400)
            self.assertIsNone(error.exception.headers.get("Content-Disposition"))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
