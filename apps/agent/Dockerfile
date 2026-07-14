FROM python:3.13-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280 AS builder

WORKDIR /app

ENV HF_HOME=/opt/models/huggingface \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

RUN python -m pip install --no-cache-dir uv==0.11.19 \
    && uv sync --frozen --no-dev
RUN mkdir -p "$HF_HOME" \
    && /app/.venv/bin/python -c "from livekit.plugins.turn_detector.multilingual import _EUORunnerMultilingual; _EUORunnerMultilingual._download_files()"

FROM builder AS development

RUN uv sync --frozen --all-groups

COPY agent ./agent
COPY dev_runner.py ./dev_runner.py

FROM python:3.13-slim@sha256:eb43ff125d8d58d7449dcba7d336c23bcac412f526d861db493b9994d8010280 AS runtime

WORKDIR /app

ENV HF_HOME=/opt/models/huggingface \
    HOME=/tmp \
    TMPDIR=/tmp \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/models/huggingface /opt/models/huggingface
COPY agent ./agent

USER 10001:10001

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/', timeout=3).read()"]

CMD ["/app/.venv/bin/python", "-m", "agent.main", "start"]
