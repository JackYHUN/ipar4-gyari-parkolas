"""Execute the unit suite and preserve the actual runner outcome, never a preset count."""
from datetime import datetime, timezone
import io
import hashlib
import json
from pathlib import Path
import sys
import unittest

from factory_yard import __version__

ROOT = Path(__file__).resolve().parent


def main():
    paths = sorted(path for folder in ("factory_yard", "tests") for path in (ROOT / folder).rglob("*.py")
                   if "__pycache__" not in path.parts)
    def hashes():
        return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    tested_sources = hashes()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = ROOT / "results"
    output.mkdir(exist_ok=True)
    (output / "unit-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    sources_unchanged = tested_sources == hashes()
    report = dict(executed_at_utc=datetime.now(timezone.utc).isoformat(), model_version=__version__,
                  command="python verify_units.py (unittest discovery)", tests_run=result.testsRun,
                  passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
                  failed=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
                  status="PASS" if result.wasSuccessful() and sources_unchanged else "FAIL", output="results/unit-tests.txt",
                  source_sha256=tested_sources, sources_unchanged_during_run=sources_unchanged)
    (output / "unit-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(stream.getvalue())
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
