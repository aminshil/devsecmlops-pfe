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
     (production.decision_threshold), and no deployment file carries its own
     copy: the API reads production.decision_threshold from this manifest
     (shipped inside the image), so the F1 the gate checks is the F1 of what is
     actually deployed and a new model's threshold needs no manual edit;
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


# Places where a static serving threshold used to be configured, and how it looked. None may
# carry one any more: the API reads the threshold the pipeline selected from the manifest.
STATIC_THRESHOLD_PATTERNS = [
    ("kubernetes/deployment.yaml", r'name:\s*PREDICT_THRESHOLD'),
    ("ansible/roles/kubernetes/templates/deployment.yaml.j2", r'name:\s*PREDICT_THRESHOLD'),
    ("ansible/group_vars/all.yml", r'cp_predict_threshold\s*:'),
    ("ansible/group_vars/demo.yml", r'cp_predict_threshold\s*:'),
    ("ansible/roles/control_panel/tasks/main.yml", r'Environment=PREDICT_THRESHOLD'),
    ("start_control_panel.sh", r'PREDICT_THRESHOLD\s*='),
    ("api/app.py", r'os\.environ\.get\("PREDICT_THRESHOLD",\s*"[^"]+"\)'),
]


# every place the serving model selector is configured. telecom_v3 is the
# selector that enables the hybrid path: v4 when history is supplied, v3
# otherwise (api/app.py). Any other value silently serves a different model.
# (file, pattern that reads the value, marker that says the file sets MODEL_NAME at all). A file that
# sets it in a form the pattern cannot read is a failure, not a skip: otherwise reformatting the value
# (no quotes, another spacing) would take the file out of the check (external review, 27 October).
MODEL_NAME_SOURCES = [
    ("kubernetes/deployment.yaml", r'name:\s*MODEL_NAME\s*\n\s*value:\s*"([^"]+)"', r'name:\s*MODEL_NAME\b'),
    ("ansible/roles/kubernetes/templates/deployment.yaml.j2", r'name:\s*MODEL_NAME\s*\n\s*value:\s*"([^"]+)"',
     r'name:\s*MODEL_NAME\b'),
    ("Dockerfile", r'ENV\s+MODEL_NAME=(\S+)', r'^\s*ENV\b[^\n]*\bMODEL_NAME\b'),
    ("api/app.py", r'os\.environ\.get\("MODEL_NAME",\s*"([^"]+)"\)', r'environ[^\n]*MODEL_NAME'),
    ("ansible/group_vars/all.yml", r'^\s*cp_model_name:\s*"?([A-Za-z0-9_]+)"?\s*$', r'^\s*cp_model_name\s*:'),
    ("ansible/group_vars/demo.yml", r'^\s*cp_model_name:\s*"?([A-Za-z0-9_]+)"?\s*$', r'^\s*cp_model_name\s*:'),
]


def check_model_name(prod: dict, root: Path) -> tuple[str, list[str], list[str]]:
    expected = (prod.get("serving") or {}).get("model_name", "telecom_v3")
    problems, checked = [], []
    for rel, pattern, marker in MODEL_NAME_SOURCES:
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text()
        m = re.search(pattern, text, re.M)
        if not m:
            if re.search(marker, text, re.M):
                problems.append(f"{rel}: sets MODEL_NAME in a form this check cannot read")
            continue
        checked.append(rel)
        if m.group(1) != expected:
            problems.append(f"{rel}: MODEL_NAME={m.group(1)}, expected {expected}")
    return expected, checked, problems


def static_thresholds(root: Path) -> list[str]:
    """Files under root that hard-code a serving threshold."""
    found = []
    for rel, pattern in STATIC_THRESHOLD_PATTERNS:
        path = root / rel
        if path.exists() and re.search(pattern, path.read_text()):
            found.append(rel)
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
    problems = _selection_problems(prod)
    for rel in static_thresholds(root):
        problems.append(f"{rel}: hard-codes the serving threshold; remove it -- the API reads "
                        f"production.decision_threshold from models/manifest.json")
    app = root / "api" / "app.py"
    if app.exists() and "decision_threshold" not in app.read_text():
        problems.append("api/app.py does not read production.decision_threshold from the manifest")
    return problems


def artifact_problems(arts: dict, models_dir: Path) -> list[str]:
    """The manifest's artifacts that are missing or differ from their recorded SHA-256."""
    bad = []
    for rel, expected in arts.items():
        p = models_dir / Path(rel).name
        if not p.exists():
            bad.append(f"{rel}: missing")
        elif sha256(p) != expected:
            bad.append(f"{rel}: hash mismatch")
    return bad


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
    bad = artifact_problems(arts, a.manifest.resolve().parent)
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
        print(f"OK: serving threshold {ev.get('threshold')} = evaluated = selected, read from the "
              f"manifest (no deployment file carries a copy)")
        expected, checked, mproblems = check_model_name(prod, a.root)
        if mproblems:
            print("FAIL: model selector drift:\n  " + "\n  ".join(mproblems))
            return 1
        print(f"OK: MODEL_NAME={expected} in {len(checked)} deployment files ({', '.join(checked)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
