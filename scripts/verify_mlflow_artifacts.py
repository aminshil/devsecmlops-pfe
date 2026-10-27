#!/usr/bin/env python3
"""
Read the model files of an MLflow run back (MLflow serves them from MinIO, its artifact store) and
compare each one, byte for byte through its SHA-256, with the hashes the pipeline recorded.

  default         the served model: models/manifest.json production.mlflow_run_id, compared with
                  production.artifacts (the six files the image bakes in and the API checks at start)
  --report FILE   a training run's report (Jenkinsfile.model stage 6): its mlflow_run_id, compared
                  with threshold_selection.artifacts (the candidate's six files)

Exit 0 only if every expected file is in the run and matches. Output is printed and, with --out,
written as JSON. Read-only: files are downloaded to memory, nothing is written under models/.

  python3 scripts/verify_mlflow_artifacts.py
  python3 scripts/verify_mlflow_artifacts.py --out models/results/mlflow_readback_YYYYMMDD.json
  python3 scripts/verify_mlflow_artifacts.py --report build/model-report.json      # Jenkins
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = "model"            # train_production.log_to_mlflow: mlflow.log_artifacts(..., artifact_path="model")


def get(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def listed(uri, run_id):
    q = urllib.parse.urlencode({"run_id": run_id, "path": ARTIFACT_PATH})
    files = json.loads(get(f"{uri}/api/2.0/mlflow/artifacts/list?{q}")).get("files", [])
    return {Path(f["path"]).name: f for f in files if not f.get("is_dir")}


def download(uri, run_id, name):
    """The MLflow server's artifact download route (the one its web UI uses)."""
    q = urllib.parse.urlencode({"path": f"{ARTIFACT_PATH}/{name}", "run_uuid": run_id, "run_id": run_id})
    return get(f"{uri}/get-artifact?{q}")


def expected_hashes(args):
    if args.report:
        rep = json.loads(args.report.read_text())
        run_id = rep.get("mlflow_run_id")
        arts = (rep.get("threshold_selection") or {}).get("artifacts") or {}
        source = f"{args.report} (threshold_selection.artifacts)"
    else:
        prod = json.loads((ROOT / "models" / "manifest.json").read_text()).get("production") or {}
        run_id = prod.get("mlflow_run_id")
        arts = prod.get("artifacts") or {}
        source = "models/manifest.json (production.artifacts)"
    return run_id, {Path(k).name: v for k, v in arts.items()}, source


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5001"))
    ap.add_argument("--report", type=Path, help="a train_production.py report (default: the served model)")
    ap.add_argument("--out", type=Path, help="also write the result as JSON")
    args = ap.parse_args()
    uri = args.tracking_uri.rstrip("/")
    if urllib.parse.urlparse(uri).scheme not in ("http", "https"):
        sys.exit(f"--tracking-uri must be an http(s) URL, not {uri!r}")
    run_id, expected, source = expected_hashes(args)
    if not run_id:
        sys.exit(f"no MLflow run id in {source.split(' ')[0]}: the run was not logged to MLflow")
    if not expected:
        sys.exit(f"no artifact hashes in {source}")
    print(f"MLflow {uri}, run {run_id}: {len(expected)} files expected from {source}")
    try:
        in_run = listed(uri, run_id)
    except (OSError, ValueError) as e:
        sys.exit(f"MLflow not reachable or run unknown: {e}")
    rows, ok = [], True
    for name, want in sorted(expected.items()):
        row = {"file": name, "expected_sha256": want}
        if name not in in_run:
            row["result"] = "missing from the run"
        else:
            try:
                data = download(uri, run_id, name)
                got = hashlib.sha256(data).hexdigest()
                row.update(bytes=len(data), downloaded_sha256=got,
                           result="match" if got == want else "DIFFERENT")
            except (OSError, urllib.error.HTTPError) as e:
                row["result"] = f"download failed ({e})"
        ok &= row["result"] == "match"
        rows.append(row)
        print(f"  {row['result']:<22} {name}" + (f"  ({row['bytes']:,} bytes)" if "bytes" in row else ""))
    print(("all " if ok else "NOT all ") + f"{len(expected)} files read back from MLflow (MinIO) and identical to the recorded hashes")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "checked_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tracking_uri": uri, "run_id": run_id, "expected_from": source,
            "files_in_run": sorted(in_run), "files": rows, "all_match": ok}, indent=2) + "\n")
        print(f"written to {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
