from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_deployment_docs_cover_staging_checklist_and_local_infra() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    compose = (REPO_ROOT / "compose.yaml").read_text()
    api_env = (REPO_ROOT / "apps" / "api" / ".env.example").read_text()
    agent_env = (REPO_ROOT / "apps" / "agent" / ".env.example").read_text()
    backend_context = (REPO_ROOT / "docs" / "architecture" / "backend-context.md").read_text()

    assert "## Local Infra" in readme
    assert "docker compose up -d postgres redis minio minio-init" in readme
    assert "- [ ] API starts with real Postgres and Redis" in readme
    assert "- [ ] Agent worker starts with real LiveKit credentials" in readme
    assert "- [ ] Clerk webhook reaches `/webhooks/clerk`" in readme
    assert "- [ ] Stripe webhook resets minutes" in readme
    assert "- [ ] Telnyx number can be provisioned" in readme
    assert "- [ ] LiveKit dispatch reaches the agent" in readme

    assert 'image: postgres:17.8-bookworm' in compose
    assert 'image: redis:7.4.7-alpine' in compose
    assert 'image: quay.io/minio/minio:RELEASE.2025-07-23T15-54-02Z' in compose
    assert 'image: quay.io/minio/mc:RELEASE.2025-07-21T05-28-08Z' in compose
    assert 'MINIO_ROOT_USER: minioadmin' in compose
    assert 'MINIO_ROOT_PASSWORD: minioadmin' in compose
    assert 'STORAGE_BUCKET_NAME: recordings' in compose

    assert "STORAGE_BUCKET_NAME=recordings" in api_env
    assert "S3_ENDPOINT_URL=http://minio:9000" in api_env
    assert "S3_ACCESS_KEY=minioadmin" in api_env
    assert "S3_SECRET_KEY=minioadmin" in api_env
    assert "S3_REGION=us-east-1" in api_env
    assert "OPENAI_API_KEY=replace-me" in api_env

    assert "REDIS_URL=redis://redis:6379/0" in agent_env
    assert "OPENAI_API_KEY=replace-me" in agent_env
    assert "DEEPGRAM_API_KEY=replace-me" in agent_env
    assert "ELEVENLABS_API_KEY=replace-me" in agent_env

    assert "## Staging Smoke Status" in backend_context
    assert "Not executed in this session" in backend_context
