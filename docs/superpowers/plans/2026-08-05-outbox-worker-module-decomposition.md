# Outbox Worker Module Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the API worker's 1,625-line outbox topic god module with one explicit, cohesive `app.workers.outbox` package while preserving every durable delivery behavior and removing the duplicated LiveKit provider algorithm.

**Architecture:** Phase one relocates behavior without redesign: delivery, bounded failures, the explicit registry, and each cohesive topic family move behind one package boundary, all legacy paths are deleted, and the full API gate must pass. Phase two adds one private typed LiveKit delivery function and migrates customer and verification dispatch separately, retaining their domain validation, locks, reconciliation, and persistence.

**Tech Stack:** Python 3.13, ARQ 0.26+, SQLAlchemy 2 async ORM, PostgreSQL 17, Redis 7, LiveKit API adapter, pytest 9/AnyIO, pytest-cov 7, Ruff 0.15, mypy 2.3, uv.

## Global Constraints

- Follow the approved design in `docs/superpowers/specs/2026-08-05-outbox-worker-module-decomposition-design.md`.
- Use an isolated worktree created through `superpowers:using-git-worktrees` when execution begins.
- Do not inspect or modify `Presvo_frontend/` or `.worktrees/shadcn-activation-preview`.
- Do not inspect real `.env` files; this change does not require environment edits.
- Do not touch `/tmp/presvo-voice-e2e.override.yaml`, `/tmp/presvo-telnyx-e2e.override.yaml`, or `/tmp/presvo-clerk-e2e.override.yaml`.
- Preserve every outbox topic string, aggregate type, payload shape, idempotency key, transaction boundary, lock, retryability value, exhaustibility value, provider call, compensation path, and persistence rule.
- Do not introduce a compatibility facade, deprecated import alias, handler re-export, dynamic registry, DI framework, service locator, strategy hierarchy, or workflow framework.
- `apps/api/app/workers/outbox/__init__.py` remains empty.
- Topic handlers import bounded errors from `outbox/failures.py`; they do not import `delivery.py` or `registry.py`.
- Phase two must not begin until phase one passes the complete API lint, typing, test, and coverage gates.
- Do not change dependencies, `uv.lock`, database schema/data, queue names, worker settings, provider configuration, realtime behavior, recording behavior, or deployment files.
- Do not run live Telnyx, LiveKit, Gemini, storage, voice-agent, or deployment workflows.
- Focused pytest commands omit global coverage flags. Coverage is collected and enforced only on complete API suites.
- Each task commits only its scoped files after its required tests pass.

---

## Locked File Structure

### Create

- `apps/api/app/workers/outbox/__init__.py` — empty package marker.
- `apps/api/app/workers/outbox/failures.py` — safe codes and delivery-failure classification.
- `apps/api/app/workers/outbox/delivery.py` — outbox claim, execution, completion/failure, and lease reconciliation.
- `apps/api/app/workers/outbox/registry.py` — the sole explicit topic-to-handler map.
- `apps/api/app/workers/outbox/_account_lifecycle.py` — shared lifecycle generation validation and account enforcement.
- `apps/api/app/workers/outbox/phone.py` — phone provisioning/routing topic adapters and compensation.
- `apps/api/app/workers/outbox/phone_provisioning.py` — deep provisioning operation named `provision_phone_number`.
- `apps/api/app/workers/outbox/customer_dispatch.py` — customer-call LiveKit domain behavior.
- `apps/api/app/workers/outbox/verification_dispatch.py` — forwarding-verification LiveKit domain behavior.
- `apps/api/app/workers/outbox/_livekit_delivery.py` — phase-two common provider algorithm.
- `apps/api/app/workers/outbox/post_call.py` — summary and recording topic adapters.
- `apps/api/app/workers/outbox/recording_reconciliation.py` — deep recording reconciliation operation.
- `apps/api/app/workers/outbox/account_deactivation.py` — existing account-deactivation topic state machine.
- `apps/api/app/workers/outbox/provider_cleanup.py` — existing provider-cleanup topic workflow.
- `apps/api/tests/workers/test_outbox_failures.py` — focused bounded-failure policy tests.
- `apps/api/tests/workers/test_outbox_architecture.py` — registry, import, and eager-loading boundary tests.
- `apps/api/tests/workers/test_livekit_delivery.py` — common LiveKit provider algorithm tests.

### Delete after migration

- `apps/api/app/workers/jobs/outbox_delivery.py`
- `apps/api/app/workers/jobs/outbox_topics.py`
- `apps/api/app/workers/jobs/account_deactivation.py`
- `apps/api/app/workers/jobs/provider_cleanup.py`
- `apps/api/app/workers/jobs/phone_provisioning.py`
- `apps/api/app/workers/jobs/recording_reconciliation.py`
- `apps/api/app/workers/jobs/summary.py`

### Modify production and durable documentation

- `apps/api/app/services/outbox_service.py` — derive supported topics from payload schemas.
- `apps/api/app/workers/arq_worker.py` — import delivery jobs from the new package.
- `docs/engineering/2026-07-30-agent-api-review-decisions.md` — mark Issue 5A implemented only after final verification.

### Modify tests to point at their true owner

- `apps/api/tests/activation/test_activation_go_live_service.py`
- `apps/api/tests/billing/test_stripe_webhooks.py`
- `apps/api/tests/contracts/test_dispatch_compatibility.py`
- `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- `apps/api/tests/integration/test_agent_runtime_transcript_durability.py`
- `apps/api/tests/integration/test_forwarding_verification_privacy.py`
- `apps/api/tests/integration/test_local_activation_to_number.py`
- `apps/api/tests/integration/test_outbox_delivery.py`
- `apps/api/tests/integration/test_recording_egress_concurrency.py`
- `apps/api/tests/livekit/test_durable_dispatch_service.py`
- `apps/api/tests/services/test_safe_service_exceptions.py`
- `apps/api/tests/test_deployment_readiness.py`
- `apps/api/tests/test_observability.py`
- `apps/api/tests/test_redaction.py`
- `apps/api/tests/workers/test_account_deactivation.py`
- `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py`
- `apps/api/tests/workers/test_individual_jobs.py`
- `apps/api/tests/workers/test_livekit_dispatch_outbox.py`
- `apps/api/tests/workers/test_phone_provisioning_cleanup.py`
- `apps/api/tests/workers/test_phone_routing_readiness.py`
- `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- `apps/api/tests/workers/test_provider_cleanup.py`
- `apps/api/tests/workers/test_recording_reconciliation.py`

## Phase One — Behavior-Preserving Relocation

### Task 1: Isolate bounded delivery failures

**Files:**
- Create: `apps/api/app/workers/outbox/__init__.py`
- Create: `apps/api/app/workers/outbox/failures.py`
- Create: `apps/api/tests/workers/test_outbox_failures.py`
- Modify: `apps/api/app/workers/jobs/outbox_delivery.py:23-169`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py:75-85`
- Modify: `apps/api/app/workers/jobs/account_deactivation.py:1-25`
- Modify: `apps/api/app/workers/jobs/provider_cleanup.py:1-20`
- Modify: `apps/api/tests/integration/test_outbox_delivery.py:1-1205`
- Modify: `apps/api/tests/services/test_safe_service_exceptions.py:1-80`

**Interfaces:**
- Produces: `OutboxDeliveryError`, `provider_failure_delivery_error`, `_classify_error`, `_outbox_error_class`, and `SAFE_OUTBOX_ERROR_CODES` from `app.workers.outbox.failures`.
- Preserves: `OutboxDeliveryError(error_code: str, *, retryable: bool, exhaustible: bool = True)` and every existing error-code mapping.
- Consumed by: delivery and every topic handler in later tasks.

- [ ] **Step 1: Record the focused pre-change baseline**

Run from `apps/api`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_outbox_delivery.py::test_root_outbox_classifier_separates_provider_and_internal_failures \
  tests/integration/test_outbox_delivery.py::test_provider_failure_delivery_error_preserves_default_durable_policy \
  tests/services/test_safe_service_exceptions.py
```

Expected: all selected tests pass. Save the pass count in the task notes; do not edit a coverage baseline from a focused run.

- [ ] **Step 2: Write the focused failure-module tests**

Create `tests/workers/test_outbox_failures.py` with direct ownership assertions:

```python
import pytest

from app.core.provider_failures import ProviderFailure
from app.services.outbox_service import OutboxPayloadError
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    _classify_error,
    _outbox_error_class,
    provider_failure_delivery_error,
)


def test_outbox_delivery_error_rejects_unsafe_codes() -> None:
    with pytest.raises(ValueError, match="Unsafe outbox error code"):
        OutboxDeliveryError("private-provider-message", retryable=False)


def test_non_exhausting_delivery_error_must_be_retryable() -> None:
    with pytest.raises(ValueError, match="must be exhaustible"):
        OutboxDeliveryError(
            "provider_terminal",
            retryable=False,
            exhaustible=False,
        )


def test_provider_failure_mapping_preserves_retryability() -> None:
    failure = ProviderFailure(
        provider="livekit",
        operation="list_dispatches",
        disposition="retryable",
        error_class="timeout",
    )

    mapped = provider_failure_delivery_error(failure)

    assert mapped.error_code == "provider_retryable"
    assert mapped.retryable is True
    assert mapped.exhaustible is True


def test_payload_and_unknown_failures_remain_distinct() -> None:
    assert _classify_error(OutboxPayloadError("opaque")) == (
        "invalid_payload",
        False,
        True,
    )
    assert _classify_error(RuntimeError("private")) == (
        "internal_defect",
        False,
        True,
    )
    assert _outbox_error_class("provider_retryable") == "unavailable"
    assert _outbox_error_class("dispatch_conflict") == "conflict"
```

- [ ] **Step 3: Run RED for the new package seam**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/workers/test_outbox_failures.py
```

Expected: collection fails because `app.workers.outbox.failures` does not exist.

- [ ] **Step 4: Move the failure policy without changing values**

Use `apply_patch` to create an empty `outbox/__init__.py`. Move these exact definitions from `jobs/outbox_delivery.py` into `outbox/failures.py`:

```text
SAFE_OUTBOX_ERROR_CODES
OutboxDeliveryError
provider_failure_delivery_error
_classify_error
_outbox_error_class
```

Keep every safe code and every mapping unchanged. `failures.py` imports only `ProviderFailure` and `OutboxPayloadError` from the application layer plus standard-library types. In the old delivery module, replace the removed definitions with:

```python
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    _classify_error,
    _outbox_error_class,
)
```

Update the three existing topic modules to import their error interfaces directly:

```python
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)
```

`account_deactivation.py` and `provider_cleanup.py` import only `OutboxDeliveryError`.

- [ ] **Step 5: Retarget direct failure-policy test imports**

In `test_outbox_delivery.py` and `test_safe_service_exceptions.py`, import failure symbols from `app.workers.outbox.failures`. Keep imports of `outbox_delivery_job` and `outbox_reconciliation_job` at their old path until Task 2.

- [ ] **Step 6: Run GREEN and static checks**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_outbox_failures.py \
  tests/integration/test_outbox_delivery.py::test_root_outbox_classifier_separates_provider_and_internal_failures \
  tests/integration/test_outbox_delivery.py::test_provider_failure_delivery_error_preserves_default_durable_policy \
  tests/services/test_safe_service_exceptions.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/failures.py \
  app/workers/jobs/outbox_delivery.py \
  app/workers/jobs/outbox_topics.py \
  app/workers/jobs/account_deactivation.py \
  app/workers/jobs/provider_cleanup.py \
  tests/workers/test_outbox_failures.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/workers/outbox/failures.py \
  app/workers/jobs/outbox_delivery.py
```

Expected: every command exits zero; the focused tests preserve existing classifications and the new invariant tests pass.

- [ ] **Step 7: Commit the bounded failure seam**

```bash
git add apps/api/app/workers/outbox apps/api/app/workers/jobs/outbox_delivery.py \
  apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/app/workers/jobs/account_deactivation.py \
  apps/api/app/workers/jobs/provider_cleanup.py \
  apps/api/tests/workers/test_outbox_failures.py \
  apps/api/tests/integration/test_outbox_delivery.py \
  apps/api/tests/services/test_safe_service_exceptions.py
git commit -m "refactor(api): isolate outbox failure policy"
```

### Task 2: Move the delivery engine without eager topic imports

**Files:**
- Create: `apps/api/app/workers/outbox/delivery.py`
- Create: `apps/api/tests/workers/test_outbox_architecture.py`
- Delete: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/app/workers/arq_worker.py:14-22`
- Modify: every test file in the locked test list that imports `app.workers.jobs.outbox_delivery`

**Interfaces:**
- Produces: `OutboxHandler`, `outbox_delivery_job`, `outbox_reconciliation_job`, `emit_outbox_terminal_failure_metric`, and `get_default_outbox_handlers` at `app.workers.outbox.delivery`.
- Preserves: handler injection through `ctx["outbox_handlers"]` and local fallback to the current `outbox_topics.DEFAULT_OUTBOX_HANDLERS` until Task 8.
- Consumes: failure policy from Task 1.

- [ ] **Step 1: Write the delivery import-boundary test**

Create `tests/workers/test_outbox_architecture.py`:

```python
import os
from pathlib import Path
import subprocess
import sys


API_ROOT = Path(__file__).resolve().parents[2]


def test_delivery_import_does_not_eagerly_import_topic_providers() -> None:
    script = "\n".join(
        (
            "import sys",
            "import app.workers.outbox.delivery",
            "forbidden = {",
            "    'app.providers.livekit_dispatch.livekit',",
            "    'app.providers.summaries.gemini',",
            "    'app.providers.telephony.factory',",
            "}",
            "loaded = forbidden.intersection(sys.modules)",
            "assert not loaded, sorted(loaded)",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(API_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run RED for the delivery location**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/workers/test_outbox_architecture.py
```

Expected: the test subprocess fails because `app.workers.outbox.delivery` does not exist.

- [ ] **Step 3: Relocate the delivery engine exactly**

Use `apply_patch` to add `outbox/delivery.py` with the remaining contents of `jobs/outbox_delivery.py`, preserving definition order and logic. Its failure import is:

```python
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    _classify_error,
    _outbox_error_class,
)
```

Keep the default lookup local and temporarily pointed at the old registry:

```python
def get_default_outbox_handlers() -> Mapping[str, OutboxHandler]:
    from app.workers.jobs.outbox_topics import DEFAULT_OUTBOX_HANDLERS

    return DEFAULT_OUTBOX_HANDLERS
```

Delete `jobs/outbox_delivery.py` in the same patch.

- [ ] **Step 4: Retarget every production and test import atomically**

Update `arq_worker.py` and every active test returned by:

```bash
rg -l 'app\.workers\.jobs\.outbox_delivery|from app\.workers\.jobs import outbox_delivery' app tests --glob '*.py'
```

Use `app.workers.outbox.delivery` for delivery jobs and `app.workers.outbox.failures` for errors/classification. Do not re-export failure symbols from `delivery.py` merely to reduce import edits.

- [ ] **Step 5: Prove the legacy delivery path is gone**

Run:

```bash
test ! -e app/workers/jobs/outbox_delivery.py
test -e app/workers/outbox/delivery.py
! rg -n 'app\.workers\.jobs\.outbox_delivery|from app\.workers\.jobs import outbox_delivery' app tests --glob '*.py'
```

Expected: all checks exit zero and the final search prints no matches.

- [ ] **Step 6: Run focused delivery, startup, and import tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_outbox_architecture.py \
  tests/workers/test_outbox_failures.py \
  tests/integration/test_outbox_delivery.py \
  tests/test_observability.py \
  tests/test_redaction.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/workers/outbox app/workers/arq_worker.py tests/workers/test_outbox_architecture.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/workers/outbox/delivery.py app/workers/arq_worker.py
```

Expected: import and static checks pass. PostgreSQL-only tests may skip in this focused local command; Task 8 runs them against disposable PostgreSQL.

- [ ] **Step 7: Commit the delivery-engine move**

```bash
git add apps/api/app/workers/outbox/delivery.py \
  apps/api/app/workers/jobs/outbox_delivery.py \
  apps/api/app/workers/arq_worker.py \
  apps/api/tests
git commit -m "refactor(api): move outbox delivery engine"
```

### Task 3: Move account deactivation and provider cleanup

**Files:**
- Create: `apps/api/app/workers/outbox/account_deactivation.py`
- Create: `apps/api/app/workers/outbox/provider_cleanup.py`
- Delete: `apps/api/app/workers/jobs/account_deactivation.py`
- Delete: `apps/api/app/workers/jobs/provider_cleanup.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py:86-91`
- Modify: `apps/api/tests/workers/test_account_deactivation.py`
- Modify: `apps/api/tests/workers/test_provider_cleanup.py`
- Modify: `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- Modify: `apps/api/tests/workers/test_phone_provisioning_cleanup.py`

**Interfaces:**
- Produces: `deliver_account_deactivation(ctx, event) -> None` and `deliver_provider_cleanup(ctx, event) -> None` at their new package paths.
- Preserves: all account state-machine steps, safe telemetry, single-flight locking, provider compensation, and durable cleanup state.
- Consumes: `OutboxDeliveryError` from Task 1.

- [ ] **Step 1: Retarget the focused tests first**

Change imports in the four listed test files to:

```python
from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup
```

Only include the import each file actually uses.

- [ ] **Step 2: Run RED for the new handler locations**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_account_deactivation.py \
  tests/workers/test_provider_cleanup.py \
  tests/workers/test_phone_provisioning_cleanup.py
```

Expected: collection fails on the missing new modules.

- [ ] **Step 3: Relocate both cohesive modules without internal redesign**

Use `apply_patch` to add both modules at their new paths and delete the old files. Preserve every definition and statement. Their only architectural import change is:

```python
from app.workers.outbox.failures import OutboxDeliveryError
```

Update the temporary registry imports in `jobs/outbox_topics.py`:

```python
from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup
```

- [ ] **Step 4: Retarget remaining active imports and monkeypatch paths**

Run:

```bash
rg -n 'app\.workers\.jobs\.(account_deactivation|provider_cleanup)' app tests --glob '*.py'
```

Update every match to the corresponding `app.workers.outbox` module. Repeat the search and require no output.

- [ ] **Step 5: Run handler and concurrency characterization tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_account_deactivation.py \
  tests/workers/test_provider_cleanup.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/integration/test_account_deactivation_concurrency.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/account_deactivation.py \
  app/workers/outbox/provider_cleanup.py \
  tests/workers/test_account_deactivation.py \
  tests/workers/test_provider_cleanup.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/workers/outbox/account_deactivation.py \
  app/workers/outbox/provider_cleanup.py
```

Expected: unit tests and static checks pass; PostgreSQL concurrency tests run at Task 8's mandatory phase gate if they skip here.

- [ ] **Step 6: Commit the handler relocation**

```bash
git add apps/api/app/workers/outbox/account_deactivation.py \
  apps/api/app/workers/outbox/provider_cleanup.py \
  apps/api/app/workers/jobs/account_deactivation.py \
  apps/api/app/workers/jobs/provider_cleanup.py \
  apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/tests
git commit -m "refactor(api): move account cleanup handlers"
```

### Task 4: Isolate phone delivery and accurately rename provisioning

**Files:**
- Create: `apps/api/app/workers/outbox/_account_lifecycle.py`
- Create: `apps/api/app/workers/outbox/phone.py`
- Create: `apps/api/app/workers/outbox/phone_provisioning.py`
- Delete: `apps/api/app/workers/jobs/phone_provisioning.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py:94-709,1310-1346,1615-1620`
- Modify: `apps/api/tests/activation/test_activation_go_live_service.py`
- Modify: `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- Modify: `apps/api/tests/integration/test_outbox_delivery.py`
- Modify: `apps/api/tests/services/test_safe_service_exceptions.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py`
- Modify: `apps/api/tests/workers/test_phone_provisioning_cleanup.py`
- Modify: `apps/api/tests/workers/test_phone_routing_readiness.py`

**Interfaces:**
- Produces: `deliver_phone_provision`, `deliver_phone_routing`, `_routing_snapshot`, and phone-private helpers in `outbox.phone`.
- Produces: `provision_phone_number(ctx, payload, *, provider_operation_key: str | None = None) -> None` in `outbox.phone_provisioning` with the old operation's behavior.
- Produces: `_validated_lifecycle_generation(event) -> int` and `_require_current_worker_account(session_factory, user_id, *, lifecycle_generation) -> None` in `outbox._account_lifecycle`.
- Preserves: phone admission, stale-operation recovery, pending-provider non-exhaustion, routing compensation, projection, and current-account checks.

- [ ] **Step 1: Retarget phone tests to the approved interfaces**

Replace phone handler imports and monkeypatch targets with:

```python
from app.workers.outbox import phone as phone_outbox
from app.workers.outbox.phone import (
    _routing_snapshot,
    deliver_phone_provision,
    deliver_phone_routing,
)
from app.workers.outbox.phone_provisioning import provision_phone_number
```

Rename test functions and local helper names containing `phone_provisioning_job` to `provision_phone_number`. Keep their assertions unchanged.

- [ ] **Step 2: Run RED for phone package ownership**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/workers/test_individual_jobs.py
```

Expected: collection fails because the new phone modules do not exist.

- [ ] **Step 3: Extract the shared lifecycle policy exactly**

Move `_validated_lifecycle_generation` and `_require_current_worker_account` from `outbox_topics.py` into `_account_lifecycle.py` with their current behavior. Use these imports:

```python
from app.workers.outbox.failures import OutboxDeliveryError
```

Keep `UserRepository`, `require_current_account_lifecycle`, `AccountStateBlockedError`, and `AccountLifecycleGenerationMismatchError` local to the shared policy module.

- [ ] **Step 4: Move and rename the deep provisioning operation**

Move all contents of `jobs/phone_provisioning.py` to `outbox/phone_provisioning.py`. Rename only the public operation and its fixed safe log prefix:

```python
async def provision_phone_number(
    ctx: dict[str, Any],
    payload: dict[str, Any],
    *,
    provider_operation_key: str | None = None,
) -> None:
```

Replace fixed occurrences of `phone_provisioning_job:` in safe log templates with `provision_phone_number:`. Do not change payloads, provider keys, error categorization, exception chaining, transactions, or return values. Delete the old module.

- [ ] **Step 5: Move the complete phone topic family**

Move these exact definitions from `outbox_topics.py` into `outbox/phone.py`:

```text
_RoutingSnapshot
_PhoneProvisionAdmission
deliver_phone_provision
_phone_provision_admission
_event_matches_provider_operation
deliver_phone_routing
_set_routing_target
_clear_routing_target
_compensate_provider_enable
_persist_phone_projection
_routing_snapshot
```

Use:

```python
from app.workers.outbox._account_lifecycle import (
    _require_current_worker_account,
    _validated_lifecycle_generation,
)
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)
from app.workers.outbox.phone_provisioning import provision_phone_number
```

Change the one call in `deliver_phone_provision` from `phone_provisioning_job` to `provision_phone_number`. Keep `deliver_phone_provision` calling `deliver_phone_routing` after a durable number exists.

The temporary `outbox_topics.py` registry imports `deliver_phone_provision` and `deliver_phone_routing` from `outbox.phone`. Its remaining customer and verification code imports the two lifecycle helpers from `_account_lifecycle` until Tasks 5 and 6 move those domains.

- [ ] **Step 6: Remove every obsolete phone name and path**

Run:

```bash
! rg -n 'app\.workers\.jobs\.phone_provisioning|phone_provisioning_job' app tests --glob '*.py'
test ! -e app/workers/jobs/phone_provisioning.py
```

Expected: no matches and no old file.

- [ ] **Step 7: Run the complete phone characterization set**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/workers/test_individual_jobs.py \
  tests/activation/test_activation_go_live_service.py \
  tests/services/test_safe_service_exceptions.py \
  tests/integration/test_outbox_delivery.py \
  tests/integration/test_account_deactivation_concurrency.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/_account_lifecycle.py \
  app/workers/outbox/phone.py \
  app/workers/outbox/phone_provisioning.py \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/workers/test_individual_jobs.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/workers/outbox/_account_lifecycle.py \
  app/workers/outbox/phone.py \
  app/workers/outbox/phone_provisioning.py
```

Expected: all available tests and static checks pass; Task 8 supplies mandatory PostgreSQL execution.

- [ ] **Step 8: Commit the phone family**

```bash
git add apps/api/app/workers/outbox apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/app/workers/jobs/phone_provisioning.py apps/api/tests
git commit -m "refactor(api): isolate phone outbox workflow"
```

### Task 5: Isolate customer-call dispatch

**Files:**
- Create: `apps/api/app/workers/outbox/customer_dispatch.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py:111-120,710-1077,1615-1622`
- Modify: `apps/api/tests/contracts/test_dispatch_compatibility.py`
- Modify: `apps/api/tests/integration/test_outbox_delivery.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_service.py`
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py`

**Interfaces:**
- Produces: `deliver_livekit_dispatch`, `_DispatchSnapshot`, `_dispatch_snapshot`, `_reconcile_dispatches`, and customer-private persistence/validation helpers.
- Preserves: customer readiness, dispatch token construction, contract metadata, LiveKit lock, provider behavior, reconciliation, and call identity persistence.
- Consumes: shared lifecycle policy and bounded failures.

- [ ] **Step 1: Retarget customer-dispatch tests**

Use explicit imports and module monkeypatch targets:

```python
from app.workers.outbox import customer_dispatch
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch
```

Replace `outbox_topics._DispatchSnapshot`, `_dispatch_snapshot`, `_reconcile_dispatches`, and `create_dispatch_token` targets with `customer_dispatch` ownership.

- [ ] **Step 2: Run RED for the customer module**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/contracts/test_dispatch_compatibility.py \
  tests/livekit/test_durable_dispatch_service.py
```

Expected: collection fails because `app.workers.outbox.customer_dispatch` does not exist.

- [ ] **Step 3: Move the customer domain unchanged**

Move these exact definitions into `customer_dispatch.py`:

```text
_DispatchSnapshot
deliver_livekit_dispatch
_validated_dispatch_reference
_dispatch_snapshot
_reconcile_dispatches
_persist_dispatch_identity
```

Give this module a customer-specific metadata adapter rather than coupling it to verification:

```python
def _parse_customer_dispatch_metadata(
    metadata: object,
) -> CustomerCallDispatch | None:
    try:
        parsed = parse_dispatch(metadata)
    except ContractError:
        return None
    if not isinstance(parsed, CustomerCallDispatch):
        return None
    return parsed
```

Use `_parse_customer_dispatch_metadata` inside `_reconcile_dispatches`. The short typed adapter is domain-specific; do not add a cross-domain contract-helper module.

Import lifecycle and failures from:

```python
from app.workers.outbox._account_lifecycle import _require_current_worker_account
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)
```

Keep the provider algorithm duplicated in phase one. The temporary registry imports `deliver_livekit_dispatch` from the new module.

- [ ] **Step 4: Prove customer ownership and remove old references**

Run:

```bash
! rg -n 'outbox_topics\.(?:_DispatchSnapshot|_dispatch_snapshot|_reconcile_dispatches|create_dispatch_token)' tests --glob '*.py'
! rg -n 'from app\.workers\.jobs\.outbox_topics import deliver_livekit_dispatch' app tests --glob '*.py'
```

Expected: no matches.

- [ ] **Step 5: Run customer behavior, integration, and static checks**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/contracts/test_dispatch_compatibility.py \
  tests/livekit/test_durable_dispatch_service.py \
  tests/integration/test_outbox_delivery.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/customer_dispatch.py \
  tests/workers/test_livekit_dispatch_outbox.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/workers/outbox/customer_dispatch.py
```

Expected: customer dispatch behavior and current provider ambiguity handling remain green.

- [ ] **Step 6: Commit the customer family**

```bash
git add apps/api/app/workers/outbox/customer_dispatch.py \
  apps/api/app/workers/jobs/outbox_topics.py apps/api/tests
git commit -m "refactor(api): isolate customer dispatch handler"
```

### Task 6: Isolate forwarding-verification dispatch

**Files:**
- Create: `apps/api/app/workers/outbox/verification_dispatch.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py:122-131,1078-1445,1615-1623`
- Modify: `apps/api/tests/contracts/test_dispatch_compatibility.py`
- Modify: `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- Modify: `apps/api/tests/integration/test_outbox_delivery.py`
- Modify: `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py`

**Interfaces:**
- Produces: `deliver_livekit_verification_dispatch`, `_VerificationDispatchSnapshot`, `_verification_dispatch_snapshot`, `_reconcile_verification_dispatches`, and verification-private validation/persistence helpers.
- Preserves: verification window/session policy, verification token construction, contract metadata, verification lock, provider behavior, reconciliation, and activation persistence.
- Consumes: shared lifecycle policy and bounded failures.

- [ ] **Step 1: Retarget verification tests**

Use:

```python
from app.workers.outbox import verification_dispatch
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch,
)
```

Retarget `_VerificationDispatchSnapshot`, `_verification_dispatch_snapshot`, `_reconcile_verification_dispatches`, and `create_verification_token` monkeypatches to the verification module.

- [ ] **Step 2: Run RED for the verification module**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/contracts/test_dispatch_compatibility.py
```

Expected: collection fails because the verification module does not exist.

- [ ] **Step 3: Move the verification domain unchanged**

Move these exact definitions into `verification_dispatch.py`:

```text
_VerificationDispatchSnapshot
deliver_livekit_verification_dispatch
_validated_verification_dispatch_reference
_verification_dispatch_snapshot
_reconcile_verification_dispatches
_verification_agent_identity
_persist_verification_dispatch_identity
```

Add the domain-specific metadata adapter:

```python
def _parse_verification_dispatch_metadata(
    metadata: object,
) -> ForwardingVerificationDispatch | None:
    try:
        parsed = parse_dispatch(metadata)
    except ContractError:
        return None
    if not isinstance(parsed, ForwardingVerificationDispatch):
        return None
    return parsed
```

Use the shared lifecycle and failure imports from Tasks 1 and 4. Keep the provider algorithm duplicated through the phase-one gate. The temporary registry imports `deliver_livekit_verification_dispatch` from the new module.

- [ ] **Step 4: Remove every verification reference to the god module**

Run:

```bash
! rg -n 'outbox_topics\.(?:_VerificationDispatchSnapshot|_verification_dispatch_snapshot|_reconcile_verification_dispatches|create_verification_token)' tests --glob '*.py'
! rg -n 'from app\.workers\.jobs\.outbox_topics import deliver_livekit_verification_dispatch' app tests --glob '*.py'
```

Expected: no matches.

- [ ] **Step 5: Run verification behavior and static checks**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/contracts/test_dispatch_compatibility.py \
  tests/integration/test_account_deactivation_concurrency.py \
  tests/integration/test_outbox_delivery.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/verification_dispatch.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/workers/outbox/verification_dispatch.py
```

Expected: verification session, race, privacy, and provider semantics remain green.

- [ ] **Step 6: Commit the verification family**

```bash
git add apps/api/app/workers/outbox/verification_dispatch.py \
  apps/api/app/workers/jobs/outbox_topics.py apps/api/tests
git commit -m "refactor(api): isolate verification dispatch handler"
```

### Task 7: Isolate post-call delivery and remove the dead summary job

**Files:**
- Create: `apps/api/app/workers/outbox/post_call.py`
- Create: `apps/api/app/workers/outbox/recording_reconciliation.py`
- Delete: `apps/api/app/workers/jobs/recording_reconciliation.py`
- Delete: `apps/api/app/workers/jobs/summary.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py:1446-1614`
- Modify: `apps/api/tests/integration/test_agent_runtime_transcript_durability.py`
- Modify: `apps/api/tests/integration/test_recording_egress_concurrency.py`
- Modify: `apps/api/tests/services/test_safe_service_exceptions.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py:1-130`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- Modify: `apps/api/tests/workers/test_recording_reconciliation.py`

**Interfaces:**
- Produces: `deliver_summary_generate`, `build_recording_reconciler`, and `deliver_recording_reconcile` from `outbox.post_call`.
- Produces: `ReconciliationResult`, `RECORDING_RECONCILIATION_ERROR_CODES`, and `RecordingReconciler` from `outbox.recording_reconciliation`.
- Preserves: summary stale-snapshot checks, recording outcome validation, non-exhaustible retries, and telemetry.
- Deletes: the unreachable `summary_job` path and only its three legacy tests.

- [ ] **Step 1: Retarget production-path post-call tests**

Use:

```python
from app.workers.outbox import post_call
from app.workers.outbox.post_call import (
    deliver_recording_reconcile,
    deliver_summary_generate,
)
from app.workers.outbox.recording_reconciliation import (
    ReconciliationResult,
    RecordingReconciler,
)
```

Retarget `build_recording_reconciler` and `get_observability` monkeypatches to `post_call`.

- [ ] **Step 2: Run RED for post-call ownership**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_recording_reconciliation.py
```

Expected: collection fails because the new modules do not exist.

- [ ] **Step 3: Move the recording reconciler unchanged**

Move all contents of `jobs/recording_reconciliation.py` to `outbox/recording_reconciliation.py`. Preserve every protocol, state comparison, provider/storage boundary, conflict merge, transaction, and outbox re-enqueue operation. Delete the old file and update all active imports.

- [ ] **Step 4: Move both production post-call adapters**

Move these definitions into `outbox/post_call.py`:

```text
deliver_summary_generate
build_recording_reconciler
deliver_recording_reconcile
_validated_recording_operation_reference
_validated_post_call_reference
```

Update lazy reconciler imports to `app.workers.outbox.recording_reconciliation`. Import failures only from `outbox.failures`. The temporary registry imports both delivery functions from `outbox.post_call`.

- [ ] **Step 5: Delete the dead summary entry point and only its legacy tests**

Delete `jobs/summary.py`. In `test_individual_jobs.py`:

- remove the summary bullet from the module docstring;
- remove `dataclass` if it has no remaining use;
- remove `FakeSummaryResult`, `FakeSummaryService`, and `FakeFailingSummaryService`;
- remove exactly `test_summary_job_happy_path`, `test_summary_job_provider_failure`, and `test_summary_job_empty_transcript`;
- retain `CTX` because notification tests use it; and
- retain every notification and phone provisioning test.

Run:

```bash
! rg -n 'app\.workers\.jobs\.(summary|recording_reconciliation)|summary_job' app tests --glob '*.py'
```

Expected: no matches.

- [ ] **Step 6: Run the complete post-call and recording characterization set**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_recording_reconciliation.py \
  tests/workers/test_individual_jobs.py \
  tests/integration/test_agent_runtime_transcript_durability.py \
  tests/integration/test_recording_egress_concurrency.py \
  tests/services/test_safe_service_exceptions.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/post_call.py \
  app/workers/outbox/recording_reconciliation.py \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_recording_reconciliation.py \
  tests/workers/test_individual_jobs.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/workers/outbox/post_call.py \
  app/workers/outbox/recording_reconciliation.py
```

Expected: production post-call coverage stays green; only the three unreachable legacy tests disappear.

- [ ] **Step 7: Commit the post-call family and dead-code deletion**

```bash
git add apps/api/app/workers/outbox apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/app/workers/jobs/recording_reconciliation.py \
  apps/api/app/workers/jobs/summary.py apps/api/tests
git commit -m "refactor(api): isolate post-call outbox workflows"
```

### Task 8: Install the explicit registry, delete the god module, and pass phase one

**Files:**
- Create: `apps/api/app/workers/outbox/registry.py`
- Modify: `apps/api/app/workers/outbox/delivery.py:381-385`
- Modify: `apps/api/app/services/outbox_service.py:11-35`
- Modify: `apps/api/tests/workers/test_outbox_architecture.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py:699-716`
- Modify: `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py:805-815`
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py:1037-1070`
- Modify: `apps/api/tests/test_deployment_readiness.py:1145-1165`
- Delete: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: every remaining active import or monkeypatch path found by the legacy-path scans below

**Interfaces:**
- Produces: `DEFAULT_OUTBOX_HANDLERS` at `app.workers.outbox.registry`.
- Produces: `SUPPORTED_OUTBOX_TOPICS = frozenset(REFERENCE_PAYLOAD_FIELDS)`.
- Preserves: local default registry acquisition and injected handler override.
- Completes: a fully green, facade-free phase-one package.

- [ ] **Step 1: Add registry and startup architecture tests before implementation**

Extend `test_outbox_architecture.py`:

```python
from app.services.outbox_service import (
    REFERENCE_PAYLOAD_FIELDS,
    SUPPORTED_OUTBOX_TOPICS,
)
from app.workers.outbox.delivery import get_default_outbox_handlers
from app.workers.outbox import delivery
from app.workers.outbox.registry import DEFAULT_OUTBOX_HANDLERS
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_registry_exactly_matches_payload_schema_topics() -> None:
    assert SUPPORTED_OUTBOX_TOPICS == frozenset(REFERENCE_PAYLOAD_FIELDS)
    assert frozenset(DEFAULT_OUTBOX_HANDLERS) == SUPPORTED_OUTBOX_TOPICS
    assert all(callable(handler) for handler in DEFAULT_OUTBOX_HANDLERS.values())


def test_default_handler_lookup_returns_the_explicit_registry() -> None:
    assert get_default_outbox_handlers() is DEFAULT_OUTBOX_HANDLERS


def test_worker_settings_imports_with_the_complete_registry() -> None:
    script = """
from app.services.outbox_service import SUPPORTED_OUTBOX_TOPICS
from app.workers import arq_worker
from app.workers.outbox.registry import DEFAULT_OUTBOX_HANDLERS

if arq_worker.on_background_startup is None:
    raise SystemExit("Background worker startup hook is missing")
if frozenset(DEFAULT_OUTBOX_HANDLERS) != SUPPORTED_OUTBOX_TOPICS:
    raise SystemExit("Default outbox registry is incomplete")
"""

    completed = subprocess.run(
        [sys.executable, "-E", "-c", script],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        pytest.fail(completed.stderr)


@pytest.mark.anyio
async def test_injected_handlers_bypass_default_registry(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_default_lookup():
        pytest.fail("default registry must not load when handlers are injected")

    monkeypatch.setattr(
        delivery,
        "get_default_outbox_handlers",
        fail_default_lookup,
    )
    session_factory = async_sessionmaker(
        db_session.bind,
        expire_on_commit=False,
    )

    result = await delivery.outbox_delivery_job(
        {
            "session_factory": session_factory,
            "outbox_handlers": {},
        }
    )

    assert result == {
        "claimed": 0,
        "delivered": 0,
        "retried": 0,
        "failed": 0,
    }
```

- [ ] **Step 2: Run RED for the registry module**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/workers/test_outbox_architecture.py
```

Expected: collection fails because `app.workers.outbox.registry` does not exist.

- [ ] **Step 3: Create the one explicit registry**

Create `outbox/registry.py` with this exact visible map:

```python
from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch
from app.workers.outbox.phone import deliver_phone_provision, deliver_phone_routing
from app.workers.outbox.post_call import (
    deliver_recording_reconcile,
    deliver_summary_generate,
)
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch,
)


DEFAULT_OUTBOX_HANDLERS = {
    "account.deactivate": deliver_account_deactivation,
    "provider.cleanup": deliver_provider_cleanup,
    "phone.provision": deliver_phone_provision,
    "phone.enable": deliver_phone_routing,
    "phone.disable": deliver_phone_routing,
    "livekit.dispatch": deliver_livekit_dispatch,
    "livekit.verification_dispatch": deliver_livekit_verification_dispatch,
    "summary.generate": deliver_summary_generate,
    "recording.reconcile": deliver_recording_reconcile,
}
```

Update the local lookup in `delivery.py` to import this map from `app.workers.outbox.registry`.

- [ ] **Step 4: Derive the supported topic set from payload schemas**

In `outbox_service.py`, define `REFERENCE_PAYLOAD_FIELDS` first and immediately derive:

```python
SUPPORTED_OUTBOX_TOPICS = frozenset(REFERENCE_PAYLOAD_FIELDS)
```

Do not introduce an enum or change any mapping key or required field.

- [ ] **Step 5: Retarget final registry consumers and delete the god module**

Update registry tests, deployment-readiness assertions, and direct handler imports to their true `outbox` modules. Delete `jobs/outbox_topics.py`; do not replace it with an alias or `__getattr__` shim.

Run these exact scans from `apps/api`:

```bash
! rg -n 'app\.workers\.jobs\.(outbox_topics|outbox_delivery|account_deactivation|provider_cleanup|phone_provisioning|recording_reconciliation|summary)' app tests --glob '*.py'
! rg -n 'from app\.workers\.jobs import outbox_topics|phone_provisioning_job|summary_job' app tests --glob '*.py'
test ! -e app/workers/jobs/outbox_topics.py
test ! -s app/workers/outbox/__init__.py
```

Expected: no legacy references, no god module, and an empty package initializer.

- [ ] **Step 6: Run all focused phase-one suites**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_outbox_architecture.py \
  tests/workers/test_outbox_failures.py \
  tests/workers/test_account_deactivation.py \
  tests/workers/test_provider_cleanup.py \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_phone_provisioning_cleanup.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_recording_reconciliation.py \
  tests/workers/test_individual_jobs.py \
  tests/contracts/test_dispatch_compatibility.py \
  tests/services/test_safe_service_exceptions.py \
  tests/test_deployment_readiness.py
```

Expected: all selected non-credentialed tests pass.

- [ ] **Step 7: Start isolated test dependencies and run the mandatory phase-one gate**

First confirm the exact disposable names are unused. If either check prints a container ID, stop and choose different task-specific exact names; do not delete an existing container.

```bash
docker ps -aq --filter 'name=^/presvo-issue5a-postgres$'
docker ps -aq --filter 'name=^/presvo-issue5a-redis$'
```

Then run the complete gate in one shell so cleanup always runs:

```bash
set -eu

cleanup_presvo_issue5a_dependencies() {
  docker stop presvo-issue5a-postgres presvo-issue5a-redis >/dev/null 2>&1 || true
}
trap cleanup_presvo_issue5a_dependencies EXIT HUP INT TERM

docker run --rm -d \
  --name presvo-issue5a-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ai_call_test \
  -p 127.0.0.1:55432:5432 \
  postgres:17.8-alpine
docker run --rm -d \
  --name presvo-issue5a-redis \
  -p 127.0.0.1:56379:6379 \
  redis:7.4.7-alpine

until docker exec presvo-issue5a-postgres pg_isready -U postgres -d ai_call_test >/dev/null 2>&1; do
  sleep 1
done
until docker exec presvo-issue5a-redis redis-cli ping | rg -q '^PONG$'; do
  sleep 1
done

cd apps/api
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call_test
export TEST_REDIS_URL=redis://127.0.0.1:56379/0
export UV_CACHE_DIR=/tmp/uv-cache

uv lock --check
uv run --frozen --no-sync ruff check app tests
uv run --frozen --no-sync mypy app
uv run --frozen --no-sync python -m pytest -q \
  --cov=app \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
uv run --frozen --no-sync python ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

Expected: every command exits zero; PostgreSQL and Redis integration tests do not skip because their URLs are missing; both disposable containers are stopped and removed by the trap. Phase two is forbidden if this step is not entirely green.

- [ ] **Step 8: Commit the complete phase-one package**

```bash
git add apps/api/app/services/outbox_service.py \
  apps/api/app/workers/outbox/registry.py \
  apps/api/app/workers/outbox/delivery.py \
  apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/tests
git commit -m "refactor(api): install explicit outbox registry"
```

## Phase Two — Tested LiveKit DRY Extraction

### Task 9: Extract the common LiveKit provider algorithm and migrate customer dispatch

**Files:**
- Create: `apps/api/app/workers/outbox/_livekit_delivery.py`
- Create: `apps/api/tests/workers/test_livekit_delivery.py`
- Modify: `apps/api/app/workers/outbox/customer_dispatch.py`
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py`

**Interfaces:**
- Produces: `ensure_livekit_dispatch(*, provider, room_name, worker_name, metadata, persisted_dispatch_id, revalidate_account, reconcile) -> LiveKitDispatch`.
- Consumes: `LiveKitDispatchProvider`, a no-argument async lifecycle revalidator, and a typed reconciliation callable over a dispatch list.
- Preserves: four account checks on the ambiguous-create path and all current provider failure mappings.
- Does not own: domain payloads, contract rules, locks, snapshots, or persistence.

- [ ] **Step 1: Write the common algorithm fakes and call-order tests**

Create `tests/workers/test_livekit_delivery.py` with a fake provider that records `list` and `create` operations, a revalidator that records `validate`, and a reconciler that records the dispatch IDs it receives. Include these named tests and exact outcomes:

```text
test_existing_dispatch_is_returned_without_create
  trace: validate, list, validate, reconcile(existing)

test_missing_dispatch_is_created_and_reconciled
  trace: validate, list, validate, reconcile(empty), validate, create,
         reconcile(created)

test_retryable_create_failure_relists_and_recovers
  trace: validate, list, validate, reconcile(empty), validate, create,
         list, validate, reconcile(recovered)

test_retryable_create_failure_without_recovery_is_retryable
  result: OutboxDeliveryError(provider_retryable, retryable=True)

test_terminal_create_failure_does_not_relist
  result: OutboxDeliveryError(provider_terminal, retryable=False)

test_initial_provider_configuration_failure_is_terminal_configuration
  result: OutboxDeliveryError(dispatch_configuration, retryable=False)

test_persisted_identity_without_provider_match_is_conflict
  result: OutboxDeliveryError(dispatch_conflict, retryable=False), no create

test_reconciliation_conflict_propagates_without_create
  result: the exact reconciliation OutboxDeliveryError

test_lifecycle_invalidation_before_list_prevents_provider_io
  result: the exact lifecycle OutboxDeliveryError, provider trace empty

test_lifecycle_invalidation_after_recovery_list_prevents_persistence_result
  result: the exact lifecycle OutboxDeliveryError after the recovery list
```

Use a retryable provider failure constructed as:

```python
ProviderFailure(
    provider="livekit",
    operation="create_dispatch",
    disposition="retryable",
    error_class="timeout",
)
```

The successful creation test must assert the exact requested `agent_name`, `room_name`, and `metadata`.

- [ ] **Step 2: Run RED for the common primitive**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/workers/test_livekit_delivery.py
```

Expected: collection fails because `_livekit_delivery.py` does not exist.

- [ ] **Step 3: Implement one private typed async function**

Implement this exact interface and call sequence:

```python
from collections.abc import Awaitable, Callable

from app.core.provider_failures import ProviderFailure
from app.providers.livekit_dispatch.base import (
    LiveKitDispatch,
    LiveKitDispatchProvider,
)
from app.providers.livekit_dispatch.livekit import (
    LiveKitDispatchConfigurationError,
)
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    provider_failure_delivery_error,
)


AccountRevalidator = Callable[[], Awaitable[None]]
DispatchReconciler = Callable[
    [list[LiveKitDispatch]],
    LiveKitDispatch | None,
]


async def ensure_livekit_dispatch(
    *,
    provider: LiveKitDispatchProvider,
    room_name: str,
    worker_name: str,
    metadata: str,
    persisted_dispatch_id: str | None,
    revalidate_account: AccountRevalidator,
    reconcile: DispatchReconciler,
) -> LiveKitDispatch:
    await revalidate_account()
    try:
        dispatches = await provider.list_dispatches(room_name=room_name)
    except LiveKitDispatchConfigurationError:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    except ProviderFailure as error:
        raise provider_failure_delivery_error(error) from error

    await revalidate_account()
    dispatch = reconcile(dispatches)
    if dispatch is not None:
        return dispatch
    if persisted_dispatch_id is not None:
        raise OutboxDeliveryError(
            "dispatch_conflict",
            retryable=False,
        )

    await revalidate_account()
    try:
        created_dispatch = await provider.create_dispatch(
            agent_name=worker_name,
            room_name=room_name,
            metadata=metadata,
        )
    except LiveKitDispatchConfigurationError:
        raise OutboxDeliveryError(
            "dispatch_configuration",
            retryable=False,
        ) from None
    except ProviderFailure as error:
        if not error.retryable:
            raise provider_failure_delivery_error(error) from error
        try:
            dispatches = await provider.list_dispatches(room_name=room_name)
        except ProviderFailure as list_error:
            raise provider_failure_delivery_error(list_error) from list_error
        await revalidate_account()
        dispatch = reconcile(dispatches)
        if dispatch is None:
            raise OutboxDeliveryError(
                "provider_retryable",
                retryable=True,
            ) from None
        return dispatch

    dispatch = reconcile([created_dispatch])
    if dispatch is None:
        raise OutboxDeliveryError(
            "provider_retryable",
            retryable=True,
        )
    return dispatch
```

Do not catch arbitrary exceptions, alter exception chaining, or add retry loops.

- [ ] **Step 4: Run the primitive matrix GREEN**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/workers/test_livekit_delivery.py
```

Expected: all ten common-algorithm tests pass, including exact call ordering.

- [ ] **Step 5: Migrate only customer dispatch to the primitive**

Inside `deliver_livekit_dispatch`, retain payload validation, lock, snapshot, provider construction, and persistence. Replace only the repeated list/create/recovery block with named local adapters:

```python
async def revalidate_account() -> None:
    await _require_current_worker_account(
        session_factory,
        snapshot.user_id,
        lifecycle_generation=lifecycle_generation,
    )

def reconcile(
    dispatches: list[LiveKitDispatch],
) -> LiveKitDispatch | None:
    return _reconcile_dispatches(snapshot, dispatches)

dispatch = await ensure_livekit_dispatch(
    provider=provider,
    room_name=snapshot.room_name,
    worker_name=snapshot.worker_name,
    metadata=snapshot.metadata,
    persisted_dispatch_id=snapshot.persisted_dispatch_id,
    revalidate_account=revalidate_account,
    reconcile=reconcile,
)
```

Persist with the unchanged `_persist_dispatch_identity` call after the helper returns. Verification dispatch remains unchanged in this task so the reviewer can compare it with the extracted sequence.

- [ ] **Step 6: Prove customer behavior and helper use**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_livekit_delivery.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/contracts/test_dispatch_compatibility.py \
  tests/integration/test_outbox_delivery.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/_livekit_delivery.py \
  app/workers/outbox/customer_dispatch.py \
  tests/workers/test_livekit_delivery.py \
  tests/workers/test_livekit_dispatch_outbox.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/workers/outbox/_livekit_delivery.py \
  app/workers/outbox/customer_dispatch.py
```

Expected: the common matrix and every customer dispatch edge case pass while verification still uses its known-good phase-one code.

- [ ] **Step 7: Commit the primitive and first consumer**

```bash
git add apps/api/app/workers/outbox/_livekit_delivery.py \
  apps/api/app/workers/outbox/customer_dispatch.py \
  apps/api/tests/workers/test_livekit_delivery.py \
  apps/api/tests/workers/test_livekit_dispatch_outbox.py
git commit -m "refactor(api): share livekit delivery orchestration"
```

### Task 10: Migrate verification dispatch and prove both consumers

**Files:**
- Modify: `apps/api/app/workers/outbox/verification_dispatch.py`
- Modify: `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py`
- Modify: `apps/api/tests/workers/test_livekit_delivery.py` only if a missing verification-neutral assertion is exposed before production edits

**Interfaces:**
- Consumes: the exact `ensure_livekit_dispatch` interface from Task 9.
- Preserves: verification lock, window/session validation, metadata, reconciliation, and activation persistence.
- Completes: removal of the duplicated provider list/create/recovery algorithm.

- [ ] **Step 1: Record the verification behavior characterization baseline**

This task is an explicitly approved, narrow exception to the failing-test-first rule because it is a pure dependency-ownership refactor with no intended observable behavior change. Do not add a permanent test that monkeypatches `ensure_livekit_dispatch` or asserts helper-call arguments; that would lock tests to implementation wiring instead of behavior.

Before production edits, inspect the existing verification suite against every behavior preserved by this task: lock ownership, window/session validation, metadata, provider list/create/recovery outcomes, reconciliation, account lifecycle revalidation, and activation persistence. If a real behavioral gap is found, add the smallest behavior-level test, run it RED for the missing behavior, and make it GREEN before continuing. Do not invent a seam assertion merely to force RED.

- [ ] **Step 2: Run the unchanged characterization suite before migration**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_forwarding_verification_dispatch_outbox.py
```

Expected: the complete verification suite passes before migration, establishing the characterization baseline. Record its test count in the task report.

- [ ] **Step 3: Replace only the verification provider block**

Keep verification reference validation, time provider, `verification_dispatch_lock`, snapshot, provider construction, and persistence unchanged. Add named `revalidate_account` and `reconcile` functions with verification's snapshot, then call `ensure_livekit_dispatch` using the same exact keyword interface as customer dispatch. Persist with `_persist_verification_dispatch_identity` only after the helper returns.

- [ ] **Step 4: Inspect the duplication removal explicitly**

Run:

```bash
rg -n 'list_dispatches|create_dispatch' \
  app/workers/outbox/customer_dispatch.py \
  app/workers/outbox/verification_dispatch.py \
  app/workers/outbox/_livekit_delivery.py
```

Expected: provider list/create calls appear only in `_livekit_delivery.py`. Domain modules may contain repository methods or testable names unrelated to provider invocation, but must not call the provider methods.

- [ ] **Step 5: Run both complete dispatch suites and static checks**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_livekit_delivery.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/contracts/test_dispatch_compatibility.py \
  tests/livekit/test_durable_dispatch_service.py \
  tests/integration/test_outbox_delivery.py \
  tests/integration/test_account_deactivation_concurrency.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/workers/outbox/_livekit_delivery.py \
  app/workers/outbox/customer_dispatch.py \
  app/workers/outbox/verification_dispatch.py \
  tests/workers/test_livekit_delivery.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/workers/outbox/_livekit_delivery.py \
  app/workers/outbox/customer_dispatch.py \
  app/workers/outbox/verification_dispatch.py
```

Expected: both domains retain all existing edge-case coverage and use the one common provider primitive.

- [ ] **Step 6: Commit the second consumer**

```bash
git add apps/api/app/workers/outbox/verification_dispatch.py \
  apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py \
  apps/api/tests/workers/test_livekit_delivery.py
git commit -m "refactor(api): migrate verification livekit delivery"
```

### Task 11: Run final verification and record implementation evidence

**Files:**
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`
- Modify production/test files only if a final gate exposes a defect already within this plan's scope.

**Interfaces:**
- Produces: complete verification evidence and an updated durable Issue 5 status.
- Preserves: Issues 1/6/8/11/12/14/16/18 and every previously recorded accepted risk or deferred direction.

- [ ] **Step 1: Prove final package ownership and forbidden scope**

Run from the repository root:

```bash
diff -u \
  <(printf '%s\n' \
  apps/api/app/workers/outbox/__init__.py \
  apps/api/app/workers/outbox/_account_lifecycle.py \
  apps/api/app/workers/outbox/_livekit_delivery.py \
  apps/api/app/workers/outbox/account_deactivation.py \
  apps/api/app/workers/outbox/customer_dispatch.py \
  apps/api/app/workers/outbox/delivery.py \
  apps/api/app/workers/outbox/failures.py \
  apps/api/app/workers/outbox/phone.py \
  apps/api/app/workers/outbox/phone_provisioning.py \
  apps/api/app/workers/outbox/post_call.py \
  apps/api/app/workers/outbox/provider_cleanup.py \
  apps/api/app/workers/outbox/recording_reconciliation.py \
  apps/api/app/workers/outbox/registry.py \
  apps/api/app/workers/outbox/verification_dispatch.py | sort) \
  <(find apps/api/app/workers/outbox -maxdepth 1 -type f -name '*.py' -print | sort)
! rg -n 'app\.workers\.jobs\.(outbox_topics|outbox_delivery|account_deactivation|provider_cleanup|phone_provisioning|recording_reconciliation|summary)' apps/api/app apps/api/tests --glob '*.py'
! rg -n 'phone_provisioning_job|summary_job' apps/api/app apps/api/tests --glob '*.py'
test ! -s apps/api/app/workers/outbox/__init__.py
git diff --check 1579fd0..HEAD
```

Expected: the exact approved package file list matches, no legacy path/name remains, the initializer is empty, and the complete post-design diff has no whitespace errors.

- [ ] **Step 2: Run the final CI-equivalent API gate with disposable dependencies**

First require both exact disposable names to be unused:

```bash
docker ps -aq --filter 'name=^/presvo-issue5a-postgres$'
docker ps -aq --filter 'name=^/presvo-issue5a-redis$'
```

Expected: neither command prints a container ID. If a name exists, choose a different task-specific exact name throughout the following block; never delete an existing container to claim the name.

Run the final gate in one shell:

```bash
set -eu

cleanup_presvo_issue5a_dependencies() {
  docker stop presvo-issue5a-postgres presvo-issue5a-redis >/dev/null 2>&1 || true
}
trap cleanup_presvo_issue5a_dependencies EXIT HUP INT TERM

docker run --rm -d \
  --name presvo-issue5a-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=ai_call_test \
  -p 127.0.0.1:55432:5432 \
  postgres:17.8-alpine
docker run --rm -d \
  --name presvo-issue5a-redis \
  -p 127.0.0.1:56379:6379 \
  redis:7.4.7-alpine

until docker exec presvo-issue5a-postgres pg_isready -U postgres -d ai_call_test >/dev/null 2>&1; do
  sleep 1
done
until docker exec presvo-issue5a-redis redis-cli ping | rg -q '^PONG$'; do
  sleep 1
done

cd apps/api
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call_test
export TEST_REDIS_URL=redis://127.0.0.1:56379/0
export UV_CACHE_DIR=/tmp/uv-cache

uv lock --check
uv run --frozen --no-sync ruff check app tests
uv run --frozen --no-sync mypy app
uv run --frozen --no-sync python -m pytest -q \
  --cov=app \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
uv run --frozen --no-sync python ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

Expected: every command exits zero. Compare `coverage.json` totals with the pre-change reference of 90.19% line and 77.81% branch; investigate any material drop even when the repository's lower round-down ratchet still passes.

- [ ] **Step 3: Review the complete implementation diff**

Run from the repository root against the immutable approved-design commit:

```bash
git status --short
git diff --stat 1579fd0..HEAD
git diff --name-status 1579fd0..HEAD
git diff --check 1579fd0..HEAD
git log --oneline 1579fd0..HEAD
```

Expected: only the approved API outbox code/tests and durable review documentation changed. The protected frontend path remains untouched.

- [ ] **Step 4: Update the durable review ledger after all gates pass**

In `docs/engineering/2026-07-30-agent-api-review-decisions.md`:

- change Issue 5's ledger status from `Accepted` to `Accepted; implemented`;
- add links to the approved design and this implementation plan in Issue 5's section;
- state that the facade-free worker outbox package, explicit registry, dead summary deletion, provisioning rename, and shared LiveKit provider primitive are complete;
- state that lock, Ruff, mypy, complete API tests, PostgreSQL/Redis integration coverage, and line/branch ratchets passed; and
- update the closing status so Issues 6A and 8A remain next while realtime Issues 1A/14A and the accepted 11C/12C risks remain unchanged.

Use this evidence paragraph without inventing scale or deployment claims:

```markdown
Implementation completed through the
[Outbox Worker Module Decomposition Design](../superpowers/specs/2026-08-05-outbox-worker-module-decomposition-design.md)
and
[Implementation Plan](../superpowers/plans/2026-08-05-outbox-worker-module-decomposition.md).
The facade-free `app.workers.outbox` package, explicit complete registry,
dead summary-path deletion, provisioning-operation rename, and shared typed
LiveKit delivery primitive passed the repository lock, Ruff, mypy, complete API,
PostgreSQL/Redis integration, and line/branch coverage-ratchet gates. No
database schema/data, queue, environment, provider, deployment, recording, or
realtime behavior changed.
```

- [ ] **Step 5: Verify documentation and commit final evidence**

Run:

```bash
git diff --check
rg -n '^\| 5 \|' docs/engineering/2026-07-30-agent-api-review-decisions.md
rg -n 'Issues 5, 6, and 8 remain|Issues 5,6,8 remain' docs/engineering/2026-07-30-agent-api-review-decisions.md
```

Expected: diff check exits zero; Issue 5 says implemented; the final search for stale “Issues 5, 6, and 8 remain” wording exits one with no matches.

Commit:

```bash
git add docs/engineering/2026-07-30-agent-api-review-decisions.md
git commit -m "docs(api): record outbox decomposition evidence"
```

- [ ] **Step 6: Confirm the final worktree is clean except protected pre-existing paths**

Run:

```bash
git status --short --branch
docker ps -a --filter 'name=^/presvo-issue5a-postgres$' --filter 'name=^/presvo-issue5a-redis$'
```

Expected: the implementation worktree has no uncommitted scoped changes; no Issue 5A disposable container remains. Do not inspect, add, delete, or commit the protected `Presvo_frontend/` path if it appears in the shared root worktree.
