"""Verify archive decision navigation separately from frozen course evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = ["frontend_braking_analysis.cjs", "frontend_braking_panel.cjs",
             "frontend_decision_window.cjs", "frontend_decision_navigator.cjs"]
    sources = list((ROOT / "web").glob("*")) + [ROOT / "tests" / name for name in names]
    sources += [ROOT / "tests" / f"frontend_{name}.cjs" for name in
                ("navigation_v09", "replay_v10", "mission_v11", "transport_context_v15")]
    sources += [Path(__file__).resolve(), ROOT / "experiments/verify_frontend.py"]

    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sources if path.is_file()}

    before = hashes()
    base_path = ROOT / "results/ui/decision-window-regressions.json"
    subprocess.run([sys.executable, "-m", "experiments.verify_frontend", "--output",
                    str(base_path.relative_to(ROOT))], cwd=ROOT, check=True,
                   capture_output=True, text=True, encoding="utf-8")
    base = json.loads(base_path.read_text(encoding="utf-8"))
    checks = []
    for name in names:
        result = subprocess.run(["node", "tests/" + name], cwd=ROOT, check=True,
                                capture_output=True, text=True, encoding="utf-8")
        checks.append(dict(command="node tests/" + name, status="PASS",
                           cases=len(re.findall(r"^PASS (?!\d+ )", result.stdout, re.M)),
                           output=result.stdout))
    archive = Path(base["mqtt_archive"]["source"])
    script = r"""const fs=require('node:fs');require('./web/perception-analysis.js');
const doc=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const rows=doc.telemetry.map(row=>({...row,
sensors:Object.hasOwn(row,'sensors')?row.sensors:row.details?.sensors,
control:Object.hasOwn(row,'control')?row.control:row.details?.control}));
const windows=[];
for(let start=0;start<rows.length;start+=2000){
const result=YardBrakingAnalysis.summarizeWindow(rows.slice(start,start+2000),{sample_period_s:.1});
windows.push({start_index:start,...result});}
process.stdout.write(JSON.stringify({frames:rows.length,windows}));"""
    received = subprocess.run(["node", "-e", script, str(archive)], cwd=ROOT, check=True,
                              capture_output=True, text=True, encoding="utf-8")
    observed = json.loads(received.stdout)
    assert observed["frames"] > 0
    assert sum(window["frame_count"] for window in observed["windows"]) == observed["frames"]
    for window in observed["windows"]:
        assert window["frame_count"] <= 2000 and window["scope"] == "loaded_received_window"
        assert window["inconsistent_decisions"] == 0
    assert before == hashes(), "Sources changed during verification"
    report = dict(status="PASS", feature="received_decision_window_v1",
                  verified_at_utc=datetime.now(timezone.utc).isoformat(),
                  regression_cases=base["total_cases"], checks=checks,
                  total_cases=base["total_cases"] + sum(check["cases"] for check in checks),
                  source_sha256=before, sources_unchanged_during_run=True,
                  base_report_sha256=hashlib.sha256(base_path.read_bytes()).hexdigest(),
                  mqtt_archive=dict(sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                                    scope="Offline windows of previously received MQTT frames; no new broker campaign",
                                    **observed),
                  limitations=["Each summary covers only its own loaded receipt window.",
                               "Disconnected braking sections are observations, not total-trip event counts or durations.",
                               "Moving model reserve does not establish physical safety.",
                               "Browser rendering and native exact-row navigation are verified separately."])
    output = ROOT / "results/ui/decision-window-check.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(status=report["status"], cases=report["total_cases"],
                         received_frames=observed["frames"], windows=len(observed["windows"]),
                         artifact=str(output))))


if __name__ == "__main__":
    main()
