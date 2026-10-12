"""Whole-prefix Unicode incident search, bounded pages and unchanged evidence exports."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from unittest.mock import patch

from factory_yard.journal import Journal
from factory_yard.operations import RULES
from factory_yard.server import Application, ROOT, create_http_server
from tests.test_operations_report_v17 import save_episode


DEFAULT_FILTERS = dict(status="all", rule="all", severity="all", car_id="", search="")


def incident(journal, number, *, rule="sensor_unavailable", car_id="CAR001", severity=None,
             note=None, title=None, advice=None, active=False, acknowledged=False):
    item = save_episode(journal, number, rule=rule, subject=car_id or "fleet",
        active=active, acknowledged=acknowledged, note=note)
    item["car_id"] = car_id
    if severity is not None:
        item["severity"] = severity
    if title is not None:
        item["title"] = title
    if advice is not None:
        item["advice"] = advice
    return journal.save_operation(rule + ":" + item["subject"], item)


class OperationsSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "search-journal.sqlite3"
        self.journal = Journal(self.path, "search")

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def test_default_and_original_status_contracts_have_no_new_metadata(self):
        incident(self.journal, 1, active=True)
        incident(self.journal, 2, acknowledged=True)
        for status in ("all", "active", "unacknowledged"):
            with self.subTest(status=status):
                result = self.journal.operations(status=status)
                self.assertNotIn("filters", result)
                self.assertNotIn("matching_count", result)
        header = json.loads(b"".join(self.journal.iter_operations_export()))
        self.assertNotIn("filters", header)
        self.assertNotIn("matching_count", header)
        self.assertEqual(len(header["items"]), 2)

    def test_explicit_all_and_empty_extra_filters_select_new_metadata(self):
        incident(self.journal, 1)
        for filters in (dict(rule="all"), dict(severity="all"), dict(car_id=""), dict(search=""),
                dict(rule="all", severity="all", car_id="", search="   ")):
            with self.subTest(filters=filters):
                result = self.journal.operations(**filters)
                self.assertEqual(result["filters"], DEFAULT_FILTERS)
                self.assertEqual(result["matching_count"], 1)
        empty = self.journal.operations(search="", until_id=0)
        self.assertEqual(empty["matching_count"], 0)

    def test_status_partitions_are_independent_and_new_statuses_echo_filters(self):
        for number, flags in enumerate(((True, False), (True, True), (False, False), (False, True)), 1):
            incident(self.journal, number, active=flags[0], acknowledged=flags[1])
        expected = dict(all=[4, 3, 2, 1], active=[2, 1], resolved=[4, 3],
            acknowledged=[4, 2], unacknowledged=[3, 1])
        summary = self.journal.operations()["summary"]
        for status, ids in expected.items():
            with self.subTest(status=status):
                result = self.journal.operations(status=status, search="")
                self.assertEqual([item["id"] for item in result["items"]], ids)
                self.assertEqual(result["matching_count"], len(ids))
                self.assertEqual(result["filters"]["status"], status)
                self.assertEqual(result["summary"], summary)
        for status in ("resolved", "acknowledged"):
            self.assertIn("matching_count", self.journal.operations(status=status))

    def test_older_matches_beyond_100_episodes_page_without_duplicates_and_count_ignores_before(self):
        for number in range(1, 321):
            incident(self.journal, number, note="Régebbi célpont" if number in (1, 9, 108, 205, 319) else "más")
        statements = []
        self.journal._connection.set_trace_callback(statements.append)
        page = self.journal.operations(limit=2, search="célpont", until_id=310)
        self.assertEqual([item["id"] for item in page["items"]], [205, 108])
        self.assertEqual(page["next_before"], 108)
        self.assertEqual(page["matching_count"], 4)
        next_page = self.journal.operations(limit=2, before=108, search="célpont", until_id=310)
        self.assertEqual([item["id"] for item in next_page["items"]], [9, 1])
        self.assertIsNone(next_page["next_before"])
        self.assertEqual(next_page["matching_count"], 4)
        self.assertEqual(next_page["summary"]["total"], 320)
        self.assertEqual(next_page["summary"]["prefix_count"], 310)
        payload_queries = [query for query in statements if query.startswith("SELECT payload FROM operations_incidents")]
        self.assertEqual(len(payload_queries), 2)
        self.assertTrue(all(query.endswith("LIMIT 3") for query in payload_queries))
        matching_queries = [query for query in statements if query.startswith("SELECT COUNT(*) FROM operations_incidents") and "operations_match" in query]
        self.assertEqual(len(matching_queries), 2)
        self.assertTrue(all("id<108" not in query for query in matching_queries))

    def test_combined_rule_severity_car_and_status_filter_is_exact_and_summary_stays_whole_run(self):
        incident(self.journal, 1, active=True, note="target")
        incident(self.journal, 2, rule="processing_lag", active=True, note="target")
        incident(self.journal, 3, car_id="CAR002", active=True, note="target")
        incident(self.journal, 4, severity="warning", active=True, note="target")
        incident(self.journal, 5, active=False, note="target")
        incident(self.journal, 6, active=True, note="elsewhere")
        result = self.journal.operations(status="active", rule="sensor_unavailable", severity="error", car_id="CAR001", search="TARGET")
        self.assertEqual([item["id"] for item in result["items"]], [1])
        self.assertEqual(result["matching_count"], 1)
        self.assertEqual(result["summary"]["total"], 6)
        self.assertEqual(result["summary"]["active"], 5)
        self.assertEqual(result["summary"]["unacknowledged"], 6)
        self.assertEqual(result["summary"]["health"], "attention")
        self.assertEqual(result["filters"], dict(status="active", rule="sensor_unavailable", severity="error", car_id="CAR001", search="TARGET"))
        self.assertEqual(self.journal.operations(car_id="car001")["matching_count"], 0)
        self.assertEqual(self.journal.operations(car_id="CAR0")["matching_count"], 0)

    def test_search_is_literal_for_percent_underscore_quotes_and_sql_injection_text(self):
        text = "100%_ ellenőrizve; ' OR 1=1 --"
        incident(self.journal, 1, note=text)
        incident(self.journal, 2, note="100X_ ellenőrizve")
        incident(self.journal, 3, note="100%X ellenőrizve")
        for query in ("100%_", "' OR 1=1 --", "%;", "%_ ellenőrizve"):
            expected = [] if query == "%;" else [1]
            with self.subTest(query=query):
                result = self.journal.operations(search=query)
                self.assertEqual([item["id"] for item in result["items"]], expected)
                self.assertEqual(result["matching_count"], len(expected))
        self.assertEqual(self.journal.operations()["summary"]["total"], 3)

    def test_full_unicode_casefold_and_original_trimmed_search_echo(self):
        incident(self.journal, 1, note="Árvíztűrő Straße ΟΣ")
        for query in ("  ÁRVÍZTŰRŐ  ", "STRASSE", "strasse", "ος", "οσ"):
            with self.subTest(query=query):
                result = self.journal.operations(search=query)
                self.assertEqual([item["id"] for item in result["items"]], [1])
                self.assertEqual(result["filters"]["search"], query.strip())
        incident(self.journal, 2, note="SS" * 128)
        result = self.journal.operations(search="ß" * 128)
        self.assertEqual([item["id"] for item in result["items"]], [2])
        self.assertEqual(len(result["filters"]["search"]), 128)

    def test_search_each_allowed_field_without_matching_measurements_or_field_boundary(self):
        item = incident(self.journal, 1, title="title-marker", advice="advice-marker", note="note-marker")
        item["subject"] = "subject-marker"
        item["measurements"] = {"reason": "measurement-only-marker"}
        self.journal.save_operation("sensor_unavailable:CAR001", item)
        for query in ("title-marker", "advice-marker", "note-marker", "subject-marker", "CAR001", "sensor_unavailable"):
            with self.subTest(query=query):
                self.assertEqual(self.journal.operations(search=query)["matching_count"], 1)
        for query in ("measurement-only-marker", "markeradvice"):
            self.assertEqual(self.journal.operations(search=query)["matching_count"], 0)

    def test_prefix_only_bounds_matching_membership_and_report_export_are_unfiltered(self):
        for number in range(1, 126):
            incident(self.journal, number, note="match" if number in (1, 55, 125) else "other")
        filtered = self.journal.operations(search="match", until_id=100)
        self.assertEqual(filtered["matching_count"], 2)
        self.assertEqual([item["id"] for item in filtered["items"]], [55, 1])
        self.assertEqual(self.journal.operations_report(until_id=100)["counts"]["total"], 100)
        exported = json.loads(b"".join(self.journal.iter_operations_download("json", until_id=100)))
        self.assertEqual(len(exported["items"]), 100)
        self.assertNotIn("filters", exported)
        self.assertNotIn("matching_count", exported)
        self.assertEqual(len(json.loads(b"".join(self.journal.iter_operations_export()))["items"]), 125)

    def test_invalid_filters_lengths_and_bounds_reject_ambiguous_inputs(self):
        incident(self.journal, 1)
        invalid = [dict(status=value) for value in (None, True, "closed", "", {}, 1)]
        invalid += [dict(rule=value) for value in (True, "unknown", "", "SENSOR_UNAVAILABLE", {}, 1)]
        invalid += [dict(severity=value) for value in (True, "info", "", "ERROR", {}, 1)]
        invalid += [dict(car_id=value) for value in (True, " CAR001", "../CAR001", "CAR*", "Á", "x" * 65, {}, 1)]
        invalid += [dict(search=value) for value in (True, {}, [], 1, "😀" * 129, " " * 129)]
        invalid += [dict(search="x", until_id=value) for value in (2, -1, "NaN")]
        invalid += [dict(search="x", before=2), dict(search="x", until_id=0, before=1)]
        for filters in invalid:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.journal.operations(**filters)

    def test_readonly_udf_frozen_count_and_legacy_queries_do_not_modify_database(self):
        incident(self.journal, 1, note="Straße")
        self.journal.flush()
        reader = Journal.open_readonly(self.path, "search", snapshot=True)
        try:
            self.assertEqual(reader.operations(search="STRASSE")["matching_count"], 1)
            incident(self.journal, 2, note="Straße")
            self.journal.flush()
            self.assertEqual(reader.operations(search="STRASSE")["matching_count"], 1)
            self.assertEqual(self.journal.operations(search="STRASSE")["matching_count"], 2)
        finally:
            reader.close()
        self.journal.close()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "search", snapshot=True)
        try:
            self.assertEqual(reader.operations(search="STRASSE")["matching_count"], 2)
        finally:
            reader.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute("DROP TABLE operations_incidents")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        reader = Journal.open_readonly(self.path, "search", snapshot=True)
        try:
            result = reader.operations(status="resolved", rule="all", severity="all", car_id="", search="straße")
            self.assertFalse(result["available"])
            self.assertIsNone(result["matching_count"])
            self.assertEqual(result["filters"]["search"], "straße")
            self.assertEqual(result["items"], [])
            self.assertNotIn("matching_count", reader.operations())
        finally:
            reader.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)


class OperationsSearchHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = json.loads((ROOT / "config/default.json").read_text(encoding="utf-8"))
        self.app = Application(config, ROOT / "config/default.json")
        self.app.run_dir, self.app.run_id = Path(self.tmp.name), "api-search"
        self.path = self.app.run_dir / "api-search-journal.sqlite3"
        self.app.journal = Journal(self.path, self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=config)
        (self.app.run_dir / "api-search-metadata.json").write_text(json.dumps(self.app.metadata), encoding="utf-8")
        for number in range(1, 128):
            incident(self.app.journal, number, note="Straße 100%_ célpont" if number in (1, 57, 126) else "other",
                active=number % 2 == 0, acknowledged=number % 3 == 0)
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

    def get(self, path, **query):
        suffix = "?" + urlencode(query) if query else ""
        with urlopen(self.url + path + suffix, timeout=5) as response:
            return json.load(response)

    def test_http_preserves_default_and_explicit_empty_query_presence(self):
        plain = self.get("/api/operations", run_id="api-search")
        self.assertNotIn("filters", plain)
        self.assertNotIn("matching_count", plain)
        for query in (dict(rule="all"), dict(severity="all"), dict(car_id=""), dict(search="")):
            with self.subTest(query=query):
                filtered = self.get("/api/operations", run_id="api-search", **query)
                self.assertEqual(filtered["filters"], DEFAULT_FILTERS)
                self.assertEqual(filtered["matching_count"], 127)

    def test_http_unicode_literal_search_pages_and_summary_report_export_invariance(self):
        query = dict(run_id="api-search", rule="all", severity="all", car_id="", search="  STRASSE  ", status="all", limit=1, until_id=125)
        first = self.get("/api/operations", **query)
        self.assertEqual([item["id"] for item in first["items"]], [57])
        self.assertEqual(first["matching_count"], 2)
        self.assertEqual(first["filters"]["search"], "STRASSE")
        last = self.get("/api/operations", **dict(query, before=first["next_before"]))
        self.assertEqual([item["id"] for item in last["items"]], [1])
        self.assertEqual(last["matching_count"], 2)
        self.assertIsNone(last["next_before"])
        plain = self.get("/api/operations", run_id="api-search", until_id=125)
        self.assertEqual(first["summary"], plain["summary"])
        report = self.get("/api/operations/report", run_id="api-search", until_id=125)
        exported = self.get("/api/operations/export", run_id="api-search", until_id=125, format="json")
        self.assertEqual(report["counts"]["total"], 125)
        self.assertEqual(len(exported["items"]), 125)
        self.assertEqual(exported["report"]["counts"], report["counts"])
        literal = self.get("/api/operations", run_id="api-search", search="100%_", until_id=125)
        self.assertEqual(literal["matching_count"], 2)

    def test_http_invalid_filter_queries_are_400(self):
        invalid = (dict(status="bad"), dict(status=""), dict(rule="other"), dict(rule=""),
            dict(severity="info"), dict(severity=""), dict(car_id="../escape"), dict(car_id="Á"),
            dict(search="x" * 129), dict(search="x", until_id=128), dict(search="x", before=999))
        for query in invalid:
            with self.subTest(query=query), self.assertRaises(HTTPError) as error:
                self.get("/api/operations", run_id="api-search", **query)
            self.assertEqual(error.exception.code, 400)
            self.assertIn("error", json.loads(error.exception.read()))

    def test_selected_run_reset_readonly_and_legacy_remain_explicit_and_immutable(self):
        old_journal = self.app.journal
        self.app.run_id = "new-search"
        self.app.journal = Journal(self.app.run_dir / "new-search-journal.sqlite3", self.app.run_id)
        self.app.metadata = dict(run_id=self.app.run_id, config=self.app.config)
        (self.app.run_dir / "new-search-metadata.json").write_text(json.dumps(self.app.metadata), encoding="utf-8")
        try:
            incident(self.app.journal, 1, note="new run")
            old_journal.close()
            before = hashlib.sha256(self.path.read_bytes()).hexdigest()
            archived = self.get("/api/operations", run_id="api-search", search="STRASSE")
            self.assertFalse(archived["current_live"])
            self.assertEqual(archived["matching_count"], 3)
            self.assertEqual(archived["run_id"], "api-search")
            self.assertEqual(self.get("/api/operations", run_id="new-search", search="STRASSE")["matching_count"], 0)
            self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
            with self.assertRaises(HTTPError) as error:
                self.get("/api/operations", run_id="foreign-search", search="STRASSE")
            self.assertEqual(error.exception.code, 400)
            with closing(sqlite3.connect(self.path)) as connection:
                with connection:
                    connection.execute("DROP TABLE operations_incidents")
            before = hashlib.sha256(self.path.read_bytes()).hexdigest()
            legacy = self.get("/api/operations", run_id="api-search", search="STRASSE", status="resolved")
            self.assertFalse(legacy["available"])
            self.assertIsNone(legacy["matching_count"])
            self.assertEqual(legacy["filters"]["status"], "resolved")
            self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        finally:
            old_journal.close()


if __name__ == "__main__":
    unittest.main()
