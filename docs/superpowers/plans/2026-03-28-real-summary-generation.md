# Real Summary Generation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder post-call summary logic with provider-agnostic real summary generation, using Gemini as the default and persisting both `summary_text` and structured `summary_data` on calls.

**Architecture:** Keep the current call-finalization flow intact and make summary generation a narrow service boundary. Add a `SummaryProvider` interface with a Gemini default, validate structured JSON output in `SummaryService`, persist both fields on `calls`, and keep failures non-blocking so call completion still succeeds when summary generation fails.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async ORM, Alembic, pytest, Google Gemini API

---

## File Structure

- Modify: `apps/api/app/models/call.py`
  - Add nullable `summary_data` JSON column.
- Create: `apps/api/alembic/versions/<revision>_add_call_summary_data.py`
  - Add the `calls.summary_data` migration.
- Modify: `apps/api/app/core/config.py`
  - Add provider-agnostic summary config fields.
- Create: `apps/api/app/providers/summaries/base.py`
  - Define `StructuredSummary` and `SummaryProvider`.
- Create: `apps/api/app/providers/summaries/gemini.py`
  - Default Gemini-backed implementation.
- Modify: `apps/api/app/services/summary_service.py`
  - Replace placeholder logic with provider orchestration, validation, and rendered summary text.
- Modify: `apps/api/app/repositories/call_repository.py`
  - Persist `summary_data` alongside `summary_text`.
- Modify: `apps/api/app/services/call_lifecycle_service.py`
  - Store real summary output and keep provider failures non-blocking.
- Create: `apps/api/tests/services/test_summary_service.py`
  - Cover success, malformed output, and provider failure behavior.
- Modify: `apps/api/tests/workers/test_post_call_jobs.py`
  - Cover lifecycle persistence of `summary_data` and non-blocking failure behavior.
- Modify: `docs/architecture/backend-context.md`
  - Record that real summary generation now exists.
- Modify: `docs/architecture/staging-smoke-runbook.md`
  - Add manual verification notes for summary generation.

## Chunk 1: Data And Config Foundation

### Task 1: Add the failing lifecycle test for structured summary persistence

**Files:**
- Modify: `apps/api/tests/workers/test_post_call_jobs.py`
- Modify: `apps/api/app/models/call.py` only after red is verified
- Modify: `apps/api/app/repositories/call_repository.py` only after red is verified

- [ ] **Step 1: Write the failing test**

Add a test that finalizes a call with a fake summary service returning structured output and asserts that the refreshed call row stores both `summary_text` and `summary_data`.

```python
@pytest.mark.anyio
async def test_call_completion_persists_structured_summary_data(db_session, active_user):
    ...
    assert refreshed_call.summary_text == "Caller asked about opening hours."
    assert refreshed_call.summary_data["caller_intent"] == "Ask about opening hours"
    assert refreshed_call.summary_data["follow_up_required"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/workers/test_post_call_jobs.py::test_call_completion_persists_structured_summary_data -v
```

Expected: FAIL because `summary_data` does not exist yet.

- [ ] **Step 3: Add `summary_data` to the model**

In `apps/api/app/models/call.py`, add:

```python
summary_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

- [ ] **Step 4: Extend repository persistence**

In `apps/api/app/repositories/call_repository.py`, update `mark_completed(...)` to accept and persist:

```python
summary_data: dict | None
```

- [ ] **Step 5: Add the Alembic migration**

Create `apps/api/alembic/versions/<revision>_add_call_summary_data.py` with `upgrade()` adding `summary_data` and `downgrade()` dropping it.

- [ ] **Step 6: Add summary config fields**

In `apps/api/app/core/config.py`, add:

```python
summary_provider: str = "gemini"
summary_model: str = "gemini-2.5-flash"
google_api_key: str | None = None
```

- [ ] **Step 7: Run the lifecycle test again**

Run the same command from Step 2.

Expected: still FAIL, but now because the summary service/repository integration is incomplete rather than missing schema.

- [ ] **Step 8: Commit the data/config foundation**

```bash
git add apps/api/app/models/call.py apps/api/app/repositories/call_repository.py apps/api/app/core/config.py apps/api/alembic/versions apps/api/tests/workers/test_post_call_jobs.py
git commit -m "feat: add summary data foundation"
```

## Chunk 2: Provider And Summary Service

### Task 2: Add failing summary service tests

**Files:**
- Create: `apps/api/tests/services/test_summary_service.py`
- Create: `apps/api/app/providers/summaries/base.py` only after red is verified
- Create: `apps/api/app/providers/summaries/gemini.py` only after red is verified
- Modify: `apps/api/app/services/summary_service.py` only after red is verified

- [ ] **Step 1: Write the success-path unit test**

Add a test with a fake provider returning valid structured data:

```python
@pytest.mark.anyio
async def test_summary_service_returns_structured_summary() -> None:
    service = SummaryService(provider=FakeSummaryProvider(...))

    result = await service.create_summary({"transcript": [...]})

    assert result.text == "Caller asked about opening hours."
    assert result.data["caller_intent"] == "Ask about opening hours"
```

- [ ] **Step 2: Write the malformed-output test**

```python
@pytest.mark.anyio
async def test_summary_service_rejects_malformed_provider_output() -> None:
    service = SummaryService(provider=FakeSummaryProvider({"summary_text": "missing fields"}))

    result = await service.create_summary({"transcript": [...]})

    assert result.text is None
    assert result.data is None
    assert result.job_enqueued is False
```

- [ ] **Step 3: Write the provider-failure test**

```python
@pytest.mark.anyio
async def test_summary_service_handles_provider_failure_non_blocking() -> None:
    service = SummaryService(provider=ExplodingSummaryProvider())

    result = await service.create_summary({"transcript": [...]})

    assert result.text is None
    assert result.data is None
    assert result.job_enqueued is False
```

- [ ] **Step 4: Run the new summary service test file**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_summary_service.py -v
```

Expected: FAIL because the new provider boundary and async service behavior do not exist yet.

### Task 3: Implement provider boundary and service orchestration

**Files:**
- Create: `apps/api/app/providers/summaries/base.py`
- Create: `apps/api/app/providers/summaries/gemini.py`
- Modify: `apps/api/app/services/summary_service.py`
- Test: `apps/api/tests/services/test_summary_service.py`

- [ ] **Step 1: Create the provider base**

In `apps/api/app/providers/summaries/base.py`, define:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredSummary:
    summary_text: str
    caller_intent: str
    action_items: list[str]
    sentiment: str
    follow_up_required: bool


class SummaryProvider:
    async def generate_summary(self, transcript: list[dict]) -> StructuredSummary:
        raise NotImplementedError
```

- [ ] **Step 2: Add the Gemini provider**

Create `apps/api/app/providers/summaries/gemini.py` with a default `GeminiSummaryProvider` that:

- reads `GOOGLE_API_KEY`
- uses `SUMMARY_MODEL`
- sends transcript text to Gemini
- requests strict JSON output matching the structured shape
- returns `StructuredSummary`

Keep the implementation narrowly focused; do not build a multi-provider registry yet.

- [ ] **Step 3: Replace placeholder `SummaryService`**

Refactor `apps/api/app/services/summary_service.py` so it:

- is async
- accepts an optional provider override for tests
- normalizes transcript lines
- short-circuits to empty result for empty caller content
- validates required fields from the provider
- returns:

```python
@dataclass(frozen=True)
class SummaryResult:
    text: str | None
    data: dict | None
    job_enqueued: bool
```

- [ ] **Step 4: Run the summary service tests**

Run the command from Task 2 Step 4.

Expected: PASS

- [ ] **Step 5: Commit the summary provider/service layer**

```bash
git add apps/api/app/providers/summaries apps/api/app/services/summary_service.py apps/api/tests/services/test_summary_service.py
git commit -m "feat: add real summary service"
```

## Chunk 3: Call Finalization Integration

### Task 4: Add failing lifecycle tests for success and failure behavior

**Files:**
- Modify: `apps/api/tests/workers/test_post_call_jobs.py`
- Modify: `apps/api/app/services/call_lifecycle_service.py` only after red is verified

- [ ] **Step 1: Add the non-blocking failure test**

Add a test where the summary service returns no summary and verify call completion still succeeds:

```python
@pytest.mark.anyio
async def test_call_completion_continues_when_summary_generation_fails(...):
    ...
    assert result.summary_text is None
    assert refreshed_call.summary_text is None
    assert refreshed_call.summary_data is None
    assert refreshed_call.status == "completed"
```

- [ ] **Step 2: Run the targeted lifecycle tests**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/workers/test_post_call_jobs.py -v
```

Expected: FAIL because `CallLifecycleService` still expects the old sync summary result.

### Task 5: Integrate real summary generation into call finalization

**Files:**
- Modify: `apps/api/app/services/call_lifecycle_service.py`
- Modify: `apps/api/app/workers/jobs/summary.py` only if needed to stay consistent with the new async service contract
- Test: `apps/api/tests/workers/test_post_call_jobs.py`

- [ ] **Step 1: Make summary generation async in finalization**

Update `CallLifecycleService.finalize_call()` to:

```python
summary_result = await self.summary_service.create_summary(payload)
```

- [ ] **Step 2: Persist both summary fields**

Pass both `summary_text` and `summary_data` into `CallRepository.mark_completed(...)`.

- [ ] **Step 3: Preserve non-blocking behavior**

If summary generation returns empty result or raises provider/validation errors, continue finalization and commit the call as completed.

- [ ] **Step 4: Keep notifications compatible**

Continue passing `summary_result.text` into `NotificationService.create_call_completed_notification(...)` so existing notification payloads still work.

- [ ] **Step 5: Run the lifecycle test file**

Run the command from Task 4 Step 2.

Expected: PASS

- [ ] **Step 6: Commit the call-finalization integration**

```bash
git add apps/api/app/services/call_lifecycle_service.py apps/api/app/workers/jobs/summary.py apps/api/tests/workers/test_post_call_jobs.py
git commit -m "feat: persist real call summaries"
```

## Chunk 4: Docs And Verification

### Task 6: Update docs and run verification

**Files:**
- Modify: `docs/architecture/backend-context.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`
- Test: `apps/api/tests/services/test_summary_service.py`
- Test: `apps/api/tests/workers/test_post_call_jobs.py`
- Test: full API suite

- [ ] **Step 1: Update backend context**

Record that:

- call summaries are now LLM-generated
- `calls.summary_data` stores the structured result
- Gemini is the default summary provider
- summary generation is non-blocking

- [ ] **Step 2: Update the staging runbook**

Replace any wording that still describes the current summary as placeholder behavior. Add a manual check to inspect:

- `calls.summary_text`
- `calls.summary_data`

after a real completed call.

- [ ] **Step 3: Run focused summary tests**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/services/test_summary_service.py tests/workers/test_post_call_jobs.py -v
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
git commit -m "docs: add real summary generation notes"
```
