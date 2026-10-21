#!/usr/bin/env python3
"""Precision-recall curves of production and of a v3-trained candidate on the SAME validation fleet.

The training run sweeps only the candidate (the 'curve' in its report, used to pick its threshold).
This sweeps production over the same grid with the same function (train_production.served_curve,
served decision = classifier OR IsolationForest, v4 path), then compares the two curves at equal
recall: whichever is more precise at the same recall is the better detector there, whatever
threshold the policy happens to pick. Read-only apart from the production curve it saves.

    python3 scripts/compare_curves.py                 # sweeps production (a few minutes)
    python3 scripts/compare_curves.py --reuse         # reuses build/v3/production_curve.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))


def best_precision(curve, target):
    ok = [x for x in curve if x["recall"] >= target]
    return max(ok, key=lambda x: x["precision"]) if ok else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--val", type=Path, default=ROOT / "data" / "telecom_fleet_v3_val.csv")
    ap.add_argument("--report", type=Path, default=ROOT / "build" / "v3" / "report.json")
    ap.add_argument("--models", type=Path, default=ROOT / "models")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "v3" / "production_curve.json")
    ap.add_argument("--reuse", action="store_true", help="load --out instead of sweeping again")
    args = ap.parse_args()

    if not args.report.exists():
        print(f"missing {args.report}", file=sys.stderr)
        return 1
    rep = json.loads(args.report.read_text())
    cand = rep["threshold_selection"]["curve"]
    if args.reuse and args.out.exists():
        prod = json.loads(args.out.read_text())
        print(f"reusing {args.out}")
    else:
        import model_policy
        import train_production as tp
        pol = model_policy.load_policy()
        grid = model_policy.threshold_grid(pol)
        print(f"sweeping production on {args.val.name}: {len(grid)} thresholds, {grid[0]} -> {grid[-1]}", flush=True)
        prod = tp.served_curve(args.models, tp.load_fleet(args.val), grid, pol["threshold"]["beta"])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(prod, indent=1))
    if [round(p["threshold"], 4) for p in prod] != [round(c["threshold"], 4) for c in cand]:
        print("the two curves use different threshold grids", file=sys.stderr)
        return 1

    marks = {round(rep["params"]["threshold"], 2), 0.91}
    print(f"\n{'thr':>5} | {'production  P':>13} {'R':>7} {'F1':>7} | {'v3 candidate  P':>15} {'R':>7} {'F1':>7}")
    for p, c in zip(prod, cand):
        if round(p["threshold"] * 100) % 5 == 0 or round(p["threshold"], 2) in marks:
            print(f"{p['threshold']:>5.2f} | {p['precision']:>13.4f} {p['recall']:>7.4f} {p['f1']:>7.4f} | "
                  f"{c['precision']:>15.4f} {c['recall']:>7.4f} {c['f1']:>7.4f}")
    bp, bc = max(prod, key=lambda x: x["f1"]), max(cand, key=lambda x: x["f1"])
    print(f"\nbest F1 on each curve: production {bp['f1']:.4f} at {bp['threshold']:.2f} | "
          f"candidate {bc['f1']:.4f} at {bc['threshold']:.2f}")
    print(f"highest recall reached: production {max(x['recall'] for x in prod):.4f} | "
          f"candidate {max(x['recall'] for x in cand):.4f}")
    s = lambda x: f"P {x['precision']:.4f} (thr {x['threshold']:.2f})" if x else "never reaches it"
    for target in (0.80, 0.85, 0.90):
        print(f"at recall >= {target:.2f}: production {s(best_precision(prod, target))} | "
              f"candidate {s(best_precision(cand, target))}")
    cross = None
    for k in range(80, 96):
        t = k / 100
        fp, fc = best_precision(prod, t), best_precision(cand, t)
        if fc and (not fp or fc["precision"] > fp["precision"]):
            cross = t
            break
    if cross is None:
        print("the candidate is never more precise than production at equal recall (0.80-0.95)")
    else:
        print(f"the candidate becomes the more precise detector from recall {cross:.2f} upwards; "
              f"below that, production is")
    return 0


if __name__ == "__main__":
    sys.exit(main())
