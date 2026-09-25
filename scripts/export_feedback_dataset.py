#!/usr/bin/env python3
"""
Export operator-labelled predictions from the feedback DB as a versioned
training dataset: data/feedback/feedback_<UTC timestamp>.csv plus a .sha256.

The live database is never read directly by training. This snapshot is the
input to `ml-model/train_production.py --feedback <file>`, and its SHA-256 is
recorded in the model manifest, so every retrained model can be traced to the
exact feedback it used.

Reads through `kubectl exec` into the PostgreSQL pod (credentials come from
the pod's own environment), so no database port or password is exposed to
the host.

Usage:  python scripts/export_feedback_dataset.py [--namespace ml-serving]
"""
import argparse
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUERY = ("COPY (SELECT id, timestamp, machine, machine_type, time_window, "
         "raw_metrics_json, history_json, model_version, final_is_anomaly, "
         "final_cause, operator_verdict, verdict_timestamp FROM predictions "
         "WHERE operator_verdict IS NOT NULL ORDER BY timestamp) "
         "TO STDOUT WITH CSV HEADER")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="ml-serving")
    ap.add_argument("--pod", default="postgres-0")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "feedback")
    a = ap.parse_args()

    cmd = ["kubectl", "-n", a.namespace, "exec", a.pod, "--", "sh", "-c",
           f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "{QUERY}"']
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr.strip(), file=sys.stderr)
        return 1
    rows = max(res.stdout.count("\n") - 1, 0)
    if rows == 0:
        print("no labelled predictions yet -- nothing exported")
        return 0
    a.out_dir.mkdir(parents=True, exist_ok=True)
    out = a.out_dir / f"feedback_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    out.write_text(res.stdout)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(".csv.sha256").write_text(f"{digest}  {out.name}\n")
    print(f"{out.relative_to(ROOT)}  rows={rows}  sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
