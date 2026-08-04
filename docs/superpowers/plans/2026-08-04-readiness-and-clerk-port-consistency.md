# Readiness and Clerk Port Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make activation-enabled number readiness one canonical fail-closed fact and make every default local Clerk origin follow `WEB_PORT` without weakening explicit override behavior.

**Architecture:** A new pure service helper evaluates the exact provisioning-to-phone invariant once. `CustomerReadinessSnapshot` carries that fact to both runtime policy and activation response construction; activation-disabled mode keeps its intentional historical compatibility rule. Development Compose uses supported nested interpolation so `WEB_PORT` owns the standard local origins while `CLERK_AUTHORIZED_PARTIES` remains the exact explicit security override.

**Tech Stack:** Python 3.13, FastAPI service/domain modules, SQLAlchemy models, Pydantic response models, pytest, Docker Compose configuration rendering, Ruff, mypy.

## Global Constraints

- Follow strict red-green-refactor. No production behavior change may be written before a covering test has failed for the expected reason.
- The canonical predicate is exactly `number_is_provisioned(*, provisioning: PhoneNumberProvisioning | None, phone_number: PhoneNumber | None) -> bool`.
- The predicate is true only for a `succeeded` provisioning row linked to the exact assigned phone whose provider identifier is nonblank after trimming.
- Activation-required readiness uses the canonical fact and returns the bounded blocker value `number_not_provisioned` when it is false.
- Activation-disabled readiness retains the historical phone-presence/provider-ID routing rule.
- No migration, database repair, direct provider mutation, Telnyx change, LiveKit change, Clerk API call, recording change, or realtime change is allowed.
- No `.env` file may be read or printed. Tests use disposable literal values only.
- `CLERK_AUTHORIZED_PARTIES` remains an exact explicit override; no origin normalization or allowlist widening is allowed.
- The two retained Telnyx/voice overrides and the live local stack must not be removed or recreated as part of these tasks.
- Preserve the untracked `apps/api/.venv` symlink.
- Do not inspect or modify `Presvo_frontend/` or `.worktrees/shadcn-activation-preview`.

---

### Task 1: Canonical activation-mode number readiness

**Files:**
- Create: `apps/api/app/services/number_provisioning_facts.py`
- Modify: `apps/api/app/services/customer_readiness_policy.py:13-176`
- Modify: `apps/api/app/services/customer_readiness_service.py:1-230`
- Modify: `apps/api/app/services/activation_snapshot_service.py:110-226`
- Create: `apps/api/tests/services/test_number_provisioning_facts.py`
- Modify: `apps/api/tests/services/test_customer_readiness_policy.py:22-175`
- Modify: `apps/api/tests/activation/test_activation_snapshot_service.py:246-298`

**Interfaces:**
- Consumes: already-loaded `PhoneNumberProvisioning | None` and `PhoneNumber | None`; no repository or provider I/O.
- Produces: `number_is_provisioned(*, provisioning, phone_number) -> bool`, `CustomerReadinessSnapshot.number_provisioned: bool`, and `ReadinessBlocker.NUMBER_NOT_PROVISIONED = "number_not_provisioned"`.
- Preserves: activation-disabled policy ignores the new blocker and continues using `phone_present` plus `phone_provider_id_present` to decide whether the legacy phone is usable.

- [ ] **Step 1: Write the pure invariant matrix before the helper exists**

Create `apps/api/tests/services/test_number_provisioning_facts.py` with real model objects and literal expectations. Include one valid case and these invalid cases: missing provisioning, `running`, `failed`, missing phone, mismatched `phone_number_id`, missing provider identifier, empty provider identifier, and whitespace-only provider identifier.

Use this test-local fixture builder to construct complete SQLAlchemy model
objects; do not put test helpers on production classes. The assertion table
uses literal booleans:

```python
from uuid import uuid4

import pytest

from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.services.number_provisioning_facts import number_is_provisioned


def build_case(
    case: str,
) -> tuple[PhoneNumberProvisioning | None, PhoneNumber | None]:
    user_id = uuid4()
    phone_number = PhoneNumber(
        user_id=user_id,
        e164="+33999000000",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn_readiness",
        provider_connection_name="app-disabled",
        is_active=False,
    )
    provisioning = PhoneNumberProvisioning(
        user_id=user_id,
        phone_number_id=phone_number.id,
        target_country_code="FR",
        status="succeeded",
        attempt_count=1,
        can_retry=False,
    )

    if case == "missing_provisioning":
        return None, phone_number
    if case == "running":
        provisioning.status = "running"
    elif case == "failed":
        provisioning.status = "failed"
    elif case == "missing_phone":
        return provisioning, None
    elif case == "mismatched_phone":
        provisioning.phone_number_id = uuid4()
    elif case == "missing_provider_id":
        phone_number.provider_number_id = None
    elif case == "empty_provider_id":
        phone_number.provider_number_id = ""
    elif case == "whitespace_provider_id":
        phone_number.provider_number_id = "   "
    elif case != "valid":
        raise AssertionError(f"unknown test case: {case}")
    return provisioning, phone_number


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("valid", True),
        ("missing_provisioning", False),
        ("running", False),
        ("failed", False),
        ("missing_phone", False),
        ("mismatched_phone", False),
        ("missing_provider_id", False),
        ("empty_provider_id", False),
        ("whitespace_provider_id", False),
    ],
)
def test_number_is_provisioned_requires_one_exact_completed_assignment(
    case: str,
    expected: bool,
) -> None:
    provisioning, phone_number = build_case(case)

    assert number_is_provisioned(
        provisioning=provisioning,
        phone_number=phone_number,
    ) is expected
```

- [ ] **Step 2: Run the helper matrix and verify RED**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest tests/services/test_number_provisioning_facts.py -q
```

Expected: collection fails because `app.services.number_provisioning_facts` does not exist. Add only the module and typed function shell returning `False`, rerun, and record the required assertion failure for the literal `valid` case. This second run is the behavioral RED gate; do not implement the predicate before observing it.

- [ ] **Step 3: Write the cross-consumer contradiction tests while production consumers are unchanged**

In `test_activation_snapshot_service.py`, extend the exact-link mismatch case and the missing/blank identity cases to assert the response is internally consistent:

```python
assert snapshot.number.provider_ready is False
assert snapshot.runtime_readiness.can_route is False
assert "number_not_provisioned" in snapshot.runtime_readiness.blockers
```

Add missing-provisioning and non-succeeded-provisioning cases when not already covered by an equivalent observable assertion. In `test_customer_readiness_policy.py`, add `number_provisioned=True` to `ready_snapshot`, then add both policy-boundary tests:

```python
def test_activation_required_number_fact_blocks_enable_routing_and_dispatch() -> None:
    result = evaluate(
        number_provisioned=False,
        activation_required=True,
        business_profile_complete=True,
        profile_projection_current=True,
        forwarding_verified=True,
        go_live_approved=True,
        go_live_activated=True,
    )

    assert ReadinessBlocker.NUMBER_NOT_PROVISIONED in result.blockers
    assert result.can_activate is False
    assert result.should_enable_phone is False
    assert result.can_route is False
    assert result.can_dispatch(called_number_matches=True) is False
    assert result.stage is CustomerReadinessStage.NUMBER_PROVISIONING_FAILED


def test_activation_disabled_mode_keeps_legacy_phone_compatibility() -> None:
    result = evaluate(number_provisioned=False, activation_required=False)

    assert ReadinessBlocker.NUMBER_NOT_PROVISIONED not in result.blockers
    assert result.can_activate is True
    assert result.can_route is True
```

- [ ] **Step 4: Run the consumer tests and verify the existing contradiction is RED**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/services/test_customer_readiness_policy.py \
  tests/activation/test_activation_snapshot_service.py \
  -q
```

Expected: failures show that `CustomerReadinessSnapshot` lacks the new fact/blocker and that the mismatched activation response still reports `runtime_readiness.can_route=True`. Fix test construction mistakes until failures are only missing intended behavior.

- [ ] **Step 5: Implement the minimal pure invariant**

Implement `apps/api/app/services/number_provisioning_facts.py` exactly as a pure query:

```python
from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning


def number_is_provisioned(
    *,
    provisioning: PhoneNumberProvisioning | None,
    phone_number: PhoneNumber | None,
) -> bool:
    return bool(
        provisioning is not None
        and provisioning.status == "succeeded"
        and phone_number is not None
        and provisioning.phone_number_id == phone_number.id
        and phone_number.provider_number_id is not None
        and phone_number.provider_number_id.strip()
    )
```

Do not add a class, protocol, repository, cache, or exception.

- [ ] **Step 6: Carry the fact through readiness policy explicitly**

Add `number_provisioned: bool` immediately after `provisioning_status` on `CustomerReadinessSnapshot`. In `build_customer_readiness_snapshot`, call `number_is_provisioned` once with the already-loaded models.

Add this exact enum member and include it in `_ACTIVATION_BLOCKERS`:

```python
NUMBER_NOT_PROVISIONED = "number_not_provisioned"
```

Inside `_evaluate_activation`, add the blocker only after confirming `activation_required` is true. In `_derive_stage`, choose the usable-number fact explicitly:

```python
usable_phone = (
    snapshot.number_provisioned
    if snapshot.activation_required
    else snapshot.phone_present and snapshot.phone_provider_id_present
)
```

For activation-required `number_provisioned=False`, retain the existing stage vocabulary: `failed` or `succeeded` provisioning with an unusable assignment maps to `NUMBER_PROVISIONING_FAILED`; absent/running provisioning maps to `NUMBER_PROVISIONING`. Do not change activation-disabled stage behavior.

- [ ] **Step 7: Remove the duplicated activation predicate**

In `ActivationSnapshotService.get`, name the built snapshot before evaluation:

```python
readiness_snapshot = build_customer_readiness_snapshot(
    user=user,
    subscription=subscription,
    balance=balance,
    phone_number=phone,
    provisioning=provisioning,
    agent_config=agent_config,
    activation_required=True,
    business_profile_complete=(
        activation_prerequisites.business_profile_complete
    ),
    profile_projection_current=(
        activation_prerequisites.profile_projection_current
    ),
    forwarding_verified=activation_prerequisites.forwarding_verified,
    go_live_approved=activation_prerequisites.go_live_approved,
    go_live_activated=activation_prerequisites.go_live_activated,
)
readiness = CustomerReadinessPolicy.evaluate(readiness_snapshot, now=evaluation_time)
number_provisioned = readiness_snapshot.number_provisioned
```

Use that one value for `ActivationFacts.number_provisioned` and `ActivationNumberResponse.provider_ready`. Delete the local boolean expression; do not leave a second implementation or a fallback inference from `assigned_e164`.

- [ ] **Step 8: Run GREEN verification for Task 1**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/services/test_number_provisioning_facts.py \
  tests/services/test_customer_readiness_policy.py \
  tests/activation/test_activation_snapshot_service.py \
  -q
.venv/bin/ruff check \
  app/services/number_provisioning_facts.py \
  app/services/customer_readiness_policy.py \
  app/services/customer_readiness_service.py \
  app/services/activation_snapshot_service.py \
  tests/services/test_number_provisioning_facts.py \
  tests/services/test_customer_readiness_policy.py \
  tests/activation/test_activation_snapshot_service.py
.venv/bin/mypy app
```

Expected: all focused tests pass, Ruff reports `All checks passed!`, and mypy reports no issues.

- [ ] **Step 9: Perform the mutation check and self-review**

Temporarily make one realistic mutation at a time and restore immediately:

1. remove the exact phone-ID equality check;
2. omit `NUMBER_NOT_PROVISIONED` from `_ACTIVATION_BLOCKERS`;
3. apply the blocker when `activation_required=False`.

For each mutation, rerun the narrow test that must fail and record the failure in the task report. Restore production code, rerun the complete focused command from Step 8, inspect `git diff`, and confirm no mutation remains.

- [ ] **Step 10: Commit Task 1**

```bash
git add \
  apps/api/app/services/number_provisioning_facts.py \
  apps/api/app/services/customer_readiness_policy.py \
  apps/api/app/services/customer_readiness_service.py \
  apps/api/app/services/activation_snapshot_service.py \
  apps/api/tests/services/test_number_provisioning_facts.py \
  apps/api/tests/services/test_customer_readiness_policy.py \
  apps/api/tests/activation/test_activation_snapshot_service.py
git commit -m "fix(api): canonicalize activation number readiness"
```

---

### Task 2: Derive default Clerk authorized parties from `WEB_PORT`

**Files:**
- Modify: `compose.dev.yaml:102-105`
- Modify: `apps/api/tests/test_deployment_readiness.py:74-126,779-809`
- Modify: `docs/architecture/local-self-service-activation.md:126-140`

**Interfaces:**
- Consumes: `WEB_PORT`, defaulting to literal `3000`; optional exact `CLERK_AUTHORIZED_PARTIES` override.
- Produces: default `http://127.0.0.1:<WEB_PORT>,http://localhost:<WEB_PORT>` for API Clerk verification while preserving the explicit override byte-for-byte.
- Preserves: production Compose, web container port target `3000`, default behavior at port 3000, CORS behavior, and every non-authentication service environment.

- [ ] **Step 1: Write the custom-port behavior test**

Add a test that renders the real Compose model rather than grepping source text:

```python
def test_local_compose_custom_web_port_updates_every_default_local_origin() -> None:
    document = load_local_compose_yaml({"WEB_PORT": "3300"})
    api_environment = resolved_service_environment(document, "api")
    web_environment = resolved_service_environment(document, "web")

    expected_origins = "http://127.0.0.1:3300,http://localhost:3300"
    assert api_environment["CORS_ALLOWED_ORIGINS"] == expected_origins
    assert api_environment["CLERK_AUTHORIZED_PARTIES"] == expected_origins
    assert web_environment["NEXT_PUBLIC_APP_URL"] == "http://127.0.0.1:3300"
```

- [ ] **Step 2: Run the custom-port test and verify RED**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/test_deployment_readiness.py::test_local_compose_custom_web_port_updates_every_default_local_origin \
  -q
```

Expected: one assertion fails because `CLERK_AUTHORIZED_PARTIES` still resolves to both port-3000 origins while CORS and the web URL resolve to port 3300.

- [ ] **Step 3: Add explicit-override characterization coverage**

Add a separate test using literal values:

```python
def test_local_compose_explicit_clerk_authorized_parties_override_wins() -> None:
    document = load_local_compose_yaml(
        {
            "WEB_PORT": "3300",
            "CLERK_AUTHORIZED_PARTIES": "https://explicit.example",
        }
    )
    api_environment = resolved_service_environment(document, "api")

    assert api_environment["CLERK_AUTHORIZED_PARTIES"] == (
        "https://explicit.example"
    )
    assert api_environment["CORS_ALLOWED_ORIGINS"] == (
        "http://127.0.0.1:3300,http://localhost:3300"
    )
```

Run this test before changing Compose and record that existing explicit override precedence is already green; it is characterization coverage for behavior the fix must preserve, not evidence for the RED gate.

- [ ] **Step 4: Implement the nested default**

Change only the development API environment value to:

```yaml
CLERK_AUTHORIZED_PARTIES: "${CLERK_AUTHORIZED_PARTIES:-http://127.0.0.1:${WEB_PORT:-3000},http://localhost:${WEB_PORT:-3000}}"
```

Do not change `CORS_ALLOWED_ORIGINS`, production Compose, or Clerk runtime validation.

- [ ] **Step 5: Document the standard and override paths**

Immediately after the standard local Compose command in `local-self-service-activation.md`, state:

```text
`WEB_PORT` changes the published web port, application URL, CORS origins, and
the two default local Clerk authorized parties together. Set
`CLERK_AUTHORIZED_PARTIES` explicitly only when the token's exact authorized
party is intentionally different from those standard loopback origins.
```

Keep the normal port-3000 activation URL example unchanged.

- [ ] **Step 6: Run GREEN verification for Task 2**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/test_deployment_readiness.py::test_local_compose_defaults_interactive_services_to_clerk \
  tests/test_deployment_readiness.py::test_local_compose_custom_web_port_updates_every_default_local_origin \
  tests/test_deployment_readiness.py::test_local_compose_explicit_clerk_authorized_parties_override_wins \
  tests/test_deployment_readiness.py::test_local_compose_accepts_explicit_synthetic_auth_for_disposable_tests \
  -q
.venv/bin/ruff check tests/test_deployment_readiness.py
```

Expected: four tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 7: Perform the mutation check and self-review**

Temporarily restore the fixed port-3000 default and rerun the custom-port test; it must fail on the exact Clerk origins. Restore the nested default. Then temporarily remove explicit override precedence by hardcoding the derived origins and rerun the override test; it must fail. Restore the implementation, rerun Step 6, inspect `git diff`, and confirm no mutation remains.

- [ ] **Step 8: Commit Task 2**

```bash
git add \
  compose.dev.yaml \
  apps/api/tests/test_deployment_readiness.py \
  docs/architecture/local-self-service-activation.md
git commit -m "fix(auth): align Clerk origins with local web port"
```

---

## Final verification and review

After both task reviewers approve:

1. Run the complete API suite outside the restricted event-loop sandbox:

   ```bash
   cd apps/api
   .venv/bin/python -m pytest -q
   .venv/bin/ruff check app tests
   .venv/bin/mypy app
   ```

2. Run the PostgreSQL-only first-bootstrap concurrency test against a uniquely
   named disposable database, verify the database is absent before creation,
   drop that exact database afterward, and verify it is absent. Never use the
   live `ai_call` database.

3. Run unchanged cross-application gates:

   ```bash
   cd apps/web
   npm run test:ci
   npm run typecheck
   npm run lint

   cd ../agent
   .venv/bin/python -m pytest -q
   .venv/bin/ruff check agent tests
   .venv/bin/mypy agent
   ```

4. Run from the worktree root:

   ```bash
   git diff --check main...HEAD
   git status --short --branch
   ```

   The only allowed untracked repository entry is `apps/api/.venv`.

5. Read-only runtime verification must prove:
   - every existing API, worker, agent, Postgres, Redis, and MinIO container ID
     is preserved;
   - all restart counts remain zero;
   - API and worker still classify `TELEPHONY_MODE` as Telnyx using boolean-only
     checks;
   - web remains healthy with Clerk settings present using presence-only checks;
   - no provider, database, activation, call, or recording mutation occurred.

6. Generate the final review package from the branch merge-base through HEAD and
   dispatch the broad final reviewer. Resolve any Critical or Important finding
   through the single allowed final fix wave and scoped re-review.

7. Present deferred documentation Issues 33–35 with numbered/lettered options
   before editing them. Do not fold them silently into either implementation
   task.
