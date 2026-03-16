# Queue-Backed Call Finalization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move call finalization off the LiveKit agent shutdown path so the API enqueues durable completion work and returns immediately.

**Architecture:** Keep the existing `POST /api/agent/calls/{call_id}/complete` endpoint, but change it to enqueue an ARQ job keyed by `call_id` and return `202` without doing the full database write inline. Add a dedicated call-finalization worker job that owns transcript persistence, summary generation, recording persistence, usage charging, notification creation, and idempotent duplicate suppression.

**Tech Stack:** FastAPI, SQLAlchemy async, Redis, ARQ, pytest, httpx

---

## Chunk 1: Queue The Completion Request

### Task 1: Define the queue contract and enqueue dependency

**Files:**
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/schemas/calls.py`
- Create: `apps/api/app/workers/jobs/call_finalization.py`
- Test: `apps/api/tests/agent/test_call_completion.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_agent_completion_endpoint_enqueues_finalization_job():
    ...
    assert fake_queue.calls == [
        (
            "call_finalization_job",
            {"call_id": str(call_id), ...},
            {"_job_id": f"call-finalization:{call_id}"},
        )
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_call_completion.py -v`
Expected: FAIL because the endpoint still calls the lifecycle service directly.

- [ ] **Step 3: Write minimal implementation**

```python
class CallFinalizationQueue:
    async def enqueue(self, payload: dict) -> None:
        await redis.enqueue_job(
            "call_finalization_job",
            payload,
            _job_id=f"call-finalization:{payload['call_id']}",
        )
```

Wire the router to depend on that queue and return a response like:

```python
{"status": "accepted", "queued": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/agent/test_call_completion.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/routers/agent.py apps/api/app/schemas/calls.py apps/api/app/workers/jobs/call_finalization.py apps/api/tests/agent/test_call_completion.py
git commit -m "feat: enqueue agent call completion"
```

## Chunk 2: Finalize In The Worker

### Task 2: Make worker finalization idempotent

**Files:**
- Modify: `apps/api/app/services/call_lifecycle_service.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Create: `apps/api/app/workers/jobs/call_finalization.py`
- Test: `apps/api/tests/workers/test_post_call_jobs.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_call_finalization_job_skips_duplicate_completed_call(...):
    ...
    assert result["status"] == "skipped"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/workers/test_post_call_jobs.py -v`
Expected: FAIL because no call-finalization job exists and duplicate calls are not short-circuited.

- [ ] **Step 3: Write minimal implementation**

```python
if call.status == "completed":
    return CallFinalizationResult(..., already_completed=True)
```

Expose that through the new worker job and register the job in `WorkerSettings`.

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/workers/test_post_call_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/call_lifecycle_service.py apps/api/app/workers/arq_worker.py apps/api/app/workers/jobs/call_finalization.py apps/api/tests/workers/test_post_call_jobs.py
git commit -m "feat: finalize calls in worker jobs"
```

## Chunk 3: Align The Agent And Verification

### Task 3: Keep agent shutdown fast and update docs/tests

**Files:**
- Modify: `apps/agent/agent/api_client.py`
- Modify: `apps/agent/tests/test_session_runtime.py`
- Modify: `apps/api/app/services/notification_service.py`
- Modify: `apps/api/tests/workers/test_post_call_jobs.py`
- Modify: `docs/architecture/backend-context.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`

- [ ] **Step 1: Write the failing test**

```python
async def test_session_runtime_accepts_queued_completion_response():
    assert api_client.calls == [...]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_session_runtime.py -v`
Expected: FAIL until the response shape and client expectations match the queued endpoint.

- [ ] **Step 3: Write minimal implementation**

Update the agent client to accept the queue response, keep the existing notification failure fix, and document that finalization now happens asynchronously.

- [ ] **Step 4: Run test to verify it passes**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_session_runtime.py tests/workers/test_post_call_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Final verification**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
cd ../agent && UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add apps/agent/agent/api_client.py apps/agent/tests/test_session_runtime.py apps/api/app/services/notification_service.py apps/api/tests/workers/test_post_call_jobs.py docs/architecture/backend-context.md docs/architecture/staging-smoke-runbook.md
git commit -m "fix: queue call finalization off agent shutdown"
```
