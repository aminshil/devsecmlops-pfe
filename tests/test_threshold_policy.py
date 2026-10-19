"""One policy file (config/model_policy.json) drives training size and threshold selection;
the API reads the selected threshold from the manifest, so no deployment file carries one."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("MODEL_NAME", "telecom_v3")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@127.0.0.1:9/none")
import model_policy as mp  # noqa: E402
import train_production as tp  # noqa: E402
import verify_model_manifest as gate  # noqa: E402


# --- the policy file ---------------------------------------------------------------------

def test_committed_policy_is_valid_and_trains_on_every_row():
    p = mp.load_policy()
    assert p["training"]["normal_sample"] is None          # null = the whole training file
    assert p["threshold"]["beta"] > 1                       # recall prioritised over precision
    grid = mp.threshold_grid(p)
    assert grid[0] == 0.05 and grid[-1] == 0.99 and len(grid) == len(set(grid))


def test_missing_policy_file_means_the_previous_defaults(tmp_path):
    p = mp.load_policy(tmp_path / "absent.json")
    assert p["threshold"]["beta"] == 2.0 and p["training"]["normal_sample"] is None


@pytest.mark.parametrize("patch", [
    {"training": {"normal_sample": 0}},
    {"threshold": {"beta": 0}},
    {"threshold": {"min_recall": 1.5}},
    {"threshold": {"grid": {"start": 0.5, "stop": 0.4, "step": 0.01}}},
    {"guardrail": {"max_f1_drop": -0.1}},
])
def test_invalid_policy_is_refused(tmp_path, patch):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(patch))
    with pytest.raises(ValueError):
        mp.load_policy(f)


def test_policy_comments_are_ignored(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"_readme": "x", "threshold": {"_beta": "y", "beta": 4}}))
    assert mp.load_policy(f)["threshold"]["beta"] == 4


# --- the selection rule --------------------------------------------------------------------

CURVE = [
    {"threshold": 0.5, "precision": 0.90, "recall": 0.60, "f1": 0.72, "f3": 0.62},
    {"threshold": 0.7, "precision": 0.75, "recall": 0.78, "f1": 0.76, "f3": 0.78},
    {"threshold": 0.9, "precision": 0.60, "recall": 0.88, "f1": 0.71, "f3": 0.85},
    {"threshold": 0.99, "precision": 0.40, "recall": 0.93, "f1": 0.56, "f3": 0.83},
]


def test_fbeta_picks_the_best_weighted_score():
    row, rule = mp.choose_threshold(CURVE, 3.0)
    assert (row["threshold"], rule) == (0.9, "fbeta")


def test_min_recall_picks_the_highest_precision_that_reaches_it():
    row, rule = mp.choose_threshold(CURVE, 3.0, min_recall=0.85)
    assert (row["threshold"], rule) == (0.9, "min_recall")
    row, _ = mp.choose_threshold(CURVE, 3.0, min_recall=0.70)
    assert row["threshold"] == 0.7


def test_unreachable_recall_target_takes_the_highest_recall():
    row, rule = mp.choose_threshold(CURVE, 3.0, min_recall=0.99)
    assert (row["threshold"], rule) == (0.99, "min_recall_unreachable")


# --- training on the whole file ------------------------------------------------------------

def test_default_trains_on_every_normal_row(tiny_fleet):
    df = tp.load_fleet(tiny_fleet["train"])
    baselines = tp.build_baselines(df, tp.FEATURES)
    x_iso, x6, x15, y, n_norm = tp.training_matrices(df, baselines, None)
    assert n_norm == int((df["label"] == 0).sum())
    assert len(x6) == len(df) and len(y) == len(df)         # every row, anomalies and normals


def test_a_cap_still_works(tiny_fleet):
    df = tp.load_fleet(tiny_fleet["train"])
    baselines = tp.build_baselines(df, tp.FEATURES)
    _, x6, _, _, n_norm = tp.training_matrices(df, baselines, 500)
    assert n_norm == 500 and len(x6) == int((df["label"] == 1).sum()) + 500


def test_cli_defaults_come_from_the_policy(tmp_path, monkeypatch):
    args = tp.parse_args(["--models-dir", str(tmp_path)])
    assert args.normal_sample is None
    assert args.max_f1_drop == mp.load_policy()["guardrail"]["max_f1_drop"]
    assert tp.parse_args(["--models-dir", str(tmp_path), "--normal-sample", "7"]).normal_sample == 7


# --- the guardrail protects recall ---------------------------------------------------------

def _eval(f1, recall):
    return {p: {"f1": f1, "recall": recall, "per_cause_recall": {"cpu_spike": 0.9}} for p in ("v3", "v4")}


def test_guardrail_tolerates_a_small_f1_cost_but_not_a_recall_loss():
    prod = _eval(0.73, 0.79)
    assert tp.guardrail(_eval(0.71, 0.84), prod, 0.03, 0.02)[0] is True     # more recall, a bit less F1
    ok, reasons = tp.guardrail(_eval(0.74, 0.70), prod, 0.03, 0.02)         # better F1, recall lost
    assert ok is False and any("recall" in r for r in reasons)


# --- the API reads the threshold from the manifest ------------------------------------------

def _manifest(tmp_path, threshold):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"production": {"decision_threshold": threshold}} if threshold is not None else {}))
    return p


def test_api_threshold_comes_from_the_manifest(tmp_path, monkeypatch):
    from api import app as A
    monkeypatch.delenv("PREDICT_THRESHOLD", raising=False)
    monkeypatch.setattr(A, "MANIFEST_PATH", _manifest(tmp_path, 0.93))
    value, source = A._resolve_threshold()
    assert value == 0.93 and "manifest" in source


def test_api_environment_override_is_explicit(tmp_path, monkeypatch):
    from api import app as A
    monkeypatch.setattr(A, "MANIFEST_PATH", _manifest(tmp_path, 0.93))
    monkeypatch.setenv("PREDICT_THRESHOLD", "0.4")
    value, source = A._resolve_threshold()
    assert value == 0.4 and "override" in source


def test_api_falls_back_when_the_manifest_has_no_threshold(tmp_path, monkeypatch):
    from api import app as A
    monkeypatch.delenv("PREDICT_THRESHOLD", raising=False)
    monkeypatch.setattr(A, "MANIFEST_PATH", _manifest(tmp_path, None))
    assert A._resolve_threshold()[0] == A.FALLBACK_THRESHOLD
    monkeypatch.setattr(A, "MANIFEST_PATH", tmp_path / "missing.json")
    assert A._resolve_threshold()[0] == A.FALLBACK_THRESHOLD


def test_served_threshold_is_the_committed_manifests():
    from api import app as A
    committed = json.loads((ROOT / "models" / "manifest.json").read_text())["production"]["decision_threshold"]
    if "PREDICT_THRESHOLD" not in os.environ:
        assert A.PREDICT_THRESHOLD == committed
        assert A.MODEL_LINEAGE["threshold"] == committed


# --- no deployment file carries a copy ----------------------------------------------------------

def test_the_repository_has_no_static_threshold():
    assert gate.static_thresholds(ROOT) == []


def test_gate_flags_a_static_threshold_in_a_deployment_file(tmp_path):
    (tmp_path / "kubernetes").mkdir()
    (tmp_path / "kubernetes" / "deployment.yaml").write_text(
        "env:\n  - name: PREDICT_THRESHOLD\n    value: \"0.6\"\n")
    arts = {"models/a.pkl": "h"}
    prod = {"artifacts": arts, "decision_threshold": 0.9,
            "evaluation": {"threshold": 0.9, "test_set": {"sha256": "t"}},
            "threshold_selection": {"selected": {"threshold": 0.9}, "artifacts": arts,
                                    "validation_set": {"sha256": "v"}}}
    problems = gate.check_threshold(prod, tmp_path)
    assert any("kubernetes/deployment.yaml" in p and "hard-codes" in p for p in problems)
