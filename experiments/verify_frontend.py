"""Execute JS evidence checks and compare pure analysis with a received MQTT archive."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from factory_yard import __version__

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="results/ui/perception-renderer-check-v18.json")
    args = parser.parse_args()
    cases = []
    names = ("perception_visuals_smoke.cjs", "perception_analysis_smoke.cjs",
             "frontend_regressions.cjs", "frontend_navigation_v09.cjs", "frontend_replay_v10.cjs",
             "frontend_mission_v11.cjs", "frontend_transport_v12.cjs", "frontend_capacity_v13.cjs",
             "frontend_transport_archive_v14.cjs", "frontend_transport_context_v15.cjs", "frontend_operations_v16.cjs",
             "frontend_operations_report_v17.cjs", "frontend_operations_search_v18.cjs")
    paths = [ROOT / "web" / name for name in
             ("app.js", "index.html", "style.css", "perception.css", "perception.js", "perception-analysis.js")]
    paths += [ROOT / "tests" / name for name in names]
    paths.append(Path(__file__).resolve())
    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths}
    tested_sources = hashes()
    for name in names:
        command = ["node", "tests/" + name]
        result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8",
                                capture_output=True, check=True)
        count = len(re.findall(r"^PASS (?!\d+ loaded-frame)", result.stdout, re.M))
        cases.append(dict(command=" ".join(command), status="PASS", cases=count,
                          output=result.stdout))
    summary_path = sorted((ROOT / "results").glob("perception-smoke-*/summary.json"))[-1]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["status"] != "PASS" or summary.get("model_version") != __version__:
        raise RuntimeError("Fresh current-version MQTT evidence is required")
    archive_path = Path(summary["json_export"])
    script = """const fs=require('node:fs');require('./web/perception-analysis.js');
const doc=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
process.stdout.write(JSON.stringify(YardPerceptionAnalysis.summarizeFrames(doc.telemetry)));"""
    result = subprocess.run(["node", "-e", script, str(archive_path)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=True)
    analysis = json.loads(result.stdout)
    archived = summary["perception_summary"]
    assert analysis["frames"]["received"] == summary["trip_samples"]
    assert analysis["frames"]["stale"] == archived["perception_stale_frames"]
    assert analysis["samples"]["distinct"] == archived["perception_distinct_samples"]
    assert analysis["frames"]["fault_active"] == summary["diagnostic_frames"]
    assert analysis["proximity"]["invalid_channels"] == 0
    if tested_sources != hashes():
        raise RuntimeError("Frontend sources changed during verification")
    report = dict(status="PASS", model_version=__version__, executed_at_utc=datetime.now(timezone.utc).isoformat(),
                  checks=cases, total_cases=sum(case["cases"] for case in cases),
                  mqtt_archive=dict(run_id=summary["run_id"], source=str(archive_path),
                                    input_scope="complete fixed received-car JSON export, offline analysis",
                                    source_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                                    analysis=analysis),
                  source_sha256=tested_sources, sources_unchanged_during_run=True)
    target = (ROOT / args.output).resolve()
    if not target.is_relative_to(ROOT / "results"):
        raise ValueError("Verification output must stay within the project results folder")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(status="PASS", cases=report["total_cases"],
                         received=analysis["frames"]["received"], stale=analysis["frames"]["stale"],
                         distinct=analysis["samples"]["distinct"], artifact=str(target))))


if __name__ == "__main__":
    main()
