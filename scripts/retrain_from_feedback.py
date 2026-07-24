"""
Retrain the primary XGBoost model using operator feedback accumulated in
PostgreSQL, with a guardrail that prevents deploying a worse model.

Pipeline (matches README "Feedback loop and online learning" section):
  1. Pull feedback rows from PostgreSQL (operator_verdict IS NOT NULL)
  2. Load original training data (seed 42) + baselines
  3. Convert feedback rows into training-format rows
  4. Combine: original + feedback (sample_weight=5 on feedback rows)
  5. Retrain XGBoost with the same hyperparameters as v3
  6. Evaluate against the independent seed-123 test set
  7. Guardrail: F1 >= current AND per-cause recall drop <= 5pts on any category
  8. Always save the newly-trained model to models/history/ (evidence trail)
  9. If guardrail passes: copy to production filename, update manifest
  10. Print structured JSON result (for Jenkins to parse)

Exit codes:
  0  = retrain attempted and completed cleanly (whether promoted or not)
  1  = fatal error (couldn't connect to DB, training crashed, etc)
  2  = guardrail rejected (still an "expected" outcome, but useful to
       distinguish from success in CI)
"""
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier
import psycopg
from psycopg.rows import dict_row


# --- Configuration --------------------------------------------------------

FEATURES = ["cpu", "ram", "network", "disk_io", "disk_usage", "load_avg"]

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://feedback:feedback-dev-password@localhost:5432/feedback",
)

TRAINING_DATA_PATH   = Path("data/telecom_fleet_v2_labeled.csv")
TEST_DATA_PATH       = Path("data/telecom_fleet_v2_test.csv")
BASELINES_PATH       = Path("models/telecom_baselines_v2.json")
PRODUCTION_MODEL     = Path("models/telecom_xgb_classifier_v2.pkl")
PRODUCTION_ENCODER   = Path("models/telecom_xgb_label_encoder_v2.pkl")
HISTORY_DIR          = Path("models/history")
MANIFEST_PATH        = Path("models/manifest.json")

FEEDBACK_SAMPLE_WEIGHT = 5.0
XGB_HYPERPARAMS = dict(
    n_estimators=150,
    max_depth=6,
    learning_rate=0.1,
    random_state=42,
    n_jobs=-1,
    verbosity=0,
    use_label_encoder=False,
)

GUARDRAIL_MAX_PER_CAUSE_RECALL_DROP = 0.05


# --- Helpers --------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_feedback_rows() -> list[dict]:
    """Pull all predictions with an operator verdict from PostgreSQL."""
    log(f"Connecting to PostgreSQL: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL}")
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT id, machine, machine_type, time_window,
                   features_json, raw_metrics_json,
                   final_is_anomaly, final_cause,
                   operator_verdict
            FROM predictions
            WHERE operator_verdict IS NOT NULL
        """).fetchall()
    log(f"Fetched {len(rows)} labeled feedback rows")
    return rows


def feedback_row_to_training_example(row: dict) -> tuple[list[float], str]:
    """
    Convert one operator-verdict row into (feature_vector, label_string).

    Label derivation:
      true_positive  -> the model was right, keep the cause it predicted
      false_positive -> operator says it wasn't an anomaly, label "normal"
      true_negative  -> "normal" (both model and operator agree)
      false_negative -> operator says it WAS an anomaly the model missed;
                         we don't know the exact cause, use "unknown_feedback"
                         (which will fall into "cascade"-like handling)
    """
    verdict = row["operator_verdict"]
    raw = json.loads(row["raw_metrics_json"])
    features = [raw[f] for f in FEATURES]

    if verdict == "true_positive":
        label = row["final_cause"] or "unknown_feedback"
    elif verdict == "false_positive":
        label = "normal"
    elif verdict == "true_negative":
        label = "normal"
    elif verdict == "false_negative":
        label = "unknown_feedback"
    else:
        raise ValueError(f"Unknown verdict: {verdict}")

    return features, label


def load_training_data(subsample_normals: int = 2_000_000) -> tuple[pd.DataFrame, pd.Series]:
    """Load the original training data (seed 42), subsample as v3 did."""
    log(f"Loading original training data from {TRAINING_DATA_PATH}...")
    t0 = time.time()
    df = pd.read_csv(TRAINING_DATA_PATH)
    log(f"  Loaded {len(df):,} rows in {time.time() - t0:.1f}s")

    log("Subsampling to match v3 methodology (all anomalies + 2M normals)...")
    anomalies = df[df.label == 1]
    normals = df[df.label == 0].sample(n=subsample_normals, random_state=42)
    df = pd.concat([anomalies, normals], ignore_index=True)
    df = df.sample(frac=1, random_state=42).reset_index(drop=True)
    log(f"  Subsampled to {len(df):,} rows")

    df["anomaly_type"] = df["anomaly_type"].fillna("normal").replace("", "normal").replace("cascade", "normal")

    return df, df["anomaly_type"]


def apply_zscore(df: pd.DataFrame, baselines: dict) -> np.ndarray:
    """Apply the same per-machine per-window z-score as the serving path."""
    result = np.zeros((len(df), len(FEATURES)), dtype=np.float32)
    global_stats = baselines["__global__"]
    for i, row in enumerate(df.itertuples(index=False)):
        machine = row.machine
        m_stats = baselines.get(machine, {})
        for j, feat in enumerate(FEATURES):
            mean, std = m_stats.get(feat, global_stats[feat])
            raw = getattr(row, feat)
            result[i, j] = (raw - mean) / std
    return result


def evaluate_model(model, label_encoder, X_test, y_test_binary, y_test_cause) -> dict:
    """Score the model on the seed-123 test set. Returns metrics dict."""
    pred_enc = model.predict(X_test)
    pred_cause = label_encoder.inverse_transform(pred_enc)
    pred_binary = (pred_cause != "normal").astype(int)

    metrics = {
        "f1": float(f1_score(y_test_binary, pred_binary)),
        "precision": float(precision_score(y_test_binary, pred_binary, zero_division=0)),
        "recall": float(recall_score(y_test_binary, pred_binary)),
        "per_cause_recall": {},
    }
    for cause in ["cpu_spike", "memory_leak", "network_flood",
                  "disk_saturation", "silent_failure", "cascade"]:
        mask = (y_test_cause == cause).values
        if mask.sum() > 0:
            metrics["per_cause_recall"][cause] = float(pred_binary[mask].mean())
    return metrics


def check_guardrail(current: dict, new: dict) -> tuple[bool, str]:
    """
    Guardrail: only promote if:
      - New F1 >= current F1 (no aggregate regression)
      - Per-cause recall drops by no more than GUARDRAIL_MAX_PER_CAUSE_RECALL_DROP
        on any category

    Returns (passed, reason_if_rejected).
    """
    if new["f1"] < current["offline_f1"]:
        return False, f"F1 dropped: {current['offline_f1']:.3f} -> {new['f1']:.3f}"

    for cause, cur_recall in current["per_cause_recall"].items():
        new_recall = new["per_cause_recall"].get(cause, 0.0)
        drop = cur_recall - new_recall
        if drop > GUARDRAIL_MAX_PER_CAUSE_RECALL_DROP:
            return False, (
                f"{cause} recall dropped {drop*100:.1f}pts "
                f"({cur_recall:.3f} -> {new_recall:.3f})"
            )

    return True, "all guardrails passed"


def main() -> int:
    result = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "promoted": False,
        "reason": None,
        "feedback_rows_used": 0,
        "new_model_path": None,
        "new_metrics": None,
        "guardrail_result": None,
    }

    try:
        # Step 1: Fetch feedback
        feedback_rows = fetch_feedback_rows()
        result["feedback_rows_used"] = len(feedback_rows)

        # Step 2: Load original training data
        df_train, y_train_cause_raw = load_training_data()

        # Step 3: Convert feedback to training examples
        feedback_features = []
        feedback_labels = []
        for row in feedback_rows:
            try:
                feats, label = feedback_row_to_training_example(row)
                feedback_features.append(feats)
                feedback_labels.append(label)
            except (KeyError, ValueError) as e:
                log(f"  Skipping malformed feedback row {row['id']}: {e}")

        log(f"Converted {len(feedback_features)} feedback rows to training examples")

        # Step 4: Apply z-score to original training features
        with open(BASELINES_PATH) as f:
            baselines = json.load(f)

        log("Computing z-scores for original training data...")
        t0 = time.time()
        X_train_orig = apply_zscore(df_train, baselines)
        log(f"  Done in {time.time() - t0:.1f}s")

        # z-score feedback rows too
        if feedback_features:
            log("Computing z-scores for feedback rows...")
            feedback_df = pd.DataFrame(feedback_features, columns=FEATURES)
            feedback_df["machine"] = [r["machine"] for r in feedback_rows[:len(feedback_features)]]
            X_train_fb = apply_zscore(feedback_df, baselines)
            X_train = np.vstack([X_train_orig, X_train_fb])
            y_train_all = list(y_train_cause_raw) + feedback_labels
        else:
            X_train = X_train_orig
            y_train_all = list(y_train_cause_raw)

        # Sample weights: 1.0 for original, 5.0 for feedback
        sample_weights = np.concatenate([
            np.ones(len(X_train_orig)),
            np.full(len(feedback_features), FEEDBACK_SAMPLE_WEIGHT),
        ]) if feedback_features else np.ones(len(X_train_orig))

        # Step 5: Encode labels + train
        le = LabelEncoder()
        y_train_enc = le.fit_transform(y_train_all)

        log(f"Training XGBoost on {len(X_train):,} rows "
            f"({len(feedback_features)} feedback @ weight {FEEDBACK_SAMPLE_WEIGHT})...")
        t0 = time.time()
        model = XGBClassifier(**XGB_HYPERPARAMS)
        model.fit(X_train, y_train_enc, sample_weight=sample_weights)
        log(f"  Trained in {time.time() - t0:.1f}s")

        # Step 6: Evaluate against test set
        log(f"Loading test data from {TEST_DATA_PATH}...")
        df_test = pd.read_csv(TEST_DATA_PATH)
        y_test_binary = df_test.label.values
        y_test_cause = df_test["anomaly_type"].fillna("normal").replace("", "normal")

        log("Computing z-scores for test data...")
        t0 = time.time()
        X_test = apply_zscore(df_test, baselines)
        log(f"  Done in {time.time() - t0:.1f}s")

        log("Evaluating...")
        new_metrics = evaluate_model(model, le, X_test, y_test_binary, y_test_cause)
        log(f"  F1={new_metrics['f1']:.3f}  "
            f"Precision={new_metrics['precision']:.3f}  "
            f"Recall={new_metrics['recall']:.3f}")
        for cause, r in new_metrics["per_cause_recall"].items():
            log(f"    {cause:<20} recall={r:.3f}")

        result["new_metrics"] = new_metrics

        # Step 7: Guardrail
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        current = manifest["current_production_model"]["evaluation"]

        passed, reason = check_guardrail(current, new_metrics)
        result["guardrail_result"] = {"passed": passed, "reason": reason}
        log(f"Guardrail: {'PASSED' if passed else 'REJECTED'} - {reason}")

        # Step 8: Always save the model (evidence trail)
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        history_model_path = HISTORY_DIR / f"telecom_xgb_v3_{ts}.pkl"
        history_encoder_path = HISTORY_DIR / f"telecom_xgb_label_encoder_v3_{ts}.pkl"

        joblib.dump(model, history_model_path)
        joblib.dump(le, history_encoder_path)
        log(f"Model saved to history: {history_model_path}")
        result["new_model_path"] = str(history_model_path)

        # Update manifest
        manifest["retrain_history"].append({
            "attempted_at": datetime.now(timezone.utc).isoformat(),
            "history_path": str(history_model_path),
            "feedback_rows_used": len(feedback_features),
            "guardrail_passed": passed,
            "guardrail_reason": reason,
            "metrics": new_metrics,
            "promoted_to_production": passed,
        })

        # Step 9: If passed, promote
        if passed:
            shutil.copy(history_model_path, PRODUCTION_MODEL)
            shutil.copy(history_encoder_path, PRODUCTION_ENCODER)
            manifest["current_production_model"] = {
                "path": str(PRODUCTION_MODEL),
                "docker_image_tag": "PENDING_JENKINS_BUILD",
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "source": "retrain",
                "training_rows": len(X_train),
                "feedback_rows_used": len(feedback_features),
                "evaluation": new_metrics,
            }
            log(f"PROMOTED: {history_model_path} -> {PRODUCTION_MODEL}")
            result["promoted"] = True

        with open(MANIFEST_PATH, "w") as f:
            json.dump(manifest, f, indent=2)

        result["success"] = True
        result["finished_at"] = datetime.now(timezone.utc).isoformat()

        print("\n" + "=" * 60)
        print("RETRAIN RESULT (JSON, for Jenkins to parse):")
        print("=" * 60)
        print(json.dumps(result, indent=2, default=str))

        return 0 if passed else 2

    except Exception as e:
        result["reason"] = f"fatal error: {e}"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        log(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        print("\n" + "=" * 60)
        print("RETRAIN RESULT (JSON, for Jenkins to parse):")
        print("=" * 60)
        print(json.dumps(result, indent=2, default=str))
        return 1


if __name__ == "__main__":
    sys.exit(main())
