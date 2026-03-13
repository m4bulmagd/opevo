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

## Staging Checklist

- [ ] API starts with real Postgres and Redis
- [ ] Agent worker starts with real LiveKit credentials
- [ ] Clerk webhook reaches `/webhooks/clerk`
- [ ] Stripe webhook reaches `/webhooks/stripe`
- [ ] Telnyx provisioning and number state switching are wired with production credentials
- [ ] LiveKit webhook reaches `/webhooks/livekit`
- [ ] A real forwarded phone call creates a pending call, dispatches the agent, and emits realtime events
