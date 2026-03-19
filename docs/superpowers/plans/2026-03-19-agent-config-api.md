# Agent Config API Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/agent/config` and `PATCH /api/agent/config` so authenticated users can read and update their editable agent config, with synchronous Telnyx enable/disable switching on `is_enabled` changes.

**Architecture:** Keep the public API surface small and move patch orchestration into a focused backend service. The router handles auth and HTTP responses, the repository handles fetch/update of `AgentConfig`, and the new service coordinates config mutation plus telephony side effects with rollback on failure.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async ORM, pytest, httpx

---

## File Structure

- Modify: `apps/api/app/routers/agent.py`
  - Replace the placeholder config response and add real `GET` + `PATCH` contract.
- Create: `apps/api/app/schemas/agent.py`
  - Define full config response and partial patch request models.
- Modify: `apps/api/app/repositories/agent_config_repository.py`
  - Add partial update support and keep persistence logic isolated from telephony.
- Create: `apps/api/app/services/agent_config_service.py`
  - Coordinate config patching, phone-number lookup, telephony switching, and rollback.
- Modify: `apps/api/app/repositories/phone_number_repository.py`
  - Reuse existing lookup if already sufficient; otherwise add the minimal helper needed by the new service.
- Create: `apps/api/tests/agent/test_agent_config_api.py`
  - Cover config reads, updates, toggle behavior, and rollback semantics.
- Modify: `apps/api/tests/conftest.py`
  - Add any reusable seeding helpers only if the new API tests need them.
- Modify: `docs/architecture/backend-context.md`
  - Record that the backend now exposes editable agent config once implemented.

## Chunk 1: Read And Patch Contract

### Task 1: Add the failing API tests for config read and patch

**Files:**
- Create: `apps/api/tests/agent/test_agent_config_api.py`
- Modify: `apps/api/tests/conftest.py` only if shared helpers are needed

- [ ] **Step 1: Write the failing GET config test**

Add a test that seeds a user plus agent config and then fetches it through the authenticated API:

```python
@pytest.mark.anyio
async def test_get_agent_config_returns_full_config(async_client, client_database_url, rs256_clerk_token_for):
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Ava",
        pipeline_mode="stt_llm_tts",
        is_enabled=False,
    )

    response = await async_client.get(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
    )

    assert response.status_code == 200
    assert response.json()["agent_name"] == "Ava"
    assert response.json()["pipeline_mode"] == "stt_llm_tts"
```

- [ ] **Step 2: Write the failing PATCH config test for normal field updates**

```python
@pytest.mark.anyio
async def test_patch_agent_config_updates_prompt_fields_without_toggle(async_client, client_database_url, rs256_clerk_token_for):
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        agent_name="Ava",
        pipeline_mode="stt_llm_tts",
        is_enabled=False,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={
            "agent_name": "Reception",
            "knowledge_base": "Open weekdays",
            "pipeline_mode": "sts",
        },
    )

    assert response.status_code == 200
    assert response.json()["agent_name"] == "Reception"
    assert response.json()["pipeline_mode"] == "sts"
```

- [ ] **Step 3: Run the two new tests to verify they fail**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_agent_config_api.py -v
```

Expected: FAIL because the router still returns only `user_id` and there is no patch endpoint or schema.

- [ ] **Step 4: Commit the failing test scaffold**

```bash
git add apps/api/tests/agent/test_agent_config_api.py apps/api/tests/conftest.py
git commit -m "test: cover agent config api contract"
```

### Task 2: Implement config schemas and GET endpoint

**Files:**
- Create: `apps/api/app/schemas/agent.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/repositories/agent_config_repository.py`
- Test: `apps/api/tests/agent/test_agent_config_api.py`

- [ ] **Step 1: Add the response and patch schemas**

In `apps/api/app/schemas/agent.py`, add:

```python
class AgentConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_name: str
    owner_context: str | None
    system_prompt: str
    knowledge_base: str
    pipeline_mode: Literal["stt_llm_tts", "sts"]
    is_enabled: bool


class AgentConfigPatchRequest(BaseModel):
    agent_name: str | None = None
    owner_context: str | None = None
    system_prompt: str | None = None
    knowledge_base: str | None = None
    pipeline_mode: Literal["stt_llm_tts", "sts"] | None = None
    is_enabled: bool | None = None
```

- [ ] **Step 2: Implement repository support for partial updates**

Add a focused method in `apps/api/app/repositories/agent_config_repository.py`:

```python
async def update_fields(self, config: AgentConfig, updates: dict[str, object]) -> AgentConfig:
    for field, value in updates.items():
        setattr(config, field, value)
    await self.session.flush()
    return config
```

- [ ] **Step 3: Replace the placeholder GET config route**

Update `apps/api/app/routers/agent.py` so `GET /api/agent/config`:
- loads the authenticated user’s config through `AgentConfigRepository`
- returns `404` if missing
- responds with `AgentConfigResponse`

- [ ] **Step 4: Run the GET test and fix until green**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_agent_config_api.py::test_get_agent_config_returns_full_config -v
```

Expected: PASS

- [ ] **Step 5: Commit the GET contract**

```bash
git add apps/api/app/schemas/agent.py apps/api/app/routers/agent.py apps/api/app/repositories/agent_config_repository.py apps/api/tests/agent/test_agent_config_api.py
git commit -m "feat: add agent config read api"
```

## Chunk 2: Toggle Orchestration And Rollback

### Task 3: Add failing tests for toggle side effects and rollback

**Files:**
- Modify: `apps/api/tests/agent/test_agent_config_api.py`

- [ ] **Step 1: Write the enable toggle test**

Add a test that seeds a user with an assigned phone number and monkeypatches the telephony layer:

```python
@pytest.mark.anyio
async def test_patch_agent_config_enables_number_when_is_enabled_changes(
    async_client, client_database_url, rs256_clerk_token_for, monkeypatch
):
    expected_user_id = await seed_agent_config_with_number(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        is_enabled=False,
    )
    fake_service = FakeTelephonyService()
    monkeypatch.setattr("app.routers.agent.AgentConfigService", lambda session: build_service(session, fake_service))

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 200
    assert response.json()["is_enabled"] is True
    assert fake_service.enabled_user_ids == [expected_user_id]
```

- [ ] **Step 2: Write the missing-phone-number failure test**

```python
@pytest.mark.anyio
async def test_patch_agent_config_toggle_without_phone_number_returns_409(
    async_client, client_database_url, rs256_clerk_token_for
):
    await seed_agent_config(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        is_enabled=False,
    )

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )

    assert response.status_code == 409
```

- [ ] **Step 3: Write the telephony rollback test**

```python
@pytest.mark.anyio
async def test_patch_agent_config_rolls_back_toggle_when_telephony_fails(
    async_client, client_database_url, rs256_clerk_token_for, monkeypatch
):
    config_id = await seed_agent_config_with_number(
        client_database_url,
        clerk_user_id="user_agent_cfg",
        email="agent@example.com",
        is_enabled=False,
    )
    monkeypatch.setattr("app.routers.agent.AgentConfigService", lambda session: build_service(session, FailingTelephonyService()))

    response = await async_client.patch(
        "/api/agent/config",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_agent_cfg')}"},
        json={"is_enabled": True},
    )
    refreshed_config = await fetch_agent_config(client_database_url, config_id)

    assert response.status_code in {502, 503}
    assert refreshed_config.is_enabled is False
```

- [ ] **Step 4: Run the new toggle tests to verify failure**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_agent_config_api.py -v
```

Expected: FAIL because PATCH orchestration and rollback handling do not exist yet.

- [ ] **Step 5: Commit the failing toggle tests**

```bash
git add apps/api/tests/agent/test_agent_config_api.py
git commit -m "test: cover agent config toggle behavior"
```

### Task 4: Implement the orchestration service and PATCH endpoint

**Files:**
- Create: `apps/api/app/services/agent_config_service.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/repositories/agent_config_repository.py`
- Modify: `apps/api/app/repositories/phone_number_repository.py` only if a lookup helper is missing
- Test: `apps/api/tests/agent/test_agent_config_api.py`

- [ ] **Step 1: Create the service for patch orchestration**

In `apps/api/app/services/agent_config_service.py`, implement a focused service method:

```python
class AgentConfigService:
    def __init__(self, session: AsyncSession, telephony_service: TelephonyService | None = None) -> None:
        self.session = session
        self.repository = AgentConfigRepository(session)
        self.phone_number_repository = PhoneNumberRepository(session)
        self.telephony_service = telephony_service or TelephonyService(session)

    async def update_for_user(self, user_id: UUID, patch: AgentConfigPatchRequest) -> AgentConfig:
        config = await self.repository.get_by_user_id(user_id)
        if config is None:
            raise LookupError("Agent config not found")
        updates = patch.model_dump(exclude_unset=True)
        requested_toggle = updates.get("is_enabled")
        if requested_toggle is not None and requested_toggle != config.is_enabled:
            phone_number = await self.phone_number_repository.get_by_user_id(user_id)
            if phone_number is None:
                raise MissingPhoneNumberError()
            if requested_toggle:
                await self.telephony_service.enable_number(user_id)
            else:
                await self.telephony_service.disable_number(user_id)
        return await self.repository.update_fields(config, updates)
```

Behavior:
- load config or raise `LookupError`
- build a dict of provided fields only
- detect whether `is_enabled` changed
- if toggle requested, ensure phone number exists before switching
- call `enable_number()` or `disable_number()`
- persist config changes only if telephony succeeds
- roll back the session on telephony/provider failure

- [ ] **Step 2: Add the PATCH route**

Update `apps/api/app/routers/agent.py`:
- inject session
- create `AgentConfigService`
- map `LookupError` to `404`
- map missing-phone toggle to `409`
- map provider failure to `502` or `503`
- return the updated `AgentConfigResponse`

- [ ] **Step 3: Re-run the full agent config API test module**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_agent_config_api.py -v
```

Expected: PASS

- [ ] **Step 4: Commit the PATCH implementation**

```bash
git add apps/api/app/services/agent_config_service.py apps/api/app/routers/agent.py apps/api/app/repositories/agent_config_repository.py apps/api/app/repositories/phone_number_repository.py apps/api/tests/agent/test_agent_config_api.py
git commit -m "feat: add agent config patch api"
```

### Task 5: Final verification and backend context update

**Files:**
- Modify: `docs/architecture/backend-context.md`
- Verify only: `apps/api`

- [ ] **Step 1: Record the new backend capability**

Update `docs/architecture/backend-context.md` to note that the backend now exposes editable agent config and synchronous enable/disable switching via the config API.

- [ ] **Step 2: Run the focused API test suite**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_agent_config_api.py tests/test_health.py -v
```

Expected: PASS

- [ ] **Step 3: Run the full API suite**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: all API tests PASS.

- [ ] **Step 4: Review the final diff**

Run:

```bash
git status --short
git diff --stat HEAD~4..HEAD
```

Expected:
- only the intended API/router/schema/service/test/doc files are changed
- local `.env` files remain untracked

- [ ] **Step 5: Final commit if verification cleanup was needed**

```bash
git add apps/api docs/architecture/backend-context.md
git commit -m "chore: finalize agent config api"
```
