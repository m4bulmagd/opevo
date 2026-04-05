# AI Call Assistant

Backend and agent monorepo for the AI Call Assistant MVP.

## Apps

- `apps/api`: FastAPI backend for auth, billing, telephony coordination, webhooks, realtime, and persistence.
- `apps/agent`: LiveKit agent worker for prompt construction, provider selection, and call runtime execution.
- `apps/web`: Next.js customer dashboard for onboarding, agent configuration, call review, and billing.

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

### Web

```bash
cd apps/web
npm install
npm run test -- --run
npm run lint
npm run build
```

For local web development, start from [apps/web/.env.example](/home/i933k/code/ai/bmad-opevo/apps/web/.env.example). The Docker `web` service reads `apps/web/.env`, and local `npm run dev` can also use `.env.local` if you prefer. The dashboard builds without Clerk keys, but hosted auth and protected data only work when `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` are configured.

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
docker build -t ai-call-web .
```

## Local Infra

Start the stateful dependencies first:

```bash
docker compose up -d postgres redis minio minio-init
```

Deployment-like runtime launch:

```bash
docker compose --profile app up --build api worker agent web
```

The web container uses:
- `API_BASE_URL=http://api:8000` for server-side requests inside the Compose network
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` for browser-visible URLs
- `NEXT_PUBLIC_APP_URL=http://localhost:3000` for billing return URLs and app links

## Local Dev Overlay

For faster iteration without rebuilding containers on every code change, use the dev overlay:

```bash
docker compose -f compose.yaml -f compose.dev.yaml --profile app up api worker agent web
```

What it does:
- `api` bind-mounts [apps/api/app](/home/i933k/code/ai/bmad-opevo/apps/api/app) and runs `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`
- `worker` bind-mounts [apps/api/app](/home/i933k/code/ai/bmad-opevo/apps/api/app) and runs `uv run arq app.workers.arq_worker.WorkerSettings`
- `agent` bind-mounts [apps/agent/agent](/home/i933k/code/ai/bmad-opevo/apps/agent/agent) and runs `python dev_runner.py`, which restarts the worker when Python files change
- `web` bind-mounts [apps/web](/home/i933k/code/ai/bmad-opevo/apps/web) and runs `npm run dev -- --hostname 0.0.0.0`

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

For the current backend implementation and partial staging-smoke status, see [backend-context.md](/home/i933k/code/ai/bmad-opevo/docs/architecture/backend-context.md). The checklist below is the intended smoke path, not a claim that every item is already complete.

- [ ] API starts with real Postgres and Redis
- [ ] Agent worker starts with real LiveKit credentials
- [ ] Clerk webhook reaches `/webhooks/clerk`
- [ ] Stripe webhook resets minutes
- [ ] Telnyx number can be provisioned
- [ ] LiveKit dispatch reaches the agent
- [ ] Post-call summary, recording metadata, notification persistence, and usage deduction complete successfully
