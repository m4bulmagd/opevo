# Call History API Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the MVP user-facing call history backend API with call listing, call detail with transcript, soft delete, and fresh recording URL minting.

**Architecture:** Keep the router thin and move history behavior into a dedicated `CallHistoryService`. Add soft-delete support to `calls`, extend repositories for visible-call filtering and ordered transcript retrieval, and have the detail endpoint mint a fresh recording access URL at read time instead of trusting the stored `recording_url` field as the public contract.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async ORM, Alembic, pytest, httpx

---

## File Structure

- Modify: `apps/api/app/models/call.py`
  - Add nullable `deleted_at` for soft delete.
- Create: `apps/api/alembic/versions/<revision>_add_call_soft_delete.py`
  - Add the `calls.deleted_at` migration.
- Modify: `apps/api/app/repositories/call_repository.py`
  - Add visible list/get helpers and soft-delete mutation.
- Modify: `apps/api/app/repositories/message_repository.py`
  - Add ordered transcript retrieval by `call_id`.
- Modify: `apps/api/app/services/recording_service.py`
  - Add read-time recording URL minting for stored recordings.
- Create: `apps/api/app/services/call_history_service.py`
  - Coordinate visible-call reads, transcript assembly, recording URL minting, and soft delete.
- Modify: `apps/api/app/schemas/calls.py`
  - Add list item, list response, transcript line, and detail response models.
- Modify: `apps/api/app/routers/calls.py`
  - Expose `GET /api/calls`, expand `GET /api/calls/{call_id}`, and add `DELETE /api/calls/{call_id}`.
- Create: `apps/api/tests/calls/test_call_history_api.py`
  - Cover list, detail, recording URL minting, and soft delete behavior.
- Modify: `docs/architecture/backend-context.md`
  - Record the new user-facing history API surface once implemented.
- Modify: `docs/architecture/staging-smoke-runbook.md`
  - Add a short manual verification step for list/detail/delete behavior after a real call exists.

## Chunk 1: Soft Delete Foundation

### Task 1: Add the failing migration/model/repository test for soft delete visibility

**Files:**
- Modify: `apps/api/tests/calls/test_call_lifecycle.py`
- Modify: `apps/api/app/models/call.py` only after red is verified
- Modify: `apps/api/app/repositories/call_repository.py` only after red is verified

- [ ] **Step 1: Write the failing repository-level test**

Add a focused test that creates two calls for the same user, marks one deleted through the repository helper you intend to add, and asserts that a visible-call query returns only the non-deleted one.

```python
@pytest.mark.anyio
async def test_call_repository_excludes_soft_deleted_calls(db_session, active_user):
    repository = CallRepository(db_session)
    visible_call = await repository.create_pending(user_id=active_user.id, caller_number="+33111111111")
    deleted_call = await repository.create_pending(user_id=active_user.id, caller_number="+33222222222")
    await db_session.commit()

    await repository.soft_delete(deleted_call)
    await db_session.commit()

    visible_calls = await repository.list_visible_by_user_id(active_user.id)

    assert [call.id for call in visible_calls] == [visible_call.id]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/calls/test_call_lifecycle.py::test_call_repository_excludes_soft_deleted_calls -v
```

Expected: FAIL because `deleted_at`, `soft_delete`, and visible-call helpers do not exist yet.

- [ ] **Step 3: Add the minimal model field**

Update `apps/api/app/models/call.py` to add:

```python
deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Add repository helpers**

In `apps/api/app/repositories/call_repository.py`, add:

```python
async def list_visible_by_user_id(self, user_id: UUID) -> list[Call]:
    result = await self.session.execute(
        select(Call)
        .where(Call.user_id == user_id, Call.deleted_at.is_(None))
        .order_by(Call.started_at.desc().nullslast(), Call.created_at.desc())
    )
    return list(result.scalars())

async def get_visible_by_id(self, call_id: UUID, *, user_id: UUID) -> Call | None:
    result = await self.session.execute(
        select(Call).where(
            Call.id == call_id,
            Call.user_id == user_id,
            Call.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()

async def soft_delete(self, call: Call) -> Call:
    call.deleted_at = datetime.now(timezone.utc)
    await self.session.flush()
    return call
```

- [ ] **Step 5: Run the repository test to verify it passes**

Run the same command from Step 2.

Expected: PASS

- [ ] **Step 6: Add the Alembic migration**

Create `apps/api/alembic/versions/<revision>_add_call_soft_delete.py` with `upgrade()` adding `deleted_at` and `downgrade()` dropping it.

- [ ] **Step 7: Commit the soft-delete foundation**

```bash
git add apps/api/app/models/call.py apps/api/app/repositories/call_repository.py apps/api/alembic/versions apps/api/tests/calls/test_call_lifecycle.py
git commit -m "feat: add call soft delete foundation"
```

## Chunk 2: List And Detail Contract

### Task 2: Add failing API tests for list and detail

**Files:**
- Create: `apps/api/tests/calls/test_call_history_api.py`
- Modify: `apps/api/app/schemas/calls.py` only after red is verified
- Modify: `apps/api/app/routers/calls.py` only after red is verified
- Create: `apps/api/app/services/call_history_service.py` only after red is verified
- Modify: `apps/api/app/repositories/message_repository.py` only after red is verified

- [ ] **Step 1: Write the failing list test**

Add a test that seeds two visible calls plus one soft-deleted call and asserts that `GET /api/calls` returns only the two visible rows in newest-first order.

```python
@pytest.mark.anyio
async def test_list_calls_returns_visible_calls_newest_first(async_client, client_database_url, rs256_clerk_token_for):
    await seed_call_history(...)

    response = await async_client.get(
        "/api/calls",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["calls"]] == [newest_id, older_id]
```

- [ ] **Step 2: Write the failing detail test**

Add a test that seeds one visible completed call plus transcript rows and asserts that `GET /api/calls/{call_id}` returns transcript ordered by `sequence_number`.

```python
@pytest.mark.anyio
async def test_get_call_detail_returns_transcript(async_client, client_database_url, rs256_clerk_token_for):
    call_id = await seed_call_with_transcript(...)

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert [line["sequence_number"] for line in response.json()["transcript"]] == [1, 2, 3]
```

- [ ] **Step 3: Write the failing deleted-call detail test**

```python
@pytest.mark.anyio
async def test_get_call_detail_returns_404_for_soft_deleted_call(async_client, client_database_url, rs256_clerk_token_for):
    call_id = await seed_deleted_call(...)

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 404
```

- [ ] **Step 4: Run the new call history API tests to verify they fail**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/calls/test_call_history_api.py -v
```

Expected: FAIL because list/detail schemas, service logic, and transcript retrieval are not implemented.

### Task 3: Implement list/detail schemas, service, and router

**Files:**
- Modify: `apps/api/app/schemas/calls.py`
- Create: `apps/api/app/services/call_history_service.py`
- Modify: `apps/api/app/repositories/message_repository.py`
- Modify: `apps/api/app/routers/calls.py`
- Test: `apps/api/tests/calls/test_call_history_api.py`

- [ ] **Step 1: Add the new call history schemas**

In `apps/api/app/schemas/calls.py`, add:

```python
class CallHistoryListItem(BaseModel):
    id: UUID
    status: str
    caller_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    minutes_charged: int | None
    summary_text: str | None
    has_recording: bool


class CallHistoryListResponse(BaseModel):
    calls: list[CallHistoryListItem]


class CallTranscriptLineResponse(BaseModel):
    speaker: str
    text: str
    sequence_number: int
    created_at: datetime


class CallDetailResponse(BaseModel):
    id: UUID
    status: str
    caller_number: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    minutes_charged: int | None
    summary_text: str | None
    recording_url: str | None
    transcript: list[CallTranscriptLineResponse]
```

- [ ] **Step 2: Add transcript retrieval**

In `apps/api/app/repositories/message_repository.py`, add:

```python
async def list_by_call_id(self, call_id: UUID) -> list[CallMessage]:
    result = await self.session.execute(
        select(CallMessage)
        .where(CallMessage.call_id == call_id)
        .order_by(CallMessage.sequence_number.asc(), CallMessage.created_at.asc())
    )
    return list(result.scalars())
```

- [ ] **Step 3: Implement `CallHistoryService`**

Create `apps/api/app/services/call_history_service.py` with methods:

```python
async def list_calls(self, clerk_user_id: str) -> list[CallHistoryListItem]: ...
async def get_call_detail(self, clerk_user_id: str, call_id: UUID) -> CallDetailResponse: ...
```

Use `UserRepository`, `CallRepository`, `MessageRepository`, and `RecordingService`. Raise a focused `CallHistoryNotFoundError` when the user or visible call is missing.

- [ ] **Step 4: Update the router**

In `apps/api/app/routers/calls.py`:

- add `GET /api/calls`
- replace the old detail route to use `CallDetailResponse`
- route everything through `CallHistoryService`
- map missing calls to `404`

- [ ] **Step 5: Run the new call history API tests**

Run the command from Task 2 Step 4.

Expected: PASS for the list/detail coverage.

- [ ] **Step 6: Commit the list/detail contract**

```bash
git add apps/api/app/schemas/calls.py apps/api/app/services/call_history_service.py apps/api/app/repositories/message_repository.py apps/api/app/routers/calls.py apps/api/tests/calls/test_call_history_api.py
git commit -m "feat: add call history read api"
```

## Chunk 3: Recording URL Minting And Soft Delete Endpoint

### Task 4: Add failing tests for recording access and delete

**Files:**
- Modify: `apps/api/tests/calls/test_call_history_api.py`
- Modify: `apps/api/app/services/recording_service.py` only after red is verified
- Modify: `apps/api/app/routers/calls.py` only after red is verified
- Modify: `apps/api/app/services/call_history_service.py` only after red is verified

- [ ] **Step 1: Write the failing recording-URL test**

Add a test that seeds a call with a persisted recording URL/object key and overrides the recording provider so the detail endpoint must mint a fresh access URL.

```python
@pytest.mark.anyio
async def test_get_call_detail_mints_fresh_recording_url(async_client, client_database_url, rs256_clerk_token_for):
    call_id = await seed_call_with_recording(...)
    override_recording_provider(...)

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert response.json()["recording_url"] == "https://signed.example.com/fresh"
```

- [ ] **Step 2: Write the failing no-recording test**

```python
@pytest.mark.anyio
async def test_get_call_detail_returns_null_recording_url_without_recording(async_client, client_database_url, rs256_clerk_token_for):
    call_id = await seed_call_without_recording(...)

    response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 200
    assert response.json()["recording_url"] is None
```

- [ ] **Step 3: Write the failing delete test**

```python
@pytest.mark.anyio
async def test_delete_call_soft_deletes_and_hides_it(async_client, client_database_url, rs256_clerk_token_for):
    call_id = await seed_call_with_transcript(...)

    delete_response = await async_client.delete(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )
    detail_response = await async_client.get(
        f"/api/calls/{call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert delete_response.status_code == 204
    assert detail_response.status_code == 404
```

- [ ] **Step 4: Write the cross-user delete `404` test**

```python
@pytest.mark.anyio
async def test_delete_call_returns_404_for_other_users_call(async_client, client_database_url, rs256_clerk_token_for):
    foreign_call_id = await seed_call_for_other_user(...)

    response = await async_client.delete(
        f"/api/calls/{foreign_call_id}",
        headers={"authorization": f"Bearer {rs256_clerk_token_for('user_calls')}"},
    )

    assert response.status_code == 404
```

- [ ] **Step 5: Run the call history API test file to verify the new cases fail**

Run the same command from Chunk 2.

Expected: FAIL because recording URL minting and delete behavior are not implemented.

### Task 5: Implement recording URL minting and soft delete endpoint

**Files:**
- Modify: `apps/api/app/services/recording_service.py`
- Modify: `apps/api/app/services/call_history_service.py`
- Modify: `apps/api/app/routers/calls.py`
- Test: `apps/api/tests/calls/test_call_history_api.py`

- [ ] **Step 1: Add recording access URL minting**

Extend `RecordingService` with a method like:

```python
async def get_access_url(self, *, call_id: UUID, user_id: UUID, stored_url: str | None) -> str | None:
    if not stored_url:
        return None
    object_key = f"calls/{user_id}/{call_id}.mp3"
    return await self.provider.get_download_url(object_key=object_key)
```

If the storage provider interface needs extension for signed download URLs, make the smallest provider changes necessary and cover them with tests.

- [ ] **Step 2: Extend `CallHistoryService` detail response**

When building detail:

- call `RecordingService.get_access_url(...)`
- include the minted URL in `CallDetailResponse`

- [ ] **Step 3: Add `delete_call` to `CallHistoryService`**

Implement:

```python
async def delete_call(self, clerk_user_id: str, call_id: UUID) -> None:
    ...
```

It should load the visible call, call `CallRepository.soft_delete(...)`, and commit.

- [ ] **Step 4: Add `DELETE /api/calls/{call_id}`**

Update the router to:

- return `204 No Content`
- return `404` for missing/foreign/already-deleted calls

- [ ] **Step 5: Run the call history API test file**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/calls/test_call_history_api.py -v
```

Expected: PASS

- [ ] **Step 6: Commit the delete and recording access behavior**

```bash
git add apps/api/app/services/recording_service.py apps/api/app/services/call_history_service.py apps/api/app/routers/calls.py apps/api/tests/calls/test_call_history_api.py
git commit -m "feat: add call history delete flow"
```

## Chunk 4: Docs And Verification

### Task 6: Update docs and run verification

**Files:**
- Modify: `docs/architecture/backend-context.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`
- Test: `apps/api/tests/calls/test_call_history_api.py`
- Test: `apps/api/tests/calls/test_call_lifecycle.py`
- Test: full API suite

- [ ] **Step 1: Update backend context**

Record that the backend now exposes:

- `GET /api/calls`
- `GET /api/calls/{call_id}`
- `DELETE /api/calls/{call_id}`

Also note that user-facing delete is a soft delete.

- [ ] **Step 2: Update staging smoke runbook**

Add a short manual check after a real completed call exists:

- confirm the call appears in `GET /api/calls`
- confirm detail includes transcript
- confirm delete returns `204`
- confirm the deleted call disappears from list/detail

- [ ] **Step 3: Run focused call history tests**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/calls/test_call_history_api.py tests/calls/test_call_lifecycle.py -v
```

Expected: PASS

- [ ] **Step 4: Run the full API suite**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: PASS

- [ ] **Step 5: Commit the docs and verification updates**

```bash
git add docs/architecture/backend-context.md docs/architecture/staging-smoke-runbook.md
git commit -m "docs: add call history api notes"
```
