"""
Feedback DB helper -- SQLite operations for the prediction logging and
operator-feedback loop (v2.12.0). See README "Feedback loop and online
learning" section for the full design.

All writes are synchronous by design: the /predict endpoint calls
insert_prediction() before returning, guaranteeing the prediction is
persisted before the caller sees the response. Latency cost is ~1-5ms
per call, acceptable given /predict's p50 baseline of ~300ms.
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("FEEDBACK_DB_PATH", "/app/data/predictions.db"))

VALID_VERDICTS = {"true_positive", "false_positive", "true_negative", "false_negative"}


def _iso_now() -> str:
    """UTC ISO-8601 timestamp for consistent, timezone-unambiguous storage."""
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    """Open a connection to the DB, ensuring the parent directory exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the predictions table if it does not exist. Idempotent."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id                 TEXT PRIMARY KEY,
                timestamp          TEXT NOT NULL,
                machine            TEXT NOT NULL,
                machine_type       TEXT,
                window             TEXT,
                features_json      TEXT NOT NULL,
                raw_metrics_json   TEXT NOT NULL,
                model_version      TEXT NOT NULL,
                predict_threshold  REAL NOT NULL,
                xgb_p_normal       REAL,
                xgb_cause          TEXT,
                iso_score          REAL,
                final_is_anomaly   INTEGER NOT NULL,
                final_cause        TEXT,
                operator_verdict   TEXT,
                verdict_timestamp  TEXT,
                verdict_notes      TEXT
            )
        """)
        # Index on timestamp for /predictions/recent efficiency
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON predictions(timestamp DESC)")
        # Index on operator_verdict for the retrain pipeline
        conn.execute("CREATE INDEX IF NOT EXISTS idx_verdict ON predictions(operator_verdict)")
        conn.commit()


def insert_prediction(
    machine: str,
    machine_type: Optional[str],
    window: Optional[str],
    features: dict,
    raw_metrics: dict,
    model_version: str,
    predict_threshold: float,
    xgb_p_normal: Optional[float],
    xgb_cause: Optional[str],
    iso_score: Optional[float],
    final_is_anomaly: int,
    final_cause: Optional[str],
) -> str:
    """
    Insert a new prediction row. Returns the generated prediction_id (UUIDv4).
    Called synchronously from /predict before responding to the caller, so
    if this raises, the /predict call will 500 -- fail-loud is intentional
    for data integrity.
    """
    pid = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute("""
            INSERT INTO predictions (
                id, timestamp, machine, machine_type, window,
                features_json, raw_metrics_json,
                model_version, predict_threshold,
                xgb_p_normal, xgb_cause, iso_score,
                final_is_anomaly, final_cause
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pid, _iso_now(), machine, machine_type, window,
            json.dumps(features), json.dumps(raw_metrics),
            model_version, predict_threshold,
            xgb_p_normal, xgb_cause, iso_score,
            final_is_anomaly, final_cause,
        ))
        conn.commit()
    return pid


def update_verdict(prediction_id: str, verdict: str, notes: Optional[str] = None) -> Optional[dict]:
    """
    Update the verdict for an existing prediction. Returns the updated row as
    a dict, or None if the prediction_id was not found.

    Raises ValueError if the verdict string is not in VALID_VERDICTS.
    Idempotent: submitting a second verdict for the same prediction_id
    overwrites the first with the newer timestamp.
    """
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(VALID_VERDICTS)}, got {verdict!r}")
    with _connect() as conn:
        cur = conn.execute("""
            UPDATE predictions
            SET operator_verdict = ?, verdict_timestamp = ?, verdict_notes = ?
            WHERE id = ?
        """, (verdict, _iso_now(), notes, prediction_id))
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
        return dict(row) if row else None


def recent_predictions(limit: int = 100) -> list[dict]:
    """Most recent N predictions, newest first. Caps at 1000 defensively."""
    limit = min(max(limit, 1), 1000)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM predictions ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def verdict_stats() -> dict:
    """
    Count verdict types across all predictions. Includes NULL (no verdict yet)
    as a separate key. Useful for monitoring how much labeled data has been
    collected for the retrain pipeline.
    """
    with _connect() as conn:
        rows = conn.execute("""
            SELECT COALESCE(operator_verdict, '_pending') AS verdict, COUNT(*) AS n
            FROM predictions
            GROUP BY operator_verdict
        """).fetchall()
        stats = {r["verdict"]: r["n"] for r in rows}
        total = sum(stats.values())
        return {"total_predictions": total, "by_verdict": stats}
