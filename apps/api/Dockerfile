# syntax=docker/dockerfile:1
FROM python:3.12.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/apps/api
WORKDIR /app
COPY apps/api/requirements.lock /app/requirements.lock
# Optional BuildKit trust bundle is for managed environments with an HTTPS proxy.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir --require-hashes -r /app/requirements.lock; \
    else \
      pip install --no-cache-dir --require-hashes -r /app/requirements.lock; \
    fi
COPY --chown=10001:10001 apps/api /app/apps/api
RUN pip install --no-cache-dir --no-deps --no-build-isolation /app/apps/api \
    && useradd --create-home --uid 10001 sentinelops
COPY --chown=10001:10001 infra/worker_service.py /app/infra/worker_service.py
COPY --chown=10001:10001 evals /app/evals
USER 10001:10001
EXPOSE 8000 8080
# PORT makes the same image usable by Cloud Run without a second Dockerfile.
CMD ["sh", "-c", "exec uvicorn sentinelops.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
