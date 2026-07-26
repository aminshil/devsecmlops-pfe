"""
Feedback DB helper -- PostgreSQL operations for the prediction logging
and operator-feedback loop (v2.12.0). See README "Feedback loop and
online learning" section for the full design.

Rewritten from SQLite -> PostgreSQL for production readiness: multi-writer
safety (multiple API pods can concurrently write without file-lock issues),
survives node failures in multi-node clusters, standard client-server DB
architecture instead of a shared-file compromise.

Connection via DATABASE_URL env var, expected format:
    postgresql://user:pass@host:5432/dbname

All writes are synchronous by design: /predict calls insert_prediction()
before returning, guaranteeing the prediction is persisted before the
caller sees the response. Latency cost is ~2-8ms per call, acceptable
given /predict's p50 baseline of ~300ms.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


def _build_database_url() -> str:
    """
    Build the PostgreSQL connection URL from environment variables.

    Priority:
      1. DATABASE_URL, if set explicitly (full connection string)
      2. Otherwise assemble from POSTGRES_{USER,PASSWORD,HOST,PORT,DB},
         each with a non-secret default EXCEPT the password, which has
         no default -- it must come from the environment (injected from
         a Kubernetes Secret in production). No credential is hardcoded
         in source.
    """
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    user = os.environ.get("POSTGRES_USER", "feedback")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "postgres.ml-serving.svc.cluster.local")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "feedback")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


DATABASE_URL = _build_database_url()

VALID_VERDICTS = {"true_positive", "false_positive", "true_negative", "false_negative"}


def _iso_now() -> str:
    """UTC ISO-8601 timestamp for consistent, timezone-unambiguous storage."""
    return datetime.now(timezone.utc).isoformat()


def _connect():
    """Open a connection to PostgreSQL. Callers are responsible for closing."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    """Create the predictions table + indexes if not already present. Idempotent."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id                 TEXT PRIMARY KEY,
                    timestamp          TEXT NOT NULL,
                    machine            TEXT NOT NULL,
                    machine_type       TEXT,
                    time_window        TEXT,
                    features_json      TEXT NOT NULL,
                    raw_metrics_json   TEXT NOT NULL,
                    model_version      TEXT NOT NULL,
                    predict_threshold  DOUBLE PRECISION NOT NULL,
                    xgb_p_normal       DOUBLE PRECISION,
                    xgb_cause          TEXT,
                    iso_score          DOUBLE PRECISION,
                    final_is_anomaly   INTEGER NOT NULL,
                    final_cause        TEXT,
                    operator_verdict   TEXT,
                    verdict_timestamp  TEXT,
                    verdict_notes      TEXT
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON predictions(timestamp DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_verdict ON predictions(operator_verdict)")
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
    Called synchronously from /predict; if this raises, the /predict call
    will 500 -- fail-loud by design for data integrity.
    """
    pid = str(uuid4())
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO predictions (
                    id, timestamp, machine, machine_type, time_window,
                    features_json, raw_metrics_json,
                    model_version, predict_threshold,
                    xgb_p_normal, xgb_cause, iso_score,
                    final_is_anomaly, final_cause
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    Update the verdict for an existing prediction. Returns the updated row
    as a dict, or None if the prediction_id was not found.

    Raises ValueError if the verdict is not in VALID_VERDICTS.
    Idempotent: submitting a second verdict for the same prediction_id
    overwrites the first with the newer timestamp.
    """
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(VALID_VERDICTS)}, got {verdict!r}")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE predictions
                SET operator_verdict = %s, verdict_timestamp = %s, verdict_notes = %s
                WHERE id = %s
            """, (verdict, _iso_now(), notes, prediction_id))
            if cur.rowcount == 0:
                conn.commit()
                return None
            cur.execute("SELECT * FROM predictions WHERE id = %s", (prediction_id,))
            row = cur.fetchone()
        conn.commit()
        return row


def recent_predictions(limit: int = 100) -> list[dict]:
    """Most recent N predictions, newest first. Caps at 1000 defensively."""
    limit = min(max(limit, 1), 1000)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM predictions ORDER BY timestamp DESC LIMIT %s",
                (limit,)
            )
            return list(cur.fetchall())


def verdict_stats() -> dict:
    """
    Count verdict types across all predictions. Includes NULL (no verdict yet)
    as '_pending'. Useful for monitoring how much labeled data has been
    collected for the retrain pipeline.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(operator_verdict, '_pending') AS verdict, COUNT(*) AS n
                FROM predictions
                GROUP BY operator_verdict
            """)
            stats = {r["verdict"]: r["n"] for r in cur.fetchall()}
    total = sum(stats.values())
    return {"total_predictions": total, "by_verdict": stats}
