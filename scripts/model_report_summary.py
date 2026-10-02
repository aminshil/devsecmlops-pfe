#!/usr/bin/env python3
"""Print the headline facts of a train_production.py --report file, for the Jenkins model job log.

Usage: python3 scripts/model_report_summary.py build/model-report.json
Standard library only. Exit 0 always when the file parses; exit 2 when it is missing or malformed.
"""
import json
import sys
from pathlib import Path


def _f(ev, key):
    return ((ev or {}).get(key) or {})


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        r = json.loads(Path(argv[1]).read_text())
    except (OSError, ValueError) as e:
        print(f"cannot read the report: {e}")
        return 2
    ds = r.get("datasets") or {}
    for name in ("train", "test"):
        d = ds.get(name) or {}
        print(f"{name:5}: {d.get('file')}  rows={d.get('rows')}  sha256={str(d.get('sha256'))[:12]}")
    fb = [k for k in ds if k.startswith("feedback")]
    print(f"feedback files: {len(fb)}  usage: {r.get('feedback')}")
    cand, prod = r.get("candidate_evaluation"), r.get("production_evaluation")
    v4 = _f(cand, "v4")
    print(f"candidate (threshold {(r.get('params') or {}).get('threshold')}): "
          f"v4 F1={v4.get('f1')} P={v4.get('precision')} R={v4.get('recall')}   v3 F1={_f(cand, 'v3').get('f1')}")
    if prod:
        print(f"production                : v4 F1={_f(prod, 'v4').get('f1')}   v3 F1={_f(prod, 'v3').get('f1')}")
    sel = (r.get("threshold_selection") or {}).get("selected")
    if sel:
        print(f"threshold selected on the validation fleet: {sel.get('threshold')} (F2 {sel.get('f2')})")
    verdict = "PASS" if r.get("guardrail_passed") else "REJECT"
    print(f"guardrail: {verdict}  {'; '.join(r.get('guardrail_reasons') or [])}")
    print(f"promoted to workspace: {r.get('promoted')}   mlflow run: {r.get('mlflow_run_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
