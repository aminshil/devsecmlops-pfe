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
     what is actually deployed;
  4. that threshold was SELECTED for exactly these artifacts on an independent
     validation fleet: the manifest carries the selection record, its artifact
     SHA-256s equal the promoted artifacts', and the validation fleet is not the
     test set. (Checks 3 and 4 are part of the deployment check, which tests
     outside the repository skip with --skip-deploy-check.)

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


# every place the serving model selector is configured. telecom_v3 is the
# selector that enables the hybrid path: v4 when history is supplied, v3
# otherwise (api/app.py). Any other value silently serves a different model.
MODEL_NAME_SOURCES = [
    ("kubernetes/deployment.yaml", r'name:\s*MODEL_NAME\s*\n\s*value:\s*"([^"]+)"'),
    ("ansible/roles/kubernetes/templates/deployment.yaml.j2", r'name:\s*MODEL_NAME\s*\n\s*value:\s*"([^"]+)"'),
    ("Dockerfile", r'ENV\s+MODEL_NAME=(\S+)'),
    ("api/app.py", r'os\.environ\.get\("MODEL_NAME",\s*"([^"]+)"\)'),
    ("ansible/group_vars/all.yml", r'cp_model_name:\s*"([^"]+)"'),
]


def check_model_name(prod: dict, root: Path) -> tuple[str, list[str], list[str]]:
    expected = (prod.get("serving") or {}).get("model_name", "telecom_v3")
    problems, checked = [], []
    for rel, pattern in MODEL_NAME_SOURCES:
        path = root / rel
        if not path.exists():
            continue
        m = re.search(pattern, path.read_text())
        if not m:
            continue
        checked.append(rel)
        if m.group(1) != expected:
            problems.append(f"{rel}: MODEL_NAME={m.group(1)}, expected {expected}")
    return expected, checked, problems


def deployed_thresholds(root: Path) -> dict:
    found = {}
    for rel, pattern in THRESHOLD_SOURCES:
        path = root / rel
        if path.exists():
            m = re.search(pattern, path.read_text())
            found[rel] = float(m.group(1)) if m else None
    return found


def _selection_problems(prod: dict) -> list[str]:
    """The threshold must have been selected for exactly these artifacts, on a
    validation fleet that is not the test set."""
    ev = prod.get("evaluation") or {}
    selected = prod.get("decision_threshold")
    sel = prod.get("threshold_selection") or {}
    if selected is None or not sel:
        return ["no recorded threshold selection: the threshold must be selected on an independent "
                "validation fleet for exactly these artifacts "
                "(ml-model/select_threshold.py --record, or train_production.py --promote)"]
    val_sha = (sel.get("validation_set") or {}).get("sha256")
    test_sha = (ev.get("test_set") or {}).get("sha256")
    checks = [
        (ev.get("threshold") != selected,
         f"evaluation recorded at {ev.get('threshold')}, but the selected threshold is {selected}"),
        ((sel.get("selected") or {}).get("threshold") != selected,
         "the selection evidence names a different threshold than decision_threshold"),
        (sel.get("artifacts") != prod.get("artifacts"),
         "the threshold was selected for different artifacts than the promoted ones "
         "(re-run the selection for the promoted model)"),
        (val_sha is not None and val_sha == test_sha,
         "the validation fleet used to select the threshold is the test set"),
    ]
    return [msg for bad, msg in checks if bad]


def check_threshold(prod: dict, root: Path) -> list[str]:
    evaluated = (prod.get("evaluation") or {}).get("threshold")
    problems = _selection_problems(prod)
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
        expected, checked, mproblems = check_model_name(prod, a.root)
        if mproblems:
            print("FAIL: model selector drift:\n  " + "\n  ".join(mproblems))
            return 1
        print(f"OK: MODEL_NAME={expected} in {len(checked)} deployment files ({', '.join(checked)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
