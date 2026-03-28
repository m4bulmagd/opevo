from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_deployment_docs_cover_staging_checklist_and_local_infra() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    compose = (REPO_ROOT / "compose.yaml").read_text()
    compose_dev = (REPO_ROOT / "compose.dev.yaml").read_text()
    api_env = (REPO_ROOT / "apps" / "api" / ".env.example").read_text()
    agent_env = (REPO_ROOT / "apps" / "agent" / ".env.example").read_text()
    api_dockerfile = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text()
    api_entrypoint = (REPO_ROOT / "apps" / "api" / "docker-entrypoint.sh").read_text()
    agent_dockerfile = (REPO_ROOT / "apps" / "agent" / "Dockerfile").read_text()
    backend_context = (REPO_ROOT / "docs" / "architecture" / "backend-context.md").read_text()

    assert "## Local Infra" in readme
    assert "docker compose up -d postgres redis minio minio-init" in readme
    assert "- [ ] API starts with real Postgres and Redis" in readme
    assert "- [ ] Agent worker starts with real LiveKit credentials" in readme
    assert "- [ ] Clerk webhook reaches `/webhooks/clerk`" in readme
    assert "- [ ] Stripe webhook resets minutes" in readme
    assert "- [ ] Telnyx number can be provisioned" in readme
    assert "- [ ] LiveKit dispatch reaches the agent" in readme
    assert "docker compose -f compose.yaml -f compose.dev.yaml --profile app up api worker agent" in readme
    assert "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload" in readme
    assert "uv run arq app.workers.arq_worker.WorkerSettings" in readme
    assert "python dev_runner.py" in readme

    assert 'image: postgres:17.8-bookworm' in compose
    assert 'image: redis:7.4.7-alpine' in compose
    assert 'image: quay.io/minio/minio:RELEASE.2025-07-23T15-54-02Z' in compose
    assert 'image: quay.io/minio/mc:RELEASE.2025-07-21T05-28-08Z' in compose
    assert 'MINIO_ROOT_USER: minioadmin' in compose
    assert 'MINIO_ROOT_PASSWORD: minioadmin' in compose
    assert 'STORAGE_BUCKET_NAME: recordings' in compose
    assert 'env_file:' in compose
    assert '- ./apps/api/.env' in compose
    assert '- ./apps/agent/.env' in compose
    assert 'DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/ai_call' in compose
    assert 'REDIS_URL: redis://redis:6379/0' in compose
    assert 'container_name: ai-call-worker' in compose
    assert 'command: ["uv", "run", "arq", "app.workers.arq_worker.WorkerSettings"]' in compose
    assert 'API_BASE_URL: http://api:8000' in compose
    assert '${LIVEKIT_API_KEY:-replace-me}' not in compose
    assert '${LIVEKIT_API_SECRET:-replace-me}' not in compose
    assert '${TELNYX_API_KEY:-replace-me}' not in compose
    assert '${AGENT_INTERNAL_API_TOKEN:-replace-me}' not in compose

    assert "STORAGE_BUCKET_NAME=recordings" in api_env
    assert "S3_ENDPOINT_URL=http://minio:9000" in api_env
    assert "S3_ACCESS_KEY=minioadmin" in api_env
    assert "S3_SECRET_KEY=minioadmin" in api_env
    assert "S3_REGION=us-east-1" in api_env
    assert "CLERK_JWKS_URL=replace-me" in api_env
    assert "STRIPE_SECRET_KEY=replace-me" in api_env
    assert "TELNYX_ORDERING_ENABLED=false" in api_env
    assert "STRIPE_PRICE_STARTER=price_replace_me" in api_env
    assert "STRIPE_PRICE_STANDARD=price_replace_me" in api_env
    assert "STRIPE_CHECKOUT_SUCCESS_URL=https://your-app.example.com/billing/success" in api_env
    assert "STRIPE_CHECKOUT_CANCEL_URL=https://your-app.example.com/billing/cancel" in api_env
    assert "CLERK_JWT_SECRET=replace-me" not in api_env
    assert "OPENAI_API_KEY=replace-me" not in api_env

    assert "REDIS_URL=redis://redis:6379/0" in agent_env
    assert "API_BASE_URL=http://api:8000" in agent_env
    assert "AGENT_INTERNAL_API_TOKEN=replace-me" in agent_env
    assert "GEMINI_API_KEY=replace-me" in agent_env
    assert "OPENAI_API_KEY=replace-me" not in agent_env
    assert "DEEPGRAM_API_KEY=replace-me" in agent_env
    assert "ELEVENLABS_API_KEY=replace-me" in agent_env

    assert "COPY alembic.ini ./" in api_dockerfile
    assert "COPY alembic ./alembic" in api_dockerfile
    assert 'CMD ["./docker-entrypoint.sh"]' in api_dockerfile
    assert "uv run alembic -c alembic.ini upgrade head" in api_entrypoint
    assert 'exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000' in api_entrypoint

    assert 'CMD ["uv", "run", "python", "-m", "agent.main", "start"]' in agent_dockerfile

    assert "services:" in compose_dev
    assert "volumes:" in compose_dev
    assert "./apps/api/app:/app/app" in compose_dev
    assert "./apps/agent/agent:/app/agent" in compose_dev
    assert "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload" in compose_dev
    assert 'command: ["uv", "run", "arq", "app.workers.arq_worker.WorkerSettings"]' in compose_dev
    assert "dev_runner.py" in compose_dev

    assert "## Staging Smoke Status" in backend_context
    assert "Partially executed on 2026-03-16." in backend_context
    assert "Queue-backed call finalization" in backend_context
    assert "phone_number_provisioning_review_required" in backend_context
