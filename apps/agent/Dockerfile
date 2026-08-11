FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS builder

WORKDIR /app

ENV HF_HOME=/opt/models/huggingface \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY apps/agent/pyproject.toml apps/agent/uv.lock ./apps/agent/
COPY libs/shared/pyproject.toml /app/libs/shared/pyproject.toml
COPY libs/shared/src /app/libs/shared/src

RUN python -m pip install --no-cache-dir uv==0.11.19 \
    && uv sync --directory apps/agent --frozen --no-dev --no-editable
RUN mkdir -p "$HF_HOME" \
    && /app/.venv/bin/python -m livekit.agents download-files

FROM builder AS development

RUN uv sync --directory apps/agent --frozen --all-groups --no-editable

COPY apps/agent/agent ./agent
COPY apps/agent/dev_runner.py ./dev_runner.py

FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS runtime

WORKDIR /app

ENV HF_HOME=/opt/models/huggingface \
    HOME=/tmp \
    TMPDIR=/tmp \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/models/huggingface /opt/models/huggingface
COPY apps/agent/agent ./agent

USER 10001:10001

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/', timeout=3).read()"]

CMD ["/app/.venv/bin/python", "-m", "agent.main", "start"]
