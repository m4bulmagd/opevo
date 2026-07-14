# JWT-Only Agent Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy static agent token so transcript append and call completion use only short-lived, call-scoped dispatch JWTs in every environment.

**Architecture:** The API worker signs a JWT with `AGENT_DISPATCH_JWT_SECRET` and places it in trusted dispatch metadata. The agent forwards that call token in `x-agent-token`; the API verifies its signature, expiry, call ID, user ID, and agent-configuration ownership without any environment-specific bypass. The signing secret remains confined to the API and worker.

**Tech Stack:** Python 3.13, FastAPI, Pydantic Settings, PyJWT, HTTPX, pytest, Ruff, mypy, Docker Compose.

## Global Constraints

- Do not change LiveKit SDK APIs, SIP routing, dispatch creation, JWT claims, JWT lifetime, or LiveKit project credentials.
- Do not expose, copy, log, commit, or place real secret values in patches.
- `AGENT_DISPATCH_JWT_SECRET` exists only in `apps/api/.env` and the API/worker runtime; the agent receives generated JWTs, never the signing secret.
- Preserve the existing HTTP 401 `{"detail": "Invalid agent token"}` response for invalid API authentication.
- Preserve all unrelated worktree changes, including `apps/web/next-env.d.ts`, `compose.dev.yaml`, `docs/Verdict.md`, and landing-page assets.
- Follow red-green-refactor: observe each new expectation fail against the current fallback before modifying production code.

---

### Task 1: Make API Agent Endpoints JWT-Only

**Files:**
- Modify: `apps/api/tests/agent/test_call_completion.py`
- Modify: `apps/api/tests/agent/test_transcript_append.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: `apps/api/tests/test_redaction.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/core/config.py`

**Interfaces:**
- Consumes: `verify_dispatch_token(token: str, expected_call_id: str, expected_user_id: str | None = None) -> dict`.
- Produces: `require_agent_auth(...) -> AuthenticatedAgentIdentity` that accepts only a valid call-scoped JWT in all environments.

- [ ] **Step 1: Extend the existing static-token rejection test to development**

Rename the test and include `development` in its environment matrix:

```python
@pytest.mark.anyio
@pytest.mark.parametrize(
    "app_env",
    ["development", "test", "staging", "production"],
)
async def test_static_agent_token_is_rejected_in_every_environment(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
) -> None:
    _configure_auth(monkeypatch, app_env=app_env)
    call_id = uuid4()
    fake_queue = FakeCallFinalizationQueue()
    app = _build_completion_app(fake_queue)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/agent/calls/{call_id}/complete",
            headers={"x-agent-token": "test-agent-token"},
            json={"duration_seconds": 1},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid agent token"}
    assert fake_queue.calls == []
```

- [ ] **Step 2: Run the new API expectation and verify RED**

Run:

```bash
cd apps/api
PYTHONPATH=. .venv/bin/python -m pytest -q 'tests/agent/test_call_completion.py::test_static_agent_token_is_rejected_in_every_environment'
```

Expected: the `development` case fails because the current HMAC fallback accepts `test-agent-token`; the `test`, `staging`, and `production` cases pass.

- [ ] **Step 3: Remove the API static-token setting and authentication branch**

Delete `import hmac` from `apps/api/app/routers/agent.py`, delete
`agent_internal_api_token` from `Settings`, and make `require_agent_auth`
start directly with JWT presence and verification:

```python
async def require_agent_auth(
    call_id: UUID,
    x_agent_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthenticatedAgentIdentity:
    if not isinstance(x_agent_token, str) or not x_agent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        )

    try:
        claims = verify_dispatch_token(
            x_agent_token,
            expected_call_id=str(call_id),
        )
        signed_user_id = UUID(claims["user_id"])
        signed_agent_config_id = UUID(claims["agent_config_id"])
    except (DispatchTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        ) from None
```

Keep the existing `Call` and `AgentConfig` ownership checks immediately after
this block unchanged.

- [ ] **Step 4: Run the API expectation and verify GREEN**

Run:

```bash
cd apps/api
PYTHONPATH=. .venv/bin/python -m pytest -q 'tests/agent/test_call_completion.py::test_static_agent_token_is_rejected_in_every_environment'
```

Expected: all four environment cases pass.

- [ ] **Step 5: Refactor API fixtures and fallback-only tests**

Change `_configure_auth` so it configures only the environment and dispatch
secret:

```python
def _configure_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_env: str,
    dispatch_secret: str | None = None,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    if dispatch_secret is None:
        monkeypatch.delenv("AGENT_DISPATCH_JWT_SECRET", raising=False)
    else:
        monkeypatch.setenv("AGENT_DISPATCH_JWT_SECRET", dispatch_secret)

    from app.core.config import get_settings

    get_settings.cache_clear()
```

Then:

- remove every `static_token=""` argument from `_configure_auth` calls;
- remove `AGENT_INTERNAL_API_TOKEN` from the autouse environment fixture in
  `apps/api/tests/conftest.py`;
- delete `test_development_static_auth_returns_404_for_missing_call`, because
  the environment-matrix rejection test now covers the former static path;
- remove the `agent_internal_api_token` sentinel from
  `test_safe_extra_filter_removes_aliases_through_handler_and_formatter` while
  preserving the other secret-redaction sentinels.

- [ ] **Step 6: Run focused API authentication tests**

Run:

```bash
cd apps/api
PYTHONPATH=. .venv/bin/python -m pytest -q tests/auth/test_jwt_auth.py 'tests/agent/test_call_completion.py::test_static_agent_token_is_rejected_in_every_environment'
```

Expected: all selected tests pass with no failures.

- [ ] **Step 7: Commit the API JWT-only boundary**

```bash
git add apps/api/app/core/config.py apps/api/app/routers/agent.py apps/api/tests/conftest.py apps/api/tests/agent/test_call_completion.py apps/api/tests/agent/test_transcript_append.py apps/api/tests/test_redaction.py
git commit -m "refactor: require dispatch JWT for agent API"
```

---

### Task 2: Require Dispatch Metadata in the Agent Client

**Files:**
- Modify: `apps/agent/tests/test_api_client.py`
- Modify: `apps/agent/tests/test_runtime_validation.py`
- Modify: `apps/agent/agent/api_client.py`
- Modify: `apps/agent/agent/config.py`

**Interfaces:**
- Consumes: dispatch metadata field `dispatch_token: str` and API header `x-agent-token`.
- Produces: `AgentApiClient.complete_call(payload: dict) -> dict` that raises `ValueError("Dispatch token is required")` before I/O whenever the payload lacks a non-empty dispatch token.

- [ ] **Step 1: Replace the development fallback test with a JWT-required expectation**

Keep a legacy value in the temporary red test fixture so the current code can
demonstrate the unwanted behavior:

```python
@pytest.mark.anyio
async def test_complete_call_requires_dispatch_token_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.api_client.get_settings",
        lambda: SimpleNamespace(
            api_base_url="http://test",
            agent_internal_api_token="development-static-token",
            api_timeout_seconds=10.0,
            api_max_retries=3,
            app_env="development",
        ),
    )

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "status": "accepted",
                "queued": True,
                "job_id": "call-finalization:call_without_dispatch",
            },
        )
    )
    mock_client = httpx.AsyncClient(transport=transport)
    client = AgentApiClient(base_url="http://test", http_client=mock_client)
    try:
        with pytest.raises(ValueError, match="Dispatch token is required"):
            await client.complete_call(
                {"call_id": "call_without_dispatch", "duration_seconds": 1}
            )
    finally:
        await mock_client.aclose()
```

- [ ] **Step 2: Run the agent expectation and verify RED**

Run:

```bash
cd apps/agent
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_api_client.py::test_complete_call_requires_dispatch_token_in_development
```

Expected: FAIL with `DID NOT RAISE ValueError` because the current client uses the development static token.

- [ ] **Step 3: Remove static-token state and require the dispatch token**

Delete `agent_internal_api_token` from `AgentSettings`. Remove the
`agent_token` constructor parameter, `self.agent_token`, and `self.app_env`
from `AgentApiClient` so its constructor is:

```python
def __init__(
    self,
    *,
    base_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> None:
    settings = get_settings()
    self.base_url = (base_url or settings.api_base_url).rstrip("/")
    self.http_client = http_client
    self._owns_http_client = http_client is None
    self.timeout = timeout if timeout is not None else settings.api_timeout_seconds
    self.max_retries = (
        max_retries if max_retries is not None else settings.api_max_retries
    )
```

Replace the opening of `complete_call` with:

```python
async def complete_call(self, payload: dict) -> dict:
    token = payload.get("dispatch_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Dispatch token is required")

    url = f"{self.base_url}/api/agent/calls/{payload['call_id']}/complete"
    headers = {"x-agent-token": token}
```

Keep the request body, retry policy, acknowledgement validation, and token
exclusion from the JSON body unchanged.

- [ ] **Step 4: Run the agent expectation and verify GREEN**

Run:

```bash
cd apps/agent
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_api_client.py::test_complete_call_requires_dispatch_token_in_development
```

Expected: PASS.

- [ ] **Step 5: Remove legacy fields from final agent tests**

- remove `agent_token=None` from direct `AgentApiClient` construction;
- remove `agent_internal_api_token` and unnecessary `app_env` attributes from
  `SimpleNamespace` API-client settings doubles;
- replace the environment-parametrized missing-token test with one
  environment-independent `test_complete_call_requires_dispatch_token` test;
- remove `agent_internal_api_token` from `AgentSettings` fixtures;
- delete `test_agent_production_does_not_require_legacy_static_api_token`.

The final missing-token test must remain:

```python
@pytest.mark.anyio
async def test_complete_call_requires_dispatch_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.api_client.get_settings",
        lambda: SimpleNamespace(
            api_base_url="http://test",
            api_timeout_seconds=10.0,
            api_max_retries=3,
        ),
    )

    client = AgentApiClient(base_url="http://test")
    with pytest.raises(ValueError, match="Dispatch token is required"):
        await client.complete_call({"call_id": "call_1", "duration_seconds": 1})
```

- [ ] **Step 6: Run the focused agent suite**

Run:

```bash
cd apps/agent
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_api_client.py tests/test_runtime_validation.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the agent JWT-only client**

```bash
git add apps/agent/agent/api_client.py apps/agent/agent/config.py apps/agent/tests/test_api_client.py apps/agent/tests/test_runtime_validation.py
git commit -m "refactor: remove static agent API token"
```

---

### Task 3: Remove Legacy Configuration and Operational Documentation

**Files:**
- Modify: `apps/api/.env.example`
- Modify: `apps/agent/.env.example`
- Modify locally without committing secrets: `apps/agent/.env`
- Modify: `docs/architecture/integration-endpoints.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`
- Modify: `docs/runbooks/credential-rotation.md`

**Interfaces:**
- Consumes: the JWT-only runtime contract implemented in Tasks 1 and 2.
- Produces: configuration and operational guidance with one agent-authentication mechanism and no agent-side signing secret.

- [ ] **Step 1: Clean tracked environment examples**

In `apps/api/.env.example`, make the authentication block:

```dotenv
REALTIME_ENABLED=false
# Use a long, random secret shared only by the API and worker.
AGENT_DISPATCH_JWT_SECRET=replace-with-a-long-random-secret
# Covers the 3600-second maximum call plus dispatch/finalization grace.
AGENT_DISPATCH_JWT_TTL_SECONDS=7200
```

In `apps/agent/.env.example`, remove the stale compatibility comment so the
API communication block is:

```dotenv
API_BASE_URL=http://api:8000
REDIS_URL=redis://redis:6379/0
```

Preserve the already-present removal of the static-token line in that dirty
file and do not modify its unrelated content.

- [ ] **Step 2: Update current architecture and operations docs**

- replace the integration-endpoint fallback bullet with
  `- rejects static shared tokens in every environment`;
- in the staging smoke runbook, replace the API static-token line with
  `AGENT_DISPATCH_JWT_SECRET=<long-random-api-and-worker-secret>` and remove the
  agent static-token line;
- remove the `agent internal token` row from the credential-rotation inventory
  while retaining the dispatch JWT secret row.

- [ ] **Step 3: Remove the ignored local agent token without exposing it**

Remove the complete line whose name is `AGENT_INTERNAL_API_TOKEN` from
`apps/agent/.env` using an in-memory `apply_patch` operation that does not emit
the value. Do not add `AGENT_DISPATCH_JWT_SECRET` to the agent environment.
Confirm `apps/api/.env` retains the user-provided dispatch secret without
printing it.

- [ ] **Step 4: Verify no active legacy references remain**

Run:

```bash
rg -n 'AGENT_INTERNAL_API_TOKEN|agent_internal_api_token|static agent token' apps/api/app apps/api/tests apps/agent/agent apps/agent/tests apps/api/.env.example apps/agent/.env.example docs/architecture docs/runbooks
```

Expected: no output. The integration documentation may intentionally say that
static shared tokens are rejected, but it must not name or configure the
removed variable.

Also run:

```bash
rg -l '^AGENT_INTERNAL_API_TOKEN=' apps/api/.env apps/agent/.env
rg -l '^AGENT_DISPATCH_JWT_SECRET=' apps/agent/.env
```

Expected: both commands produce no output.

- [ ] **Step 5: Commit tracked configuration and documentation**

```bash
git add apps/api/.env.example apps/agent/.env.example docs/architecture/integration-endpoints.md docs/architecture/staging-smoke-runbook.md docs/runbooks/credential-rotation.md
git commit -m "docs: standardize agent auth on dispatch JWTs"
```

---

### Task 4: Full Verification and Runtime Reload

**Files:**
- Verify: `apps/api`
- Verify: `apps/agent`
- Verify local services from `compose.dev.yaml`

**Interfaces:**
- Consumes: all JWT-only code, tests, environment, and documentation changes.
- Produces: fresh automated and runtime evidence that dispatch JWT creation and agent registration are configured.

- [ ] **Step 1: Run API static analysis and full tests**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: Ruff exits 0, mypy reports no issues, and pytest reports zero
failures. The PostgreSQL test database must be reachable before the full test
command.

- [ ] **Step 2: Run agent static analysis and full tests**

Run:

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: Ruff exits 0, mypy reports no issues, and pytest reports zero
failures.

- [ ] **Step 3: Inspect the final diff without touching unrelated changes**

Run:

```bash
git diff --check 853f2d2^..HEAD
git status --short
git log -5 --oneline
```

Expected: no whitespace errors; only the user's pre-existing unrelated changes
remain uncommitted; the JWT-only design, plan, and three implementation commits
are the newest commits.

- [ ] **Step 4: Recreate the runtime services**

Run:

```bash
docker compose -f compose.dev.yaml up -d --force-recreate api worker agent
```

Expected: API, worker, and agent containers are running; API health checks pass.

- [ ] **Step 5: Verify runtime secret boundaries without printing secrets**

Run a boolean-only settings probe in the worker and an environment-presence
probe in the agent. Expected worker result:

```text
dispatch_secret_set=True dispatch_secret_safe=True agent_name=ai-call-agent
```

Expected agent result:

```text
dispatch_signing_secret_present=False legacy_static_token_present=False
```

Inspect fresh agent logs and confirm the worker registers with LiveKit. Make a
fresh inbound call because earlier `dispatch_configuration` outbox records are
terminal and are not retried.

- [ ] **Step 6: Verify the fresh call dispatch result**

Query the newest `livekit.dispatch` outbox row and associated call without
printing dispatch metadata. Expected results:

- outbox status is `delivered` rather than `failed`;
- `last_error_code` is null;
- the call has a non-empty `livekit_dispatch_id`;
- agent logs show receipt of the corresponding job.

If LiveKit or an AI provider fails after dispatch delivery, report that later
stage separately; do not reclassify it as the removed static-token problem.
