"""Compare native browser JSON/SVG downloads with their archived MQTT frame.

Example: python -m experiments.check_frame_download --json <file> --svg <file>
         --name parkoloszenzor_v07
The browser downloads must already exist; this command does not produce them.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--svg", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", args.name):
        parser.error("A mentési név csak betűt, számot, aláhúzást és kötőjelet tartalmazhat")
    frame = json.loads(args.json.read_text(encoding="utf-8"))
    assert frame["view_context"]["archived"], "Az egyezésvizsgálathoz a történeti nézetből letöltött keret szükséges"
    document = ET.fromstring(args.svg.read_bytes())
    svg_frame = json.loads(document.findtext("{http://www.w3.org/2000/svg}metadata"))
    assert frame == svg_frame, "A JSON és az SVG metaadata eltér"
    query = urlencode(dict(run_id=frame["run_id"], car_id=frame["car_id"], format="json"))
    with urlopen(args.url.rstrip("/") + "/api/car/history/export?" + query, timeout=30) as response:
        archive = json.load(response)
    candidates = [point for point in archive["telemetry"]
                  if point["sim_time"] == frame["telemetry_sim_time"] and point["utc"] == frame["utc"]]
    assert len(candidates) == 1, "Nem egyértelmű az archivált keret"
    point = candidates[0]
    assert point["sensors"] == frame["sensors"], "Az érzékelőadat nem egyezik az archívummal"
    assert point["control"] == frame["control"], "A vezérlési döntés nem egyezik az archívummal"
    proximity = frame["sensors"]["proximity"]
    assert proximity["source"] == "virtual_bumper_ray_fan" and len(proximity["channels"]) == 8
    assert proximity["sample_sim_time_s"] == frame["sensors"]["sample_sim_time_s"]
    output = ROOT / "results/ui"
    paths = {}
    for kind, source in (("json", args.json), ("svg", args.svg)):
        target = output / f"{args.name}.{kind}"
        shutil.copyfile(source, target)
        paths[kind] = str(target.relative_to(ROOT)).replace("\\", "/")
    report = dict(status="PASS", executed_at_utc=datetime.now(timezone.utc).isoformat(),
                  method="Native browser downloads compared exactly with archived MQTT record",
                  run_id=frame["run_id"], car_id=frame["car_id"],
                  telemetry_sim_time=frame["telemetry_sim_time"],
                  sensor_sample_sim_time_s=proximity["sample_sim_time_s"],
                  controller_sim_time_s=frame["control"]["decision_sim_time_s"],
                  source=proximity["source"], channels=len(proximity["channels"]),
                  fresh_in_view=frame["view_context"]["fresh_in_view"], files=paths,
                  sha256={kind: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for kind, path in paths.items()})
    (output / f"{args.name}-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
