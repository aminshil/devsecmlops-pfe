# ── DevSecMLOps API image ──────────────────────────────────────────
# Model is BAKED IN: every image tag = one specific model version.
# Rationale: 2MB model + 1MB baselines JSON; rebuild costs ~10s;
#            rollback = kubectl rollout undo (previous tag still in registry).
FROM python:3.10-slim

# ── Security: patch the base image's OS packages ──────────────────────────
# python:3.10-slim's Debian packages accumulate CVEs between upstream base-
# image rebuilds (util-linux, perl, openssl, sqlite, pcre2, gzip were all
# flagged by Trivy). `apt-get upgrade` pulls Debian's own patched builds of
# whatever is already installed -- it does not add packages, so image size
# is essentially unchanged. Placed first so this layer caches independently
# of application code and only invalidates when Debian ships new patches.
RUN apt-get update -qq && \
    apt-get upgrade -y -qq && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ── Security: run as non-root (Trivy/SonarQube quality gates will check) ──
RUN groupadd -r -g 10001 appuser && useradd -r -u 10001 -g appuser -m -d /home/appuser appuser

WORKDIR /app

# ── Install deps FIRST (layer caching: deps change less often than code) ──
# --no-cache-dir keeps the image small
COPY requirements-api.txt .
# setuptools>=78.1.1 fixes CVE-2025-47273 (path traversal in PackageIndex);
# msgpack>=1.2.1 fixes GHSA-6v7p-g79w-8964 (out-of-bounds read on Unpacker
# reuse) -- msgpack is a transitive dep (via boto3), pinned explicitly so the
# fix isn't silently undone by a future transitive-resolution change.
RUN pip install --no-cache-dir --upgrade pip "setuptools>=78.1.1" wheel>=0.46.2 && \
    pip install --no-cache-dir -r requirements-api.txt && \
    pip install --no-cache-dir "msgpack>=1.2.1" boto3

# ensurepip bundles its own frozen setuptools wheel purely to bootstrap
# pip in brand-new virtualenvs -- it is never imported or run by this
# image (pip is already installed above). Removing it drops a dead,
# outdated copy that Trivy otherwise flags as a live dependency.
RUN find /usr/local/lib/python3.10/ensurepip/_bundled -name "setuptools-*.whl" -delete

# ── Copy application code ──
COPY api/ ./api/
COPY ml-model/preprocess.py ./ml-model/preprocess.py
COPY ml-model/root_cause.py ./ml-model/root_cause.py

# ── Copy the ONE shipped model artifact ──
COPY models/telecom_serving_model.pkl     ./models/
COPY models/telecom_serving_baselines.json ./models/

# v3 artifacts (all small enough to bake in directly -- no MinIO fetch needed,
# unlike v2 whose 124MB RandomForest requires runtime download):
COPY models/telecom_xgb_classifier_v2.pkl    ./models/
COPY models/telecom_xgb_label_encoder_v2.pkl ./models/
# v4 rolling-features model (15 features). Loaded alongside v3; used when
# the /predict caller supplies a 'history' field. See README v4 section.
COPY models/telecom_xgb_v4_rolling.pkl         ./models/
COPY models/telecom_xgb_v4_rolling_encoder.pkl ./models/
COPY models/telecom_iso_v2.pkl               ./models/
COPY models/telecom_baselines_v2.json        ./models/

# v2 model artifacts (RandomForest, 124MB) fetched from MinIO at startup
# instead of baked in -- see docker-entrypoint.sh
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
COPY models/dependency_graph.json ./models/

# ── Runtime config (override at run/deploy time with -e MODEL_NAME=...) ──
ENV MODEL_NAME=telecom \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── (v2.12.0 previously created /app/data for the SQLite feedback DB.
# That was replaced with a proper PostgreSQL StatefulSet in K8s, so no
# local writable data directory is needed inside the container anymore.) ──

# ── Own /app as appuser and drop privileges ──
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ── Healthcheck: same /health endpoint K8s liveness probe will use ──
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" \
  || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
