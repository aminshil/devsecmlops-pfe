#!/usr/bin/env python3
"""Production (trained on v2) versus a candidate trained on v3, read from the files the runs wrote:

  A  production on the v2 test fleet    models/manifest.json  production.evaluation
  B  production on the v3 test fleet    <report>              production_evaluation
  C  v3 candidate on the v3 test fleet  <report>              candidate_evaluation

B versus C is the fair comparison: same test fleet, both trained on full-size fleets. A is the
reference the README reports today; it is scored on a different (v2) test fleet.

    python3 scripts/compare_v2_v3.py --report build/v3/report.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fmt(x):
    return "—" if x is None else f"{x:.4f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=ROOT / "models" / "manifest.json")
    ap.add_argument("--report", type=Path, default=ROOT / "build" / "v3" / "report.json")
    args = ap.parse_args()
    for p in (args.manifest, args.report):
        if not p.exists():
            print(f"missing {p}", file=sys.stderr)
            return 1
    man = json.loads(args.manifest.read_text())
    rep = json.loads(args.report.read_text())
    prod_thr = man["production"].get("decision_threshold")
    rows = [("A production on v2 test", man["production"]["evaluation"], prod_thr),
            ("B production on v3 test", rep["production_evaluation"], prod_thr),
            ("C v3 candidate on v3 test", rep["candidate_evaluation"], rep["params"].get("threshold"))]

    for path in ("v4", "v3"):
        print(f"\n== served decision, {path} path ==")
        print(f"{'':<27} {'thr':>5} {'F1':>7} {'prec':>7} {'recall':>7} | {'clf F1':>7} {'clf P':>7} "
              f"{'clf R':>7} | {'cause acc':>9}")
        for name, ev, thr in rows:
            e = ev.get(path, {})
            print(f"{name:<27} {thr if thr is not None else '—':>5} {fmt(e.get('f1')):>7} "
                  f"{fmt(e.get('precision')):>7} {fmt(e.get('recall')):>7} | {fmt(e.get('classifier_only_f1')):>7} "
                  f"{fmt(e.get('classifier_only_precision')):>7} {fmt(e.get('classifier_only_recall')):>7} | "
                  f"{fmt(e.get('cause_accuracy')):>9}")

    print("\n== per-cause recall, v4 path ==")
    causes = sorted({c for _, ev, _ in rows for c in (ev.get("v4", {}).get("per_cause_recall") or {})})
    print(f"{'cause':<22}" + "".join(f"{n.split()[0]:>10}" for n, _, _ in rows))
    for c in causes:
        print(f"{c:<22}" + "".join(f"{fmt((ev.get('v4', {}).get('per_cause_recall') or {}).get(c)):>10}"
                                   for _, ev, _ in rows))

    b, c = rep["production_evaluation"]["v4"], rep["candidate_evaluation"]["v4"]
    print("\n== fair comparison (B vs C: same v3 test fleet) ==")
    for k in ("f1", "precision", "recall"):
        print(f"  {k:<9} production {fmt(b.get(k))}   v3 candidate {fmt(c.get(k))}   "
              f"difference {c.get(k, 0) - b.get(k, 0):+.4f}")
    print(f"\nguardrail on the {rep.get('guardrail_set', '?')} fleet: "
          f"{'PASSED' if rep.get('guardrail_passed') else 'REJECTED'}")
    for reason in (rep.get("guardrail_reasons") or [])[:10]:
        print(f"  - {reason}")
    rows_train = (rep.get("datasets", {}).get("train") or {}).get("rows")
    if rows_train:
        print(f"\ncandidate trained on {rows_train:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
