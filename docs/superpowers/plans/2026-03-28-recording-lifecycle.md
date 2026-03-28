# Recording Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unused live-call recording blob path with LiveKit-managed mixed-call recording metadata, keep storage retention in the bucket lifecycle, and return `recording_url = null` when recordings expire.

**Architecture:** Start a LiveKit Room Composite Egress for each call room and persist recording metadata on the `calls` row. Keep queue-backed call finalization focused on transcript/summary/usage work, and have call history mint fresh signed URLs from stored object keys while degrading missing objects to `null`.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, ARQ, LiveKit server SDK, S3-compatible storage, pytest, uv

---

## File Map

- Modify: `apps/api/app/models/call.py`
  - Add persisted recording metadata fields.
- Create: `apps/api/alembic/versions/0004_add_call_recording_metadata.py`
  - Add DB columns for object key and egress id.
- Modify: `apps/api/app/repositories/call_repository.py`
  - Persist and update recording metadata cleanly.
- Create: `apps/api/app/providers/livekit_recording/base.py`
  - Define recording provider interface and result shape.
- Create: `apps/api/app/providers/livekit_recording/livekit.py`
  - Wrap LiveKit room composite egress start behavior.
- Create: `apps/api/app/services/livekit_recording_service.py`
  - Coordinate provider calls and non-blocking failure behavior.
- Modify: `apps/api/app/core/config.py`
  - Add recording-related LiveKit/S3 config.
- Modify: `apps/api/app/services/recording_service.py`
  - Mint signed URLs from object key and return `None` on missing-object failures.
- Modify: `apps/api/app/services/call_history_service.py`
  - Use persisted `recording_object_key` for access URL minting.
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
  - Start recording egress when a call/room dispatch is created and persist metadata.
- Modify: `apps/api/app/services/call_lifecycle_service.py`
  - Stop depending on live-call recording bytes for the normal path.
- Modify: `apps/api/app/schemas/calls.py`
  - Keep API contract stable; remove unused internal assumptions only if needed.
- Modify: `apps/api/app/routers/agent.py`
  - Preserve backward compatibility but stop treating recording bytes as primary live path.
- Test: `apps/api/tests/livekit/test_recording_dispatch.py`
  - Cover egress start and metadata persistence.
- Test: `apps/api/tests/calls/test_call_history_api.py`
  - Cover missing/expired recording behavior with object-key-based reads.
- Test: `apps/api/tests/workers/test_post_call_jobs.py`
  - Confirm call finalization still works without recording bytes.
- Test: `apps/api/tests/providers/test_livekit_recording_provider.py`
  - Cover provider request shaping and failure handling.
- Modify: `docs/architecture/call-history-api.md`
  - Document expired recording behavior.
- Modify: `docs/architecture/integration-endpoints.md`
  - Document internal recording/egress responsibilities if needed.
- Modify: `docs/architecture/backend-context.md`
  - Update staging and lifecycle notes.

## Chunk 1: Schema And Recording Read Path

### Task 1: Add recording metadata fields to calls

**Files:**
- Modify: `apps/api/app/models/call.py`
- Create: `apps/api/alembic/versions/0004_add_call_recording_metadata.py`
- Test: `apps/api/tests/calls/test_call_history_api.py`

- [ ] **Step 1: Write the failing test**

Add a test that seeds a call with `recording_object_key` but no usable stored URL, then requests call detail and expects the service to rely on the object key for fresh access.

```python
async def test_get_call_detail_mints_fresh_recording_url_from_object_key(...):
    ...
    assert response.json()["recording_url"] == "https://signed.example.com/fresh"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/calls/test_call_history_api.py::test_get_call_detail_mints_fresh_recording_url_from_object_key -v
```

Expected: FAIL because `Call` has no `recording_object_key` field yet.

- [ ] **Step 3: Add model field and migration**

Add nullable fields to `Call`:

```python
recording_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
recording_egress_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Create Alembic migration adding both columns.

- [ ] **Step 4: Run the targeted test again**

Run the same pytest command.

Expected: FAIL later in the read path because the service does not yet use `recording_object_key`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/models/call.py apps/api/alembic/versions/0004_add_call_recording_metadata.py apps/api/tests/calls/test_call_history_api.py
git commit -m "feat: add call recording metadata fields"
```

### Task 2: Make call detail resilient to expired recordings

**Files:**
- Modify: `apps/api/app/services/recording_service.py`
- Modify: `apps/api/app/services/call_history_service.py`
- Test: `apps/api/tests/calls/test_call_history_api.py`

- [ ] **Step 1: Write the failing tests**

Add two tests:

```python
async def test_get_call_detail_returns_null_recording_url_when_object_missing(...):
    ...
    assert response.json()["recording_url"] is None

async def test_get_call_detail_uses_object_key_for_signed_url(...):
    ...
    assert response.json()["recording_url"] == "https://signed.example.com/fresh"
```

Use a fake recording service/provider that raises an object-not-found style error for the missing case.

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/calls/test_call_history_api.py::test_get_call_detail_returns_null_recording_url_when_object_missing tests/calls/test_call_history_api.py::test_get_call_detail_uses_object_key_for_signed_url -v
```

Expected: FAIL because the service still depends on `stored_url`.

- [ ] **Step 3: Implement minimal read-path changes**

Update `RecordingService` to accept `recording_object_key` and return `None` for missing-object signing failures.

Minimal intended behavior:

```python
if not recording_object_key:
    return None
try:
    return await self.provider.get_download_url(object_key=recording_object_key)
except MissingObjectError:
    return None
```

Update `CallHistoryService` to pass `call.recording_object_key`.

- [ ] **Step 4: Run tests to verify they pass**

Run the same two pytest tests.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/recording_service.py apps/api/app/services/call_history_service.py apps/api/tests/calls/test_call_history_api.py
git commit -m "feat: handle expired recordings in call history"
```

## Chunk 2: LiveKit Recording Provider And Dispatch Wiring

### Task 3: Add the LiveKit recording provider boundary

**Files:**
- Create: `apps/api/app/providers/livekit_recording/base.py`
- Create: `apps/api/app/providers/livekit_recording/livekit.py`
- Modify: `apps/api/app/core/config.py`
- Test: `apps/api/tests/providers/test_livekit_recording_provider.py`

- [ ] **Step 1: Write the failing provider tests**

Add tests for:
- building a room composite egress request with `audio_only=True`
- returning egress id, object key, and stored URL metadata
- surfacing provider failures as explicit exceptions

Example:

```python
async def test_start_room_recording_uses_audio_only_room_composite():
    ...
    assert request.audio_only is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/providers/test_livekit_recording_provider.py -v
```

Expected: FAIL because provider files do not exist.

- [ ] **Step 3: Implement the minimal provider layer**

Create a result shape like:

```python
@dataclass(frozen=True)
class RecordingEgressResult:
    egress_id: str
    object_key: str
    url: str | None
```

Wrap LiveKit room composite egress start with `audio_only=True` and direct file output.

- [ ] **Step 4: Run tests to verify they pass**

Run the same pytest command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/providers/livekit_recording/base.py apps/api/app/providers/livekit_recording/livekit.py apps/api/app/core/config.py apps/api/tests/providers/test_livekit_recording_provider.py
git commit -m "feat: add livekit recording provider"
```

### Task 4: Start recording egress during dispatch and persist metadata

**Files:**
- Create: `apps/api/app/services/livekit_recording_service.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Test: `apps/api/tests/livekit/test_recording_dispatch.py`

- [ ] **Step 1: Write the failing dispatch tests**

Add tests for:
- successful dispatch starts recording egress and persists `recording_object_key` + `recording_egress_id`
- recording start failure does not block dispatch

Example:

```python
async def test_dispatch_persists_recording_metadata_when_egress_starts(...):
    ...
    assert refreshed_call.recording_object_key == "calls/user/call.mp3"

async def test_dispatch_continues_when_recording_egress_fails(...):
    ...
    assert response.status_code == 202
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/livekit/test_recording_dispatch.py -v
```

Expected: FAIL because there is no recording service wired into dispatch.

- [ ] **Step 3: Implement the minimal dispatch integration**

Add a `LiveKitRecordingService` that attempts egress start and returns either metadata or `None` on failure.

Persist metadata through repository helpers, for example:

```python
await self.call_repository.set_recording_metadata(
    call,
    recording_object_key=result.object_key,
    recording_egress_id=result.egress_id,
    recording_url=result.url,
)
```

Do not fail the dispatch if recording startup fails; log and continue.

- [ ] **Step 4: Run tests to verify they pass**

Run the same pytest command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/livekit_recording_service.py apps/api/app/services/livekit_dispatch_service.py apps/api/app/repositories/call_repository.py apps/api/tests/livekit/test_recording_dispatch.py
git commit -m "feat: start recording egress on call dispatch"
```

## Chunk 3: Finalization Cleanup And Docs

### Task 5: Remove the live-call recording-bytes dependency from the main path

**Files:**
- Modify: `apps/api/app/services/call_lifecycle_service.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/schemas/calls.py`
- Test: `apps/api/tests/workers/test_post_call_jobs.py`
- Test: `apps/api/tests/agent/test_call_completion.py`

- [ ] **Step 1: Write the failing tests**

Add tests asserting:
- call finalization still succeeds with no recording bytes
- completion endpoint remains backward-compatible if `recording_bytes_base64` is provided

Example:

```python
async def test_call_completion_succeeds_without_recording_bytes(...):
    ...
    assert result.status == "completed"
```

- [ ] **Step 2: Run tests to verify they fail or prove current assumptions**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/workers/test_post_call_jobs.py tests/agent/test_call_completion.py -v
```

Expected: either targeted FAIL due to old recording assumptions or a clear baseline proving the path already works.

- [ ] **Step 3: Implement minimal cleanup**

Keep backward compatibility for `recording_bytes_base64`, but make it clearly non-primary for live calls. Do not require recording bytes anywhere in the main flow.

- [ ] **Step 4: Run tests to verify they pass**

Run the same pytest command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/call_lifecycle_service.py apps/api/app/routers/agent.py apps/api/app/schemas/calls.py apps/api/tests/workers/test_post_call_jobs.py apps/api/tests/agent/test_call_completion.py
git commit -m "refactor: decouple live recordings from call completion"
```

### Task 6: Update docs and run full API verification

**Files:**
- Modify: `docs/architecture/call-history-api.md`
- Modify: `docs/architecture/backend-context.md`
- Modify: `docs/architecture/integration-endpoints.md`

- [ ] **Step 1: Update the docs**

Document:
- mixed room recording via LiveKit egress
- bucket-managed retention
- expired recordings return `recording_url = null`

- [ ] **Step 2: Run focused tests**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/calls/test_call_history_api.py tests/livekit/test_recording_dispatch.py tests/providers/test_livekit_recording_provider.py tests/workers/test_post_call_jobs.py tests/agent/test_call_completion.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full API suite**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/architecture/call-history-api.md docs/architecture/backend-context.md docs/architecture/integration-endpoints.md
git commit -m "docs: record recording lifecycle behavior"
```

