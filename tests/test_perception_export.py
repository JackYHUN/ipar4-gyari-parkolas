"""Native frame attachments preserve evidence while rejecting active SVG input."""
from http.client import HTTPResponse
import json
import socket
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from factory_yard.server import SVG_NAMESPACE, create_http_server


class PerceptionExportTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.server = create_http_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def post(self, kind, content, filename="CAR001_run_7.050_erzekelo.json", *, origin=None, raw=None, content_type=None):
        body = raw if raw is not None else urlencode({"filename": filename, "format": kind, "content": content}).encode("utf-8")
        headers = {"Content-Type": content_type or "application/x-www-form-urlencoded;charset=UTF-8"}
        if origin is not None:
            headers["Origin"] = origin
        request = Request(self.url + "/api/perception/export", body, headers, method="POST")
        with urlopen(request, timeout=5) as response:
            return response.read(), response.headers

    @staticmethod
    def frame():
        return {"schema_version": 1, "kind": "received_virtual_sensor_frame", "car_id": "CAR001",
            "telemetry_sim_time": 9.05, "utc": "2026-10-05T20:00:01.123Z",
            "sensors": {"sample_sim_time_s": 7.0, "age_s": 2.05, "fresh": False,
                "environment": {"vehicles": [{"id": "CAR002", "distance_m": 3.42}]}},
            "control": {"decision_sim_time_s": 9.05, "stop_reason": "Elavult érzékelés"}}

    def passive_svg(self, *, prefix=""):
        metadata = json.dumps(self.frame(), ensure_ascii=False)
        return prefix + f'''<svg xmlns="{SVG_NAMESPACE}" width="910" height="397" viewBox="0 0 910 397" font-family="Segoe UI, Arial, sans-serif">
            <rect width="910" height="397" fill="#f4f9fa"/>
            <svg x="10" y="37" width="340" height="340" viewBox="0 0 340 340" role="img" aria-label="Fogadott környezetminta" data-sample-time="7" data-fresh="false">
                <circle cx="170" cy="170" r="138" fill="none" stroke="#ddb36c" stroke-width="1.2" stroke-dasharray="4 5"/>
                <g><path d="M170 150L166 158H174Z" fill="#f3fafc"/>
                <text x="170" y="331" font-size="10" font-weight="600" text-anchor="middle" paint-order="stroke" stroke="#122d3b" stroke-width="3">CAR001 · ELAVULT</text></g>
            </svg><metadata>{metadata}</metadata></svg>'''

    def assert_rejected(self, kind, content, **kwargs):
        with self.assertRaises(HTTPError) as caught:
            self.post(kind, content, **kwargs)
        self.assertEqual(caught.exception.code, 400)
        self.assertNotIn("Content-Disposition", caught.exception.headers)
        json.loads(caught.exception.read())

    def test_json_attachment_preserves_separate_clocks_nested_observations_and_hungarian_text(self):
        frame = self.frame()
        content, headers = self.post("json", json.dumps(frame), origin=self.url)
        self.assertEqual(json.loads(content), frame)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="CAR001_run_7.050_erzekelo.json"')
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(int(headers["Content-Length"]), len(content))
        self.app.control.assert_not_called()

    def test_svg_attachment_keeps_passive_geometry_and_exact_observation_metadata(self):
        content, headers = self.post("svg", self.passive_svg(), "CAR001.svg")
        document = ET.fromstring(content)
        self.assertEqual(document.tag, "{" + SVG_NAMESPACE + "}svg")
        self.assertEqual(json.loads(document.findtext("{" + SVG_NAMESPACE + "}metadata")), self.frame())
        self.assertIsNotNone(document.find(".//{" + SVG_NAMESPACE + "}circle"))
        self.assertEqual(headers["Content-Type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="CAR001.svg"')
        self.assertEqual(headers["Content-Security-Policy"], "default-src 'none'; sandbox")

    def test_filename_is_basename_sanitized_and_cannot_inject_headers_or_an_active_extension(self):
        _, headers = self.post("json", "{}", "C:\\private\\../../audit\r\nX-Injected: evil.html")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="audit__X-Injected__evil.json"')
        self.assertNotIn("X-Injected", headers)
        _, headers = self.post("svg", self.passive_svg(), "../../...")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="erzekelo_minta.svg"')

    def test_cross_origin_form_is_rejected_before_content_is_processed(self):
        with self.assertRaises(HTTPError) as caught:
            self.post("json", "{}", origin="https://outside.example")
        self.assertEqual(caught.exception.code, 403)
        self.assertNotIn("Content-Disposition", caught.exception.headers)
        self.assertIn("error", json.loads(caught.exception.read()))
        self.app.control.assert_not_called()

    def test_rejected_incomplete_oversize_body_does_not_block_waiting_for_declared_size(self):
        # The drain is bounded even when a client advertises an enormous body
        # but leaves its write half open without actually sending the payload.
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3) as connection:
            request = ("POST /api/perception/export HTTP/1.0\r\n"
                f"Host: 127.0.0.1:{self.server.server_port}\r\n"
                "Content-Type: application/x-www-form-urlencoded\r\n"
                "Content-Length: 2147483647\r\n\r\nx")
            started = time.monotonic()
            connection.sendall(request.encode("ascii"))
            response = HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 400)
            self.assertNotIn("Content-Disposition", response.headers)
            self.assertIn("error", json.loads(response.read()))
            self.assertLess(time.monotonic() - started, 2)
        self.app.control.assert_not_called()

    def test_malformed_wrong_type_and_oversize_forms_return_errors_without_attachments(self):
        cases = [
            ("json", "[]", {}), ("json", "{", {}), ("json", '{"age":NaN}', {}),
            ("json", '{"age":1e999}', {}), ("html", "{}", {}),
            ("json", "{}", {"content_type": "application/json"}),
            ("json", "{}", {"raw": b"filename=x&format=json&content=%FF"}),
            ("json", "{}", {"raw": b"filename=x&format=json&content={}&content={}"}),
            ("json", "{}", {"raw": b"filename=x&format=json"}),
            ("json", json.dumps({"text": "x" * (128 * 1024)}), {}),
            ("json", "{}", {"raw": b"x" * (256 * 1024 + 1)}),
        ]
        for kind, content, options in cases:
            with self.subTest(kind=kind, options=list(options), size=len(content)):
                self.assert_rejected(kind, content, **options)

    def test_active_svg_elements_external_references_and_xml_entities_are_rejected(self):
        cases = [
            "<svg>",
            f'<svg xmlns="{SVG_NAMESPACE}"><script>alert(1)</script></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}" onload="alert(1)"/>',
            f'<svg xmlns="{SVG_NAMESPACE}"><foreignObject><div/></foreignObject></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}"><image href="https://outside.example/image"/></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}"><path href="https://outside.example" d="M0 0"/></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}"><rect style="fill:url(https://outside.example)"/></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}"><rect fill="url(https://outside.example)"/></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}"><svg xmlns="http://www.w3.org/1999/xhtml"/></svg>',
            f'<!DOCTYPE svg [<!ENTITY file SYSTEM "file:///C:/private.txt">]><svg xmlns="{SVG_NAMESPACE}"><text>&file;</text></svg>',
            f'<svg xmlns="{SVG_NAMESPACE}">' + '<g>' * 15 + '</g>' * 15 + '</svg>',
        ]
        for content in cases:
            with self.subTest(content=content[:80]):
                self.assert_rejected("svg", content)

    def test_xml_stylesheet_processing_instructions_are_removed_from_passive_exports(self):
        content, _ = self.post("svg", self.passive_svg(prefix='<?xml-stylesheet href="https://outside.example/style.css"?>'))
        self.assertNotIn(b"outside.example", content)
        self.assertNotIn(b"xml-stylesheet", content)
        self.assertEqual(json.loads(ET.fromstring(content).findtext("{" + SVG_NAMESPACE + "}metadata")), self.frame())


if __name__ == "__main__":
    unittest.main()
