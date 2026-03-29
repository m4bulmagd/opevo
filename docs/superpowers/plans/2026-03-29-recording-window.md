# Recording Window Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move mixed call recording start to agent join and stop it on SIP caller leave so recordings better match the actual conversation window.

**Architecture:** Keep SIP caller join responsible for number matching, pending-call creation, and agent dispatch only. Add room-scoped recording start/stop handling around LiveKit participant webhooks, using `livekit_room_id` and persisted `recording_egress_id` to keep the flow concurrency-safe and idempotent.

**Tech Stack:** FastAPI, SQLAlchemy, LiveKit server SDK, pytest, uv

---

## File Map

- Modify: `apps/api/app/services/livekit_dispatch_service.py`
  - Split SIP join, agent join, and SIP leave responsibilities.
- Modify: `apps/api/app/webhooks/livekit.py`
  - Route both `participant_joined` and `participant_left` events.
- Modify: `apps/api/app/services/livekit_recording_service.py`
  - Add explicit stop support.
- Modify: `apps/api/app/providers/livekit_recording/base.py`
  - Add stop method to the provider boundary.
- Modify: `apps/api/app/providers/livekit_recording/livekit.py`
  - Wrap LiveKit egress stop behavior.
- Modify: `apps/api/app/repositories/call_repository.py`
  - Add room-scoped lookup helpers for pending/no-egress and active/with-egress.
- Test: `apps/api/tests/livekit/test_dispatch_service.py`
  - Cover timing and idempotency behavior.
- Test: `apps/api/tests/livekit/test_dispatch_webhook.py`
  - Cover webhook routing for join and leave events.
- Test: `apps/api/tests/providers/test_livekit_recording_provider.py`
  - Cover explicit egress stop behavior.

## Chunk 1: Service Timing Behavior

### Task 1: Lock in join/leave timing with failing dispatch-service tests

**Files:**
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`
- Test: `apps/api/tests/livekit/test_dispatch_service.py`

- [ ] **Step 1: Write the failing tests**

Add tests for:
- SIP caller join creates/displays the pending call without starting recording
- agent participant join starts recording for the room's pending call
- repeated agent joins do nothing when recording already exists
- SIP caller leave stops recording for the room's active call
- recording stop failures remain non-blocking

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/livekit/test_dispatch_service.py -v
```

Expected: FAIL because recording currently starts during SIP join and there is no stop path.

- [ ] **Step 3: Implement minimal service/repository changes**

Add room-scoped lookup helpers, split service handlers by participant role and event type, and only start recording on agent join when the room's pending call has no `recording_egress_id`.

- [ ] **Step 4: Run the dispatch-service tests again**

Run the same pytest command.

Expected: PASS.

## Chunk 2: Provider And Webhook Wiring

### Task 2: Add explicit stop support to the recording boundary

**Files:**
- Modify: `apps/api/app/providers/livekit_recording/base.py`
- Modify: `apps/api/app/providers/livekit_recording/livekit.py`
- Modify: `apps/api/app/services/livekit_recording_service.py`
- Modify: `apps/api/tests/providers/test_livekit_recording_provider.py`

- [ ] **Step 1: Write the failing provider test**

Add a provider/service test showing `stop_room_recording(egress_id)` calls the LiveKit egress stop API and wraps provider failures appropriately.

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/providers/test_livekit_recording_provider.py -v
```

Expected: FAIL because the stop API does not exist yet.

- [ ] **Step 3: Implement the minimal stop path**

Add `stop_room_recording(...)` to the provider interface and recording service, and wire it to LiveKit egress stop behavior.

- [ ] **Step 4: Run the provider test again**

Run the same pytest command.

Expected: PASS.

### Task 3: Route webhook events to the new timing handlers

**Files:**
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/tests/livekit/test_dispatch_webhook.py`

- [ ] **Step 1: Write the failing webhook tests**

Add tests covering:
- `participant_joined` for SIP caller routes to call creation/dispatch only
- `participant_joined` for agent routes to recording start
- `participant_left` for SIP caller routes to recording stop

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/livekit/test_dispatch_webhook.py -v
```

Expected: FAIL because only `participant_joined` is handled today and role-specific timing is absent.

- [ ] **Step 3: Implement minimal webhook routing**

Route join/leave events into the service and rely on participant identity/kind/attributes to distinguish SIP caller vs agent behavior.

- [ ] **Step 4: Run the webhook tests again**

Run the same pytest command.

Expected: PASS.

## Chunk 3: Verification

### Task 4: Run focused verification

**Files:**
- Test: `apps/api/tests/livekit/test_dispatch_service.py`
- Test: `apps/api/tests/livekit/test_dispatch_webhook.py`
- Test: `apps/api/tests/providers/test_livekit_recording_provider.py`

- [ ] **Step 1: Run the focused suite**

Run:
```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/livekit/test_dispatch_service.py tests/livekit/test_dispatch_webhook.py tests/providers/test_livekit_recording_provider.py -v
```

Expected: PASS.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-03-29-recording-window.md apps/api/app/services/livekit_dispatch_service.py apps/api/app/webhooks/livekit.py apps/api/app/services/livekit_recording_service.py apps/api/app/providers/livekit_recording/base.py apps/api/app/providers/livekit_recording/livekit.py apps/api/app/repositories/call_repository.py apps/api/tests/livekit/test_dispatch_service.py apps/api/tests/livekit/test_dispatch_webhook.py apps/api/tests/providers/test_livekit_recording_provider.py
git commit -m "feat: tighten livekit recording window timing"
```
