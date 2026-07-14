# AI Call Assistant

Backend and agent monorepo for the AI Call Assistant MVP.

## Apps

- `apps/api`: FastAPI backend for auth, billing, telephony coordination, webhooks, realtime, and persistence.
- `apps/agent`: LiveKit agent worker for prompt construction, provider selection, and call runtime execution.
- `apps/web`: Next.js customer dashboard for onboarding, agent configuration, call review, and billing.

## Self-Serve France Launch Contract

The current MVP scope is intentionally narrow:

- France-only self-serve onboarding
- one paid plan: `starter`
- one launch pipeline: `stt_llm_tts`
- automatic French number provisioning after the first fresh paid Stripe invoice
- required agent setup before routing can be enabled
- visible onboarding status in the dashboard with assigned number display and manual retry for retryable provisioning failures

Anything outside that contract, especially additional plans or `sts`, should be treated as post-MVP work until the real self-serve path is verified in staging.

## Local Verification

### API

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

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

The production web build validates its public Clerk key, server Clerk key, and
backend URL at module initialization. For a local build that is not connected to
hosted services, use explicit non-secret build-only values:

```bash
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsuZXhhbXBsZS5jb20k \
CLERK_SECRET_KEY=ci-build-only-placeholder \
API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_APP_URL=http://127.0.0.1:3000 \
npm run build
```

For local web development, start from [apps/web/.env.example](apps/web/.env.example).
The Docker `web` service reads `apps/web/.env`, and local `npm run dev` can also
use `.env.local`. Hosted auth and protected data require real public
`NEXT_PUBLIC_*` values when the web assets are built, plus the real
`CLERK_SECRET_KEY` and server-only `API_BASE_URL` values at runtime. Never pass
the Clerk secret as a Docker build argument.

### Dependency audits

Python audits use the exact hashes in each uv lockfile rather than resolving a
fresh dependency graph:

```bash
cd apps/api # repeat from apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv export --frozen --all-groups --no-emit-project \
  --format requirements-txt --output-file /tmp/presvo-requirements.txt
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync pip-audit \
  --disable-pip --require-hashes --no-deps --progress-spinner=off \
  --requirement /tmp/presvo-requirements.txt

cd ../web
npm audit --audit-level=high
```

The agent currently has five narrowly scoped, time-limited `transformers`
exceptions imposed by pinned LiveKit and Speechmatics dependency ceilings. Run
its local audit with the exact `--ignore-vuln` arguments recorded in
[the dependency exception register](docs/security/dependency-exceptions.md).
The exceptions apply only to model-loading paths the agent does not use and
must not be copied to the API or web audits.

## Continuous Integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull
request, every push to `main`, and manual dispatch. It enforces:

- API Ruff, mypy, and the complete pytest suite against PostgreSQL 17, plus
  Redis 7 availability;
- agent Ruff, mypy, and the complete pytest suite;
- web Biome, TypeScript, Vitest, and Next.js production build;
- a blank-database Alembic upgrade to `head`;
- hashed Python and locked npm dependency audits;
- full-history gitleaks scanning; and
- HIGH/CRITICAL fixed-vulnerability Trivy scans for all three application
  images.

All third-party GitHub Actions are pinned to full commit SHAs. Dependabot tracks
uv, npm, GitHub Actions, and Docker dependencies every week.

### Required GitHub branch protection

After this workflow exists on the remote default branch, configure a GitHub
ruleset targeting `main` with all of the following settings:

- Require a pull request with at least one approving review.
- Dismiss stale pull request approvals when new commits are pushed.
- Require conversation resolution and a branch that is up to date before merge.
- Require linear history.
- Require signed commits.
- Do not permit force pushes or branch deletion.
- Leave the bypass list empty for administrators and repository roles during
  beta.
- Require every check below; `CI / Required` is the stable aggregate check and
  must never replace the individual migration or security checks:
  - `CI / API`
  - `CI / Agent`
  - `CI / Web`
  - `CI / Migrations`
  - `CI / Dependency audit / api`
  - `CI / Dependency audit / agent`
  - `CI / Dependency audit / web`
  - `CI / Gitleaks`
  - `CI / Container scan / api`
  - `CI / Container scan / agent`
  - `CI / Container scan / web`
  - `CI / Required`

The ruleset is external GitHub state; committing this repository does not
activate it. Verify the exact check names from one successful remote workflow
run before saving the ruleset.

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

### Web

```bash
cd apps/web
docker build \
  --build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_replace_me \
  --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
  --build-arg NEXT_PUBLIC_APP_URL=https://app.example.com \
  -t ai-call-web .
```

The Dockerfile's default public values are non-secret localhost/CI placeholders
so credential-free checks can build the image. A deployable image must override
them as shown above. Inject `CLERK_SECRET_KEY` only when the resulting container
runs.

## Local Infra

Start the stateful dependencies first:

```bash
docker compose up -d postgres redis minio minio-init
```

Deployment-like runtime launch (the explicit env file also supplies the public
values to the web image build):

```bash
docker compose --env-file apps/web/.env --profile app up --build api worker agent web
```

The core app stack can also be started without the web container when you only need the backend and agent services:

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile app up api worker agent
```

The web container uses:
- `API_BASE_URL=http://api:8000` at runtime for server-side requests inside the Compose network
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` from `apps/web/.env` at build time
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` at build time for browser-visible URLs
- `NEXT_PUBLIC_APP_URL=http://localhost:3000` at build time for billing return URLs and app links

Compose forwards only public `NEXT_PUBLIC_*` values as build arguments. It
continues injecting `CLERK_SECRET_KEY` from `apps/web/.env` only into the running
container.

## Local Dev Overlay

For faster iteration without rebuilding containers on every code change, use the dev overlay:

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile app up api worker agent web
```

What it does:
- `api` bind-mounts [apps/api/app](apps/api/app) and runs `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- `worker` bind-mounts [apps/api/app](apps/api/app) and runs `uv run arq app.workers.arq_worker.WorkerSettings`
- `agent` bind-mounts [apps/agent/agent](apps/agent/agent) and runs `python dev_runner.py`, which restarts the worker when Python files change
- `web` bind-mounts [apps/web](apps/web) and runs `npm run dev -- --hostname 0.0.0.0`

Frontend-specific notes:
- The dashboard keeps the template shell, colors, and theme presets: `default`, `brutalist`, `soft-pop`, and `tangerine`
- Product routes live under `/dashboard`, `/dashboard/calls`, `/dashboard/agent`, and `/dashboard/billing`
- Without Clerk env vars, the UI renders setup notices instead of the hosted sign-in flow

Important limits:
- Source edits reload automatically, but dependency changes still require rebuilding the image
- API worker code changes require restarting the `worker` container in the dev overlay
- Agent code changes restart the worker process, so any active call in flight will be interrupted

Useful commands:

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile app logs -f api worker agent web
docker compose -f compose.yaml -f compose.dev.yaml --profile app restart api worker agent web
```

Live STT/LLM/TTS debug logs:

```bash
# set in apps/agent/.env
AGENT_DEBUG_STREAMS=true

docker compose --profile app up -d --build agent
docker compose --profile app logs -f agent
```

When enabled, the agent emits structured `agent.debug` log lines for:
- `stt.*`
- `llm.start`, `llm.delta`, `llm.complete`
- `tts.start`, `tts.first_frame`, `tts.complete`, `tts.error`

Voice turn-taking defaults:
- `LIVEKIT_SILERO_VAD_ENABLED=true` enables LiveKit Silero VAD in the agent session
- `LIVEKIT_TURN_DETECTOR_ENABLED=true` enables the LiveKit multilingual turn detector in the agent session
- `SPEECHMATICS_TURN_DETECTION_MODE=adaptive` keeps Speechmatics endpointing in adaptive mode underneath the LiveKit turn-taking layer

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

For the current backend implementation and partial staging-smoke status, see
[backend-context.md](docs/architecture/backend-context.md). The checklist below
is the intended France self-serve smoke path, not a claim that every item is
already complete.

- [ ] API starts with real Postgres and Redis
- [ ] Agent worker starts with real LiveKit credentials
- [ ] Clerk webhook reaches `/webhooks/clerk`
- [ ] Stripe webhook resets minutes
- [ ] Telnyx number can be provisioned
- [ ] LiveKit dispatch reaches the agent
- [ ] New Clerk user signs up and reaches the dashboard
- [ ] User starts the `starter` Stripe checkout flow
- [ ] First fresh `invoice.paid` activates the subscription and enqueues France number provisioning
- [ ] A French number is assigned automatically and shown in onboarding status
- [ ] User completes agent setup and enables routing without staff intervention
- [ ] LiveKit dispatch reaches the agent for the assigned number
- [ ] One real inbound call persists transcript, summary, recording metadata, and minute deduction
