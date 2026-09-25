#!/usr/bin/env python3
"""
CI model gate (Jenkins stage "Model gate").

Refuses the build unless:
  1. every production artifact listed in models/manifest.json exists and its
     SHA-256 matches the manifest (the artifacts being baked are exactly the
     ones the training pipeline evaluated and promoted);
  2. the manifest carries an evaluation of those artifacts on the independent
     test set, and the served v4 decision's F1 at the production threshold is
     at least --min-f1.

Exit 0 = pass, 1 = fail (with the reason printed).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-f1", type=float, required=True)
    ap.add_argument("--manifest", type=Path, default=ROOT / "models" / "manifest.json")
    a = ap.parse_args()

    prod = json.load(open(a.manifest)).get("production") or {}
    arts = prod.get("artifacts") or {}
    if not arts:
        print("FAIL: manifest has no production artifacts")
        return 1
    bad = []
    models_dir = a.manifest.resolve().parent
    for rel, expected in arts.items():
        p = models_dir / Path(rel).name
        if not p.exists():
            bad.append(f"{rel}: missing")
        elif sha256(p) != expected:
            bad.append(f"{rel}: hash mismatch")
    if bad:
        print("FAIL: artifacts differ from the promoted model:\n  " + "\n  ".join(bad))
        return 1
    print(f"OK: {len(arts)} artifacts match the manifest")

    ev = prod.get("evaluation") or {}
    v4 = ev.get("v4")
    if not v4:
        print("FAIL: no recorded evaluation for these artifacts "
              "(run: python ml-model/train_production.py --evaluate-production)")
        return 1
    test = ev.get("test_set", {})
    print(f"served v4 on {test.get('file')} (sha256 {str(test.get('sha256'))[:12]}, "
          f"threshold {ev.get('threshold')}): F1={v4['f1']} P={v4['precision']} R={v4['recall']}")
    if v4["f1"] < a.min_f1:
        print(f"FAIL: F1 {v4['f1']} < required {a.min_f1}")
        return 1
    print(f"OK: F1 {v4['f1']} >= {a.min_f1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
