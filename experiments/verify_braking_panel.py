"""Verify the passive braking view against regressions and saved MQTT frames."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    sources = sorted((ROOT / "web").glob("*")) + [
        ROOT / "tests/frontend_braking_analysis.cjs", ROOT / "tests/frontend_braking_panel.cjs",
        Path(__file__).resolve(), ROOT / "experiments/verify_frontend.py",
    ]
    def hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sources if p.is_file()}
    before = hashes()
    subprocess.run([sys.executable, "-m", "experiments.verify_frontend", "--output",
                    "results/ui/braking-regression-base.json"], cwd=ROOT, check=True,
                   capture_output=True, text=True, encoding="utf-8")
    baseline_path = ROOT / "results/ui/braking-regression-base.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    checks = []
    for name in ("frontend_braking_analysis.cjs", "frontend_braking_panel.cjs"):
        result = subprocess.run(["node", "tests/" + name], cwd=ROOT, check=True,
                                capture_output=True, text=True, encoding="utf-8")
        checks.append(dict(command="node tests/" + name, status="PASS",
                           cases=len(re.findall(r"^PASS (?!\d+ received-braking)", result.stdout, re.M)), output=result.stdout))
    archive_path = Path(baseline["mqtt_archive"]["source"])
    script = r"""const fs=require('node:fs');require('./web/perception-analysis.js');
const doc=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const result={frames:doc.telemetry.length,available:0,inconsistent:0,missing:0,phases:{},freshness:{}};
for(const row of doc.telemetry){const sensors=Object.hasOwn(row,'sensors')?row.sensors:row.details?.sensors;
const control=Object.hasOwn(row,'control')?row.control:row.details?.control;
const data={sensors,control,telemetry_sim_time:row.sim_time,fresh:sensors?.fresh===true&&sensors?.fault_active!==true};
const r=YardBrakingAnalysis.analyzeFrame(data);
if(!r.available){result.missing++;continue;}result.available++;
if(r.consistency!=='consistent')result.inconsistent++;
result.phases[r.phase]=(result.phases[r.phase]||0)+1;
result.freshness[r.freshness]=(result.freshness[r.freshness]||0)+1;
}process.stdout.write(JSON.stringify(result));"""
    result = subprocess.run(["node", "-e", script, str(archive_path)], cwd=ROOT, check=True,
                            capture_output=True, text=True, encoding="utf-8")
    archive_check = json.loads(result.stdout)
    assert archive_check["available"] > 0 and archive_check["inconsistent"] == 0
    assert before == hashes(), "Frontend sources changed during verification"
    report = dict(status="PASS", feature="received_braking_decision_v1",
                  verified_at_utc=datetime.now(timezone.utc).isoformat(), model_version="0.18.0",
                  regression_cases=baseline["total_cases"], checks=checks,
                  new_cases=sum(c["cases"] for c in checks),
                  source_sha256=before, sources_unchanged_during_run=True,
                  base_report_sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                  mqtt_archive=dict(path=str(archive_path), sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                                    scope="Offline analysis of previously received MQTT frames; no new broker campaign", **archive_check),
                  limitations=["The route limit already contains controller margins; reaction distance is not subtracted twice.",
                               "A positive model reserve is not a physical safety guarantee.",
                               "Browser rendering is verified separately; historical course results retain their original dates and source scope."])
    path = ROOT / "results/ui/braking-panel-check.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(status="PASS", regressions=report["regression_cases"],
                         new_cases=report["new_cases"], recorded_frames=archive_check["frames"],
                         available=archive_check["available"], artifact=str(path))))


if __name__ == "__main__":
    main()
