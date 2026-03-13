# AI Call Assistant

Backend and agent monorepo for the AI Call Assistant MVP.

## Apps

- `apps/api`: FastAPI backend for auth, billing, telephony coordination, webhooks, realtime, and persistence.
- `apps/agent`: LiveKit agent worker for prompt construction, provider selection, and call runtime execution.

## Local Verification

### API

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

### Agent

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

## Docker Builds

### API

```bash
cd apps/api
docker build -t ai-call-api .
```

### Agent

```bash
cd apps/agent
docker build -t ai-call-agent .
```

## Local Infra

Start the stateful dependencies first:

```bash
docker compose up -d postgres redis minio minio-init
```

Deployment-like runtime launch:

```bash
docker compose --profile app up --build api agent
```

## Local Dev Overlay

For faster iteration without rebuilding containers on every code change, use the dev overlay:

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile app up api agent
```

What it does:
- `api` bind-mounts [apps/api/app](/home/i933k/code/ai/bmad-opevo/.worktrees/backend-foundation-mvp/apps/api/app) and runs `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- `agent` bind-mounts [apps/agent/agent](/home/i933k/code/ai/bmad-opevo/.worktrees/backend-foundation-mvp/apps/agent/agent) and runs `python dev_runner.py`, which restarts the worker when Python files change

Important limits:
- Source edits reload automatically, but dependency changes still require rebuilding the image
- Agent code changes restart the worker process, so any active call in flight will be interrupted

Useful commands:

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile app logs -f api agent
docker compose -f compose.yaml -f compose.dev.yaml --profile app restart api agent
```

Local service versions:
- PostgreSQL `17.8`
- Redis `7.4.7`
- MinIO `RELEASE.2025-07-23T15-54-02Z`

Core local endpoints:
- PostgreSQL: `postgresql+asyncpg://postgres:postgres@localhost:5432/ai_call`
- Redis: `redis://localhost:6379/0`
- MinIO API: `http://localhost:9000`
- MinIO console: `http://localhost:9001`
- MinIO bucket: `recordings`

## Staging Checklist

- [ ] API starts with real Postgres and Redis
- [ ] Agent worker starts with real LiveKit credentials
- [ ] Clerk webhook reaches `/webhooks/clerk`
- [ ] Stripe webhook resets minutes
- [ ] Telnyx number can be provisioned
- [ ] LiveKit dispatch reaches the agent
- [ ] Post-call summary, recording metadata, notification persistence, and usage deduction complete successfully
