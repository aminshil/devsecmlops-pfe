"""compare_v2_v3.py reads production from the manifest and both sides of the v3 run from its report."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "compare_v2_v3.py"


def _ev(f1, causes):
    return {"v4": {"f1": f1, "precision": f1 - 0.05, "recall": f1 + 0.05, "per_cause_recall": causes},
            "v3": {"f1": f1 - 0.05}}


def test_comparison_prints_the_three_rows_and_the_fair_difference(tmp_path):
    man = tmp_path / "manifest.json"
    rep = tmp_path / "report.json"
    man.write_text(json.dumps({"production": {"decision_threshold": 0.91,
                                              "evaluation": _ev(0.7291, {"cascade": 0.27})}}))
    rep.write_text(json.dumps({"production_evaluation": _ev(0.80, {"cascade_gray": 0.09}),
                               "candidate_evaluation": _ev(0.85, {"cascade_gray": 0.30}),
                               "params": {"threshold": 0.5}, "guardrail_passed": True,
                               "guardrail_set": "validation", "guardrail_reasons": [],
                               "datasets": {"train": {"rows": 17280000}}}))
    out = subprocess.run([sys.executable, str(SCRIPT), "--manifest", str(man), "--report", str(rep)],
                         check=True, capture_output=True, text=True).stdout
    assert "A production on v2 test" in out and "C v3 candidate on v3 test" in out
    assert "difference +0.0500" in out and "PASSED" in out
    assert "cascade_gray" in out and "cascade" in out and "17,280,000" in out


def test_missing_report_is_a_clear_error(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), "--report", str(tmp_path / "nope.json")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "missing" in r.stderr
