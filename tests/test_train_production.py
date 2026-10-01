"""The production model pipeline: train -> evaluate -> guardrail -> promote,
run end to end on tiny generated fleets (never touches models/)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml-model"))
import train_production as tp  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import verify_model_manifest as gate  # noqa: E402


def _validation_for(fleet) -> Path:
    """A validation file distinct from the train and test files. Mechanics only: it is
    the tiny test fixture minus its last row, so its bytes differ (the independence
    guard compares SHA-256) and it still contains anomalies. A real validation fleet is
    generated with its own seed (data/telecom_fleet_v2_val.csv)."""
    path = Path(fleet["test"]).with_name("derived_validation.csv")
    if not path.exists():
        pd.read_csv(fleet["test"]).iloc[:-1].to_csv(path, index=False)
    return path


def _args(fleet, models_dir, work_dir, *extra):
    args = ["--train", str(fleet["train"]), "--test", str(fleet["test"]),
            "--normal-sample", "5000", "--models-dir", str(models_dir),
            "--work-dir", str(work_dir), *extra]
    if "--promote" in extra and "--val" not in extra:
        args += ["--val", str(_validation_for(fleet))]
    return args


@pytest.fixture(scope="module")
def promoted(tiny_fleet, tmp_path_factory):
    """Initial training into an empty models dir, promoted."""
    models = tmp_path_factory.mktemp("models")
    work = tmp_path_factory.mktemp("work")
    rc = tp.main(_args(tiny_fleet, models, work, "--promote",
                       "--report", str(work / "report.json")))
    return {"rc": rc, "models": models, "work": work,
            "report": json.loads((work / "report.json").read_text())}


def test_initial_training_is_promoted_with_lineage(promoted):
    assert promoted["rc"] == 0
    manifest = json.loads((promoted["models"] / "manifest.json").read_text())
    prod = manifest["production"]
    assert prod["source"] == "ml-model/train_production.py"
    assert set(prod["datasets"]) == {"train", "test"}
    assert all(len(d["sha256"]) == 64 for d in prod["datasets"].values())
    assert len(prod["artifacts"]) == len(tp.ARTIFACTS)
    for key in ("v3", "v4", "v4_at_0.5"):
        assert 0.0 <= prod["evaluation"][key]["f1"] <= 1.0
    assert manifest["history"][-1]["promoted"] is True


def test_ci_gate_accepts_promoted_model(promoted):
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_model_manifest.py"),
                          "--min-f1", "0.0", "--manifest", str(promoted["models"] / "manifest.json"),
                          "--skip-deploy-check"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stdout


def test_ci_gate_rejects_tampered_artifact(promoted, tmp_path):
    copy = tmp_path / "models"
    shutil.copytree(promoted["models"], copy)
    (copy / tp.ARTIFACTS["iso_model"]).write_bytes(b"tampered")
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_model_manifest.py"),
                          "--min-f1", "0.0", "--manifest", str(copy / "manifest.json"),
                          "--skip-deploy-check"],
                         capture_output=True, text=True)
    assert res.returncode == 1 and "hash mismatch" in res.stdout


def test_ci_gate_rejects_low_f1(promoted):
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_model_manifest.py"),
                          "--min-f1", "1.01", "--manifest", str(promoted["models"] / "manifest.json"),
                          "--skip-deploy-check"],
                         capture_output=True, text=True)
    assert res.returncode == 1


def test_retraining_same_data_is_deterministic_and_passes(tiny_fleet, promoted, tmp_path):
    models = tmp_path / "models"
    shutil.copytree(promoted["models"], models)
    rc = tp.main(_args(tiny_fleet, models, tmp_path / "w", "--report", str(tmp_path / "r.json")))
    run = json.loads((tmp_path / "r.json").read_text())
    assert rc == 0
    assert run["candidate_evaluation"]["v4"]["f1"] == run["production_evaluation"]["v4"]["f1"]


def test_rejected_candidate_leaves_production_untouched(tiny_fleet, promoted, tmp_path, monkeypatch):
    models = tmp_path / "models"
    shutil.copytree(promoted["models"], models)
    before = {p.name: p.read_bytes() for p in models.glob("*.pkl")}
    monkeypatch.setattr(tp, "guardrail", lambda *a, **k: (False, ["forced rejection"]))
    rc = tp.main(_args(tiny_fleet, models, tmp_path / "w", "--promote"))
    assert rc == 1
    assert {p.name: p.read_bytes() for p in models.glob("*.pkl")} == before
    history = json.loads((models / "manifest.json").read_text())["history"]
    assert history[-1]["promoted"] is False
    assert history[-1]["guardrail_reasons"] == ["forced rejection"]


def test_guardrail_rules():
    prod = {p: {"f1": 0.70, "per_cause_recall": {"cpu_spike": 0.95, "memory_leak": 0.90}}
            for p in ("v3", "v4")}
    ok = {p: {"f1": 0.71, "per_cause_recall": {"cpu_spike": 0.94, "memory_leak": 0.90}}
          for p in ("v3", "v4")}
    assert tp.guardrail(ok, prod, 0.0, 0.02)[0] is True
    worse_f1 = json.loads(json.dumps(ok)); worse_f1["v4"]["f1"] = 0.69
    assert tp.guardrail(worse_f1, prod, 0.0, 0.02)[0] is False
    worse_cause = json.loads(json.dumps(ok)); worse_cause["v3"]["per_cause_recall"]["memory_leak"] = 0.85
    passed, reasons = tp.guardrail(worse_cause, prod, 0.0, 0.02)
    assert not passed and "memory_leak" in reasons[0]


def test_evaluate_production_records_evaluation(tiny_fleet, promoted, tmp_path):
    models = tmp_path / "models"
    shutil.copytree(promoted["models"], models)
    manifest = json.loads((models / "manifest.json").read_text())
    manifest["production"].pop("evaluation")
    (models / "manifest.json").write_text(json.dumps(manifest))
    assert tp.main(["--evaluate-production", "--test", str(tiny_fleet["test"]),
                    "--models-dir", str(models)]) == 0
    ev = json.loads((models / "manifest.json").read_text())["production"]["evaluation"]
    assert ev["test_set"]["rows"] > 0 and "v4" in ev


def test_feedback_rows_are_used_and_unknown_causes_skipped(tiny_fleet, promoted, tmp_path):
    t = pd.read_csv(tiny_fleet["test"]).sort_values(["machine", "timestamp"]).reset_index(drop=True)
    rows = []
    for i in range(10, 70):
        r = t.iloc[i]
        verdict = ["true_positive", "false_positive", "false_negative"][i % 3]
        hist = {c: t.iloc[i - 9:i + 1][c].astype(float).tolist() for c in ("cpu", "ram", "load_avg")}
        rows.append({"id": f"p{i}", "timestamp": r.timestamp, "machine": r.machine,
                     "machine_type": r.type, "time_window": "afternoon",
                     "raw_metrics_json": json.dumps({c: float(r[c]) for c in tp.FEATURES}),
                     "history_json": json.dumps(hist) if i % 2 else None,
                     "model_version": "telecom_v4_rolling", "final_is_anomaly": 1,
                     "final_cause": "cpu_spike" if verdict == "true_positive" else "",
                     "operator_verdict": verdict, "verdict_timestamp": r.timestamp})
    fb = tmp_path / "feedback.csv"
    pd.DataFrame(rows).to_csv(fb, index=False)
    models = tmp_path / "models"
    shutil.copytree(promoted["models"], models)
    tp.main(_args(tiny_fleet, models, tmp_path / "w", "--feedback", str(fb),
                  "--max-f1-drop", "1.0", "--max-recall-drop", "1.0",
                  "--report", str(tmp_path / "r.json")))
    info = json.loads((tmp_path / "r.json").read_text())["feedback"]
    assert info["rows_skipped_unknown_cause"] == 20       # the false negatives
    assert info["rows_used_v3"] == 40
    assert 0 < info["rows_used_v4"] < 40                  # only rows with history


def test_missing_dataset_exits_2(tmp_path):
    assert tp.main(["--train", str(tmp_path / "nope.csv"), "--test", str(tmp_path / "nope.csv"),
                    "--models-dir", str(tmp_path)]) == 2


# --- threshold lifecycle: a promoted model's threshold is selected for that model ---

def _selected_prod():
    arts = {"models/a.pkl": "h1"}
    return {"artifacts": dict(arts), "decision_threshold": 0.6,
            "evaluation": {"threshold": 0.6, "test_set": {"sha256": "test-sha"}},
            "threshold_selection": {"selected": {"threshold": 0.6}, "artifacts": dict(arts),
                                    "validation_set": {"sha256": "val-sha"}}}


def test_gate_accepts_a_selection_made_for_these_artifacts(tmp_path):
    assert gate.check_threshold(_selected_prod(), tmp_path) == []


def test_gate_requires_a_threshold_selection(tmp_path):
    prod = _selected_prod()
    del prod["threshold_selection"], prod["decision_threshold"]
    assert any("no recorded threshold selection" in p for p in gate.check_threshold(prod, tmp_path))


def test_gate_rejects_a_threshold_selected_for_other_artifacts(tmp_path):
    prod = _selected_prod()
    prod["threshold_selection"]["artifacts"] = {"models/a.pkl": "other"}
    assert any("different artifacts" in p for p in gate.check_threshold(prod, tmp_path))


def test_gate_rejects_an_evaluation_at_another_threshold(tmp_path):
    prod = _selected_prod()
    prod["evaluation"]["threshold"] = 0.85
    assert any("evaluation recorded at 0.85" in p for p in gate.check_threshold(prod, tmp_path))


def test_gate_rejects_the_test_set_as_validation_fleet(tmp_path):
    prod = _selected_prod()
    prod["threshold_selection"]["validation_set"]["sha256"] = "test-sha"
    assert any("is the test set" in p for p in gate.check_threshold(prod, tmp_path))


def test_promotion_requires_an_independent_validation_fleet(tiny_fleet, tmp_path):
    models, work = tmp_path / "models", tmp_path / "work"
    models.mkdir()
    for bad in (tmp_path / "missing.csv", Path(tiny_fleet["test"]), Path(tiny_fleet["train"])):
        assert tp.main(_args(tiny_fleet, models, work, "--promote", "--val", str(bad))) == 2, bad
    assert not (models / "manifest.json").exists()
    assert not list(models.glob("*.pkl"))


def test_promoted_threshold_is_selected_for_the_candidate(promoted):
    prod = json.loads((promoted["models"] / "manifest.json").read_text())["production"]
    sel = prod["threshold_selection"]
    assert prod["decision_threshold"] == sel["selected"]["threshold"] == prod["evaluation"]["threshold"]
    assert sel["artifacts"] == prod["artifacts"]
    assert sel["validation_set"]["sha256"] != prod["evaluation"]["test_set"]["sha256"]
    assert gate.check_threshold(prod, promoted["models"]) == []
