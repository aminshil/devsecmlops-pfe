# ── DevSecMLOps API image ──────────────────────────────────────────
# Model is BAKED IN: every image tag = one specific model version.
# Rationale: 2MB model + 1MB baselines JSON; rebuild costs ~10s;
#            rollback = kubectl rollout undo (previous tag still in registry).
FROM python:3.10-slim

# ── Security: run as non-root (Trivy/SonarQube quality gates will check) ──
RUN groupadd -r appuser && useradd -r -g appuser -m -d /home/appuser appuser

WORKDIR /app

# ── Install deps FIRST (layer caching: deps change less often than code) ──
# --no-cache-dir keeps the image small
COPY requirements-api.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-api.txt

# ── Copy application code ──
COPY api/ ./api/
COPY ml-model/preprocess.py ./ml-model/preprocess.py

# ── Copy the ONE shipped model artifact ──
COPY models/telecom_serving_model.pkl     ./models/
COPY models/telecom_serving_baselines.json ./models/

# ── Runtime config (override at run/deploy time with -e MODEL_NAME=...) ──
ENV MODEL_NAME=telecom \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ── Own /app as appuser and drop privileges ──
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# ── Healthcheck: same /health endpoint K8s liveness probe will use ──
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" \
  || exit 1

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
