"""Check saved browser SVG geometry against the local passive attachment endpoint.

This validates HTTP output, not the embedded browser's download delivery.
Capture selected-dom-render-v09.json through the documented browser API first.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def main():
    snapshot_path = ROOT / "results/ui/selected-dom-render-v09.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    summary_path = sorted((ROOT / "results").glob("perception-smoke-*/summary.json"))[-1]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    archive = json.loads(Path(summary["json_export"]).read_text(encoding="utf-8"))
    assert snapshot["run"] == summary["run_id"]
    # The capture explicitly selected the first received frame and rear channel.
    row = archive["telemetry"][0]
    radar = ET.fromstring(snapshot["radar"])
    front = ET.fromstring(snapshot["front"])
    assert float(radar.attrib["data-sample-time"]) == row["sensors"]["sample_sim_time_s"]
    assert snapshot["selected"] == ["rear_center"]
    metadata = dict(schema_version=1, kind="received_virtual_sensor_frame",
                    run_id=snapshot["run"], car_id=snapshot["car"],
                    telemetry_sim_time=row["sim_time"], telemetry_source=row["details"].get("source"),
                    utc=row["utc"], sensors=row["sensors"], control=row["control"],
                    view_context=dict(archived=True, fresh_in_view=True, held_during_gap=False,
                                      selected_channel_id="rear_center"))
    wrapper = ET.Element("{" + NS + "}svg", dict(width="910", height="397", viewBox="0 0 910 397"))
    ET.SubElement(wrapper, "{" + NS + "}metadata").text = json.dumps(metadata, ensure_ascii=False)
    for element, x, width, height in ((radar, 10, 340, 340), (front, 372, 510, 300)):
        element.attrib.pop("id", None)
        element.attrib.update(x=str(x), y="37", width=str(width), height=str(height))
        # HTML's outerHTML omits the SVG namespace; preserve actual geometry.
        for child in element.iter():
            if not child.tag.startswith("{"):
                child.tag = "{" + NS + "}" + child.tag
        wrapper.append(element)
    outputs = []
    for kind, content in (("svg", ET.tostring(wrapper, encoding="unicode")),
                          ("json", json.dumps(metadata, ensure_ascii=False))):
        body = urlencode(dict(format=kind, content=content,
                              filename="CAR001_selected_v09." + kind)).encode("utf-8")
        request = Request("http://127.0.0.1:8765/api/perception/export", data=body,
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(request, timeout=10) as response:
            assert response.status == 200
            assert "attachment" in response.headers["Content-Disposition"]
            raw = response.read()
            mime = response.headers["Content-Type"]
        if kind == "svg":
            document = ET.fromstring(raw)
            returned = json.loads(document.find("{" + NS + "}metadata").text)
            assert any(element.attrib.get("stroke") == "#a3e8f1" for element in document.iter())
            assert "Kijelölt csatorna" in " ".join(document.itertext())
        else:
            returned = json.loads(raw)
        assert returned == metadata
        target = ROOT / ("results/ui/selected-http-frame-v09." + kind)
        target.write_bytes(raw)
        outputs.append(dict(format=kind, bytes=len(raw), content_type=mime,
                            sha256=hashlib.sha256(raw).hexdigest(), artifact=target.relative_to(ROOT).as_posix()))
    report = dict(status="PASS", executed_at_utc=datetime.now(timezone.utc).isoformat(),
                  scope="Actual browser DOM geometry rewrapped by test and echoed as local HTTP attachment; not native browser download confirmation",
                  run_id=snapshot["run"], telemetry_id=row["id"], selected_channel_id="rear_center",
                  sensors_exact=True, control_exact=True, outputs=outputs)
    (ROOT / "results/ui/selected-frame-http-check-v09.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
