FROM node:24.18.0-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# The browser revision and Linux dependencies must match the pinned Python package.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    BIGKINDS_RUNTIME=server \
    BIGKINDS_DATA_DIR=/data

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv tini xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt ./requirements.lock.txt
RUN python -m venv /opt/venv \
    && python -m pip install --no-cache-dir -r requirements.lock.txt

COPY run_web.py ./run_web.py
COPY src/ ./src/
COPY scripts/playwright_runtime_smoke.py ./scripts/playwright_runtime_smoke.py
COPY frontend/package.json ./frontend/package.json
COPY --from=frontend-build /build/frontend/dist/ ./frontend/dist/

RUN mkdir -p /data
RUN PYTHONPATH=/app python scripts/playwright_runtime_smoke.py
EXPOSE 8000

# tini reaps browser descendants; the Python launcher owns Xvfb and worker shutdown.
ENTRYPOINT ["/usr/bin/tini", "-s", "--"]
CMD ["python", "run_web.py"]
