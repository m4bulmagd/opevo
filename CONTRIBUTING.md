# Contributing to Presvo

Thanks for helping improve Presvo. The project is in active development,
production-oriented and locally verified, but not production-certified.
Focused fixes, tests, documentation corrections, and small product improvements
are easier to review than broad rewrites.

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

### Shared wire contracts

The API and agent share the small, versioned `presvo-contracts` package. Run
its focused package checks from the package directory:

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src
```

The API and agent remain independent uv projects. Refresh **both** application
lockfiles when the shared package's dependency graph changes (for example, a
shared runtime dependency is added, removed, or constrained differently). A
shared source-only change does not require lockfile churn. Local uv installs
may use the editable path dependency for fast iteration; production images use
`uv sync --no-editable`, so their runtime environment contains an installed
copy rather than a source-path reference.

API and agent images intentionally use the repository root as their Docker
build context so they can install `libs/shared`; run these commands from the
repository root:

```bash
docker build --file apps/api/Dockerfile --tag presvo-api:local .
docker build --file apps/agent/Dockerfile --tag presvo-agent:local .
```

### API

Start isolated CI-equivalent PostgreSQL and Redis services before running the
API suite. These commands require ports `5432` and `6379` to be free; stop the
local development stack first if it is running.

```bash
docker run --detach --rm --name presvo-api-test-postgres \
  --env POSTGRES_DB=ai_call_test \
  --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD=postgres \
  --publish 127.0.0.1:5432:5432 \
  --health-cmd='pg_isready -U postgres -d ai_call_test' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  postgres:17.8-bookworm
docker run --detach --rm --name presvo-api-test-redis \
  --publish 127.0.0.1:6379:6379 \
  --health-cmd='redis-cli ping' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  redis:7.4.7-alpine
```

Wait until both containers report `healthy`:

```bash
until docker inspect --format '{{.State.Health.Status}}' presvo-api-test-postgres | grep -qx healthy; do sleep 1; done
until docker inspect --format '{{.State.Health.Status}}' presvo-api-test-redis | grep -qx healthy; do sleep 1; done
```

Then run the checks with the same non-secret service URLs as CI.
`TEST_DATABASE_URL` is required; without it, the PostgreSQL integration and
migration-proof tests intentionally skip.

```bash
cd apps/api
export APP_ENV=test
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test
export REDIS_URL=redis://127.0.0.1:6379/0
export TEST_REDIS_URL=redis://127.0.0.1:6379/0
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
uv run --frozen --no-sync python -m pytest -q \
  --cov=app \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

For focused API pytest runs while iterating, omit the coverage flags and do not
run the coverage checker. `pytest-timeout` gives every API test, including
setup and teardown, a 60-second deadline.

Remove the isolated services when verification is complete:

```bash
docker rm --force presvo-api-test-postgres presvo-api-test-redis
```

### Agent

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
uv run --frozen --no-sync python -m pytest -q \
  --cov=agent \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

For focused agent pytest runs while iterating, omit the coverage flags and do
not run the coverage checker. `pytest-timeout` gives every ordinary agent
test, including setup and teardown, a 30-second deadline; only credentialed,
manual LiveKit evaluations use their explicit 180-second deadline.

The committed `coverage-baseline.json` files are measured quality gates. A
coverage decrease requires adding or improving tests, never lowering a
baseline. Raise a baseline in the same change only for a repeatable coverage
improvement attributable to its code or test changes, using the new measured
value rounded down to two decimal places. Do not raise a baseline from a single
higher run caused by stochastic execution-path variance.

### Web

```bash
cd apps/web
npm ci
npm run check
npm run typecheck
npm run test:ci
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsuZXhhbXBsZS5jb20k \
CLERK_SECRET_KEY=ci-build-only-placeholder \
API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_APP_URL=http://127.0.0.1:3000 \
NEXT_PUBLIC_REALTIME_ENABLED=false \
  npm run build
```

These build-only values match CI and are not provider credentials. Real Clerk
or other provider secrets are not required for repository verification.

The complete workflow, blank-database migration check, and exact required
GitHub ruleset checks are documented in
[`docs/engineering/ci-and-branch-protection.md`](docs/engineering/ci-and-branch-protection.md).

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
