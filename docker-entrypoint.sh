#!/bin/sh
set -e

# Fetch v2 model artifacts from MinIO at container startup if MODEL_NAME=telecom_v2
# and the files aren't already baked into the image.
if [ "$MODEL_NAME" = "telecom_v2" ]; then
  echo "MODEL_NAME=telecom_v2 -- fetching artifacts from MinIO..."

  MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://host.docker.internal:9001}"
  # Fail closed: this is a legacy path (MODEL_NAME=telecom_v2, A-B
  # comparison / reproducibility only), but it should not fall back to
  # publicly-known default credentials -- consistent with the rest of
  # this project's security posture (no committed or defaulted secrets).
  : "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY must be set for MODEL_NAME=telecom_v2}"
  : "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY must be set for MODEL_NAME=telecom_v2}"
  MINIO_BUCKET="${MINIO_BUCKET:-model-artifacts}"

  mkdir -p /app/models

  python3 -c "
import boto3
from botocore.client import Config
import sys

s3 = boto3.client(
    's3',
    endpoint_url='${MINIO_ENDPOINT}',
    aws_access_key_id='${MINIO_ACCESS_KEY}',
    aws_secret_access_key='${MINIO_SECRET_KEY}',
    config=Config(signature_version='s3v4'),
    region_name='us-east-1',
)

files = [
    ('telecom/v2/rf_classifier.pkl', '/app/models/telecom_rf_classifier_v2.pkl'),
    ('telecom/v2/iso_model.pkl', '/app/models/telecom_iso_v2.pkl'),
    ('telecom/v2/baselines.json', '/app/models/telecom_baselines_v2.json'),
]

for key, dest in files:
    print(f'  Fetching {key} -> {dest} ...')
    s3.download_file('${MINIO_BUCKET}', key, dest)
    print(f'  Done.')

print('All v2 artifacts fetched successfully.')
" || { echo "FATAL: failed to fetch model artifacts from MinIO"; exit 1; }

fi

exec "$@"
