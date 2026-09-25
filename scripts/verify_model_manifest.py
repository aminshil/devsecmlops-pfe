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

  3. the threshold the evaluation was recorded at is the selected one
     (production.decision_threshold) and is exactly the value every
     deployment file configures -- so the F1 the gate checks is the F1 of
     what is actually deployed.

Exit 0 = pass, 1 = fail (with the reason printed).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# every place the serving threshold is configured, and how to read it
THRESHOLD_SOURCES = [
    ("kubernetes/deployment.yaml", r'name:\s*PREDICT_THRESHOLD\s*\n\s*value:\s*"([^"]+)"'),
    ("ansible/roles/kubernetes/templates/deployment.yaml.j2", r'name:\s*PREDICT_THRESHOLD\s*\n\s*value:\s*"([^"]+)"'),
    ("ansible/group_vars/all.yml", r'cp_predict_threshold:\s*"([^"]+)"'),
    ("ansible/group_vars/demo.yml", r'cp_predict_threshold:\s*"([^"]+)"'),
    ("api/app.py", r'os\.environ\.get\("PREDICT_THRESHOLD",\s*"([^"]+)"\)'),
]


def deployed_thresholds(root: Path) -> dict:
    found = {}
    for rel, pattern in THRESHOLD_SOURCES:
        path = root / rel
        if path.exists():
            m = re.search(pattern, path.read_text())
            found[rel] = float(m.group(1)) if m else None
    return found


def check_threshold(prod: dict, root: Path) -> list[str]:
    evaluated = (prod.get("evaluation") or {}).get("threshold")
    problems = []
    selected = prod.get("decision_threshold")
    if selected is not None and evaluated != selected:
        problems.append(f"evaluation recorded at {evaluated}, but the selected threshold is {selected}")
    for rel, value in deployed_thresholds(root).items():
        if value is None:
            problems.append(f"{rel}: PREDICT_THRESHOLD not found")
        elif value != evaluated:
            problems.append(f"{rel}: deploys {value}, but the model was evaluated at {evaluated}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-f1", type=float, required=True)
    ap.add_argument("--manifest", type=Path, default=ROOT / "models" / "manifest.json")
    ap.add_argument("--root", type=Path, default=ROOT,
                    help="repository root holding the deployment files")
    ap.add_argument("--skip-deploy-check", action="store_true",
                    help="only for testing a manifest outside the repository")
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
    if not a.skip_deploy_check:
        problems = check_threshold(prod, a.root)
        if problems:
            print("FAIL: threshold drift:\n  " + "\n  ".join(problems))
            return 1
        print(f"OK: deployed threshold {ev.get('threshold')} = evaluated threshold"
              + (" = selected threshold" if prod.get("decision_threshold") is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
