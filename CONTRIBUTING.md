# Contributing to Presvo

Thanks for helping improve Presvo. The project is in active pre-production
development, so focused fixes, tests, documentation corrections, and small
product improvements are easier to review than broad rewrites.

## Before you start

- Read `README.md` for the product and repository overview.
- Read `docs/PROJECT_STATUS.md` before changing product scope or roadmap copy.
- Open an issue before starting a large feature or architectural change.
- Never include credentials, `.env` files, customer transcripts, recordings,
  or full private phone numbers in an issue, test fixture, log, or commit.

## Prerequisites

- Docker with Docker Compose
- Python 3.13 and uv 0.11.19 for local Python verification
- Node.js 22.19 for the web application

## Repository layout

- `apps/api` — FastAPI control plane, webhooks, billing, persistence, and jobs
- `apps/agent` — LiveKit voice-agent runtime
- `apps/web` — Next.js landing page and customer dashboard
- `libs/shared` — small cross-application Python contracts
- `docs` — architecture decisions, runbooks, status, plans, and specifications

## Local development

Start the core local stack from the repository root:

```bash
docker compose -f compose.dev.yaml up --build
```

This starts PostgreSQL, Redis, MinIO, migrations, the API, the ARQ worker, and
the web application. Without Clerk credentials, the web application shows
configuration notices instead of authenticated customer data.

Live phone calls require hosted provider configuration. Copy only the example
files you need, keep the resulting `.env` files untracked, and start the voice
profile after configuring LiveKit and the selected model providers:

```bash
docker compose -f compose.dev.yaml --profile voice up --build
```

The staging runbook documents the complete provider-backed path:
`docs/architecture/staging-smoke-runbook.md`.

## Verification

### API

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

The full API suite expects PostgreSQL and Redis for its integration coverage.

### Agent

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

### Web

```bash
cd apps/web
npm ci
npm run check
npm run typecheck
npm run test:ci
npm run build
```

## Change expectations

- Add or update tests for behavior changes.
- Add an Alembic migration for every database schema change.
- Keep routers thin, business rules in services, and database access in
  repositories.
- Keep provider SDK calls inside their provider adapters.
- Update `docs/PROJECT_STATUS.md` when a feature moves between status labels.
- Run the focused checks while iterating and the complete affected-app checks
  before requesting review.
- Keep commits focused and use descriptive messages.

## Security

Do not report vulnerabilities in a public issue. Follow `SECURITY.md`.
