#!/usr/bin/env python3
"""
Create the MLflow artifact bucket on MinIO if it does not exist yet (idempotent).

MLflow's `--artifacts-destination s3://mlflow-artifacts/` does not create the
bucket; on a clean machine the first artifact upload fails until it exists.
Credentials come from the environment, never from arguments:

  MINIO_ROOT_USER=... MINIO_ROOT_PASSWORD=... \
    python scripts/ensure_minio_bucket.py [--endpoint http://localhost:9001] [--bucket mlflow-artifacts]

Prints "created" or "exists"; exits non-zero if MinIO cannot be reached.
"""
import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", default="http://localhost:9001")
    ap.add_argument("--bucket", default="mlflow-artifacts")
    ap.add_argument("--wait", type=int, default=60, help="seconds to wait for MinIO to answer")
    a = ap.parse_args()

    user, password = os.environ.get("MINIO_ROOT_USER"), os.environ.get("MINIO_ROOT_PASSWORD")
    if not user or not password:
        print("MINIO_ROOT_USER and MINIO_ROOT_PASSWORD must be set", file=sys.stderr)
        return 2
    s3 = boto3.client("s3", endpoint_url=a.endpoint, aws_access_key_id=user,
                      aws_secret_access_key=password, region_name="us-east-1")

    deadline = time.time() + a.wait
    while True:
        try:
            s3.head_bucket(Bucket=a.bucket)
            print("exists")
            return 0
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchBucket", "NotFound"):
                s3.create_bucket(Bucket=a.bucket)
                print("created")
                return 0
            print(f"MinIO refused: {e.response['Error']['Code']}", file=sys.stderr)
            return 1
        except EndpointConnectionError as e:
            if time.time() > deadline:
                print(f"MinIO not reachable at {a.endpoint}: {e}", file=sys.stderr)
                return 1
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
