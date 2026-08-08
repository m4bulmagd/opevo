# Terminal Readiness Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present terminal invalid number assignments as explicit non-retryable failures, restore provisioning-free legacy outbox coverage, publish the changed readiness semantics as `runtime-v5`, reject unassigned exact-link identities, and make explicit local-auth token validation fail closed on surrounding whitespace.

**Architecture:** `ActivationPolicy` keeps the existing failure stage but distinguishes a provider failure from a terminal succeeded-yet-invalid assignment with one stable blocker. The number milestone renders that blocker explicitly. The canonical number predicate keeps its existing persisted-data contract while explicitly rejecting a null relationship identity. Test fixtures expose activation assumptions through a boolean opt-in, the existing readiness response carries the new semantic version, and API/web local-auth boundaries reject padded secrets without normalizing them.

**Tech Stack:** Python 3.13, FastAPI service policy, SQLAlchemy test models, pytest, Next.js 16, React 19, TypeScript, Vitest, Testing Library, Biome, Ruff, mypy.

## Global Constraints

- Follow strict red-green-refactor. Do not edit production behavior before the named test fails for the expected reason.
- Reuse `ActivationStage.PROVISIONING_FAILED`; do not add another activation stage.
- The stable terminal-assignment blocker is exactly `number_assignment_inconsistent`.
- Evaluate the inconsistency only after the existing provisioning-consent gate.
- `failed` provisioning keeps its existing provider-failure behavior; absent, `queued`, and `running` states remain refreshable provisioning.
- A consented `succeeded` provisioning with `number_provisioned=False` is non-retryable, has no next action, and must not display the provisioning spinner or business-profile correction guidance.
- `_seed_dispatch` defaults to no provisioning row. Only activation-enabled tests with an otherwise ready customer opt in through `with_provisioning=True`.
- The readiness policy version is exactly `runtime-v5`; web code continues treating it as opaque data.
- Do not change the canonical `number_is_provisioned` predicate except for the approved 41A non-null link guard. Do not change the database schema, persisted provisioning status, provider state, or live account state.
- `LOCAL_AUTH_TOKEN` remains exact secret material. Reject leading/trailing whitespace in API runtime validation and the server-only web session boundary; do not trim, expose, log, or echo it.
- Do not add a provider retry, number order, compensation, repair command, support subsystem, route, dependency, cache, or migration.
- Do not read or print `.env` files, credentials, provider identifiers, real phone numbers, transcripts, recordings, or private runtime overrides.
- Do not inspect or modify `Opevo_frontend/` or `.worktrees/shadcn-activation-preview`.
- Preserve the untracked `apps/api/.venv` symlink and the retained `/tmp` voice/Telnyx/Clerk overrides.
- Do not recreate API, worker, agent, web, PostgreSQL, Redis, or MinIO services.
- Deferred documentation Issues 33–35, realtime, and recording remain outside this plan.

---

### Task 1: Classify terminal invalid assignments in activation responses

**Files:**
- Modify: `apps/api/app/services/activation_policy.py:42-81`
- Modify: `apps/api/tests/activation/test_activation_policy.py:1-121`
- Modify: `apps/api/tests/activation/test_activation_snapshot_service.py:221-323`

**Interfaces:**
- Consumes: `ActivationFacts.provisioning_consented`, `ActivationFacts.provisioning_status`, and `ActivationFacts.number_provisioned` as built by `ActivationSnapshotService` from the existing canonical readiness snapshot.
- Produces: `ActivationDecision(stage=ActivationStage.PROVISIONING_FAILED, next_action=None, blockers=("number_assignment_inconsistent",))` for a consented terminal invalid assignment.
- Preserves: the no-consent gate, the existing `failed` branch, pending provisioning, exact succeeded assignments, and runtime readiness blockers.

- [ ] **Step 1: Correct the pending-policy fixture and add the terminal RED case**

In `test_activation_policy.py`, make the existing pending test describe a real pending row and add a separate terminal assertion:

```python
def test_missing_assigned_number_remains_in_provisioning() -> None:
    decision = ActivationPolicy.evaluate(
        replace(
            ready_facts(),
            provisioning_status="running",
            number_provisioned=False,
        )
    )

    assert decision.stage is ActivationStage.PROVISIONING
    assert decision.blockers == ("number_not_ready",)


def test_succeeded_provisioning_with_invalid_assignment_is_terminal() -> None:
    decision = ActivationPolicy.evaluate(
        replace(
            ready_facts(),
            provisioning_status="succeeded",
            number_provisioned=False,
        )
    )

    assert decision.stage is ActivationStage.PROVISIONING_FAILED
    assert decision.next_action is None
    assert decision.blockers == ("number_assignment_inconsistent",)
```

- [ ] **Step 2: Add the cross-consumer snapshot RED case**

In `test_activation_snapshot_service.py`, leave provisioning consent present and corrupt only the exact assignment link:

```python
@pytest.mark.anyio
async def test_consented_terminal_assignment_inconsistency_fails_explicitly() -> None:
    records = list(build_records())
    records[4].phone_number_id = uuid4()
    service, _repositories = build_service(records=tuple(records))

    snapshot = await service.get(records[0].id, now=NOW)

    assert snapshot.stage is ActivationStage.PROVISIONING_FAILED
    assert snapshot.next_action is None
    assert snapshot.blockers == ["number_assignment_inconsistent"]
    assert snapshot.number.provisioning_status == "succeeded"
    assert snapshot.number.provider_ready is False
    assert snapshot.number.can_retry is False
    assert snapshot.runtime_readiness.stage == "number_provisioning_failed"
    assert snapshot.runtime_readiness.can_route is False
    assert "number_not_provisioned" in snapshot.runtime_readiness.blockers
```

Do not remove or weaken the existing no-consent mismatch tests; they continue proving the consent gate wins first.

- [ ] **Step 3: Run the two RED files**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/activation/test_activation_policy.py \
  tests/activation/test_activation_snapshot_service.py \
  -q
```

Expected: the two new terminal assertions fail because the policy returns `ActivationStage.PROVISIONING` with `number_not_ready`. All pre-existing cases pass.

- [ ] **Step 4: Implement the explicit terminal branch**

In `ActivationPolicy.evaluate`, keep the branch after the existing `failed` condition and before the general false-number condition:

```python
        if (
            facts.provisioning_status == "succeeded"
            and not facts.number_provisioned
        ):
            return ActivationDecision(
                stage=ActivationStage.PROVISIONING_FAILED,
                completed_milestones=completed_milestones,
                next_action=None,
                blockers=("number_assignment_inconsistent",),
            )
```

Do not normalize the response provisioning status to `failed`; it remains the truthful stored value `succeeded`.

- [ ] **Step 5: Run Task 1 GREEN and static checks**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/activation/test_activation_policy.py \
  tests/activation/test_activation_snapshot_service.py \
  -q
.venv/bin/ruff check \
  app/services/activation_policy.py \
  tests/activation/test_activation_policy.py \
  tests/activation/test_activation_snapshot_service.py
.venv/bin/mypy app/services/activation_policy.py
```

Expected: all commands exit zero.

- [ ] **Step 6: Prove the terminal branch is mutation-sensitive**

Temporarily change the new `provisioning_status == "succeeded"` comparison to `provisioning_status == "running"`, run the two new test names, and require failures showing terminal inconsistency returns the pending stage. Restore the exact comparison and rerun Task 1 GREEN.

- [ ] **Step 7: Commit Task 1**

```bash
git add \
  apps/api/app/services/activation_policy.py \
  apps/api/tests/activation/test_activation_policy.py \
  apps/api/tests/activation/test_activation_snapshot_service.py
git commit -m "fix(api): classify invalid number assignments"
```

---

### Task 2: Render explicit non-retryable assignment guidance

**Files:**
- Modify: `apps/web/src/app/(activation)/activate/_components/number/provisioning-status.tsx:33-134`
- Modify: `apps/web/tests/app/number-milestone.test.tsx:235-390`

**Interfaces:**
- Consumes: `ActivationSnapshot.stage == "provisioning_failed"`, `snapshot.blockers`, and `snapshot.number.can_retry` from Task 1.
- Produces: a safe `number_assignment_inconsistent` alert with no spinner, retry button, or business-profile correction link.
- Preserves: ready-number rendering, pending spinner, provider retry, retry failure feedback, and ordinary terminal profile-correction guidance.

- [ ] **Step 1: Write the explicit UI RED case**

Add this case next to the existing provisioning-failure tests in `number-milestone.test.tsx`:

```tsx
it("explains a terminal assignment inconsistency without retry or profile correction", () => {
  render(
    <NumberMilestone
      localBilling={false}
      snapshot={snapshot({
        stage: "provisioning_failed",
        blockers: ["number_assignment_inconsistent", "private-provider-detail"],
        number: {
          ...snapshot().number,
          assigned_e164: "+33187654321",
          provider_ready: false,
          provisioning_status: "succeeded",
          can_retry: false,
        },
      })}
    />,
  );

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(/couldn't verify your assigned number/i);
  expect(screen.getByText(/Reference: number_assignment_inconsistent/i)).toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Retry provisioning/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Correct business profile/i })).not.toBeInTheDocument();
  expect(screen.queryByText(/private-provider-detail/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the UI RED case**

Run from `apps/web`:

```bash
npm test -- --run tests/app/number-milestone.test.tsx \
  -t "explains a terminal assignment inconsistency"
```

Expected: FAIL because the existing non-retryable branch renders business-profile correction guidance.

- [ ] **Step 3: Implement the inconsistency branch before generic failure handling**

In `ProvisioningStatus`, after the active `provisioning` branch and before `retryable`, add:

```tsx
  const assignmentInconsistent =
    snapshot.stage === "provisioning_failed" && snapshot.blockers.includes("number_assignment_inconsistent");
  if (assignmentInconsistent) {
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="destructive">
          <AlertTitle>We couldn't verify your assigned number</AlertTitle>
          <AlertDescription>
            Opevo recorded provisioning as complete, but the assigned number no longer matches the completed request.
            Refresh once. If this continues, contact support and share the reference below.
          </AlertDescription>
        </Alert>
        <p className="font-mono text-muted-foreground text-xs">
          Reference: number_assignment_inconsistent
        </p>
      </div>
    );
  }
```

Do not render arbitrary blocker strings or the assigned number in this failure branch.

- [ ] **Step 4: Run Task 2 GREEN and web static checks**

Run from `apps/web`:

```bash
npm test -- --run tests/app/number-milestone.test.tsx
npm run typecheck
npm run lint
```

Expected: all commands exit zero.

- [ ] **Step 5: Prove the explicit branch is mutation-sensitive**

Temporarily replace the exact blocker value in `includes(...)` with `number_provisioning_failed`. Run the new test and require it to fail on its safe-copy and no-profile-link assertions. Restore `number_assignment_inconsistent` and rerun Task 2 GREEN.

- [ ] **Step 6: Commit Task 2**

```bash
git add \
  'apps/web/src/app/(activation)/activate/_components/number/provisioning-status.tsx' \
  apps/web/tests/app/number-milestone.test.tsx
git commit -m "fix(web): explain invalid number assignments"
```

---

### Task 3: Restore provisioning-free legacy outbox semantics

**Files:**
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py:1-680`

**Interfaces:**
- Consumes: the existing `_seed_dispatch` setup and module-level `ACTIVATION_FLOW_ENABLED=false` default.
- Produces: `_seed_dispatch(..., with_provisioning: bool = False)` and explicit activation-enabled call sites.
- Preserves: every call, subscription, usage, outbox-event, provider-contract, and activation-enabled dispatch assertion.

- [ ] **Step 1: Add the legacy zero-row RED assertion**

In `test_dispatch_handler_creates_and_persists_provider_identity`, immediately after `_seed_dispatch` returns, add:

```python
    assert await db_session.scalar(select(PhoneNumberProvisioning)) is None
```

- [ ] **Step 2: Run the parametrized legacy RED test**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/workers/test_livekit_dispatch_outbox.py::test_dispatch_handler_creates_and_persists_provider_identity \
  -q
```

Expected: all three parameter cases fail because the shared fixture currently inserts one provisioning row.

- [ ] **Step 3: Parameterize the shared seed without duplicating setup**

Change the signature:

```python
async def _seed_dispatch(
    db_session,
    *,
    owner_name: str | None = None,
    business_display_name: str | None = None,
    user_id: UUID | None = None,
    config_id: UUID | None = None,
    call_id: UUID | None = None,
    with_provisioning: bool = False,
):
```

Wrap only the provisioning insertion:

```python
    if with_provisioning:
        db_session.add(
            PhoneNumberProvisioning(
                user_id=user.id,
                phone_number_id=phone.id,
                target_country_code="FR",
                status="succeeded",
                attempt_count=1,
                can_retry=False,
            )
        )
```

Pass `with_provisioning=True` in exactly these two activation-enabled tests:

```python
async def test_activation_flow_missing_business_name_fails_dispatch_closed(...)
async def test_default_guided_projection_serializes_through_dispatch_contract(...)
```

Keep every activation-disabled caller on the default.

- [ ] **Step 4: Run the complete outbox module GREEN and Ruff**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest tests/workers/test_livekit_dispatch_outbox.py -q
.venv/bin/ruff check tests/workers/test_livekit_dispatch_outbox.py
```

Expected: all commands exit zero, including both activation-enabled cases.

- [ ] **Step 5: Prove both fixture boundaries are mutation-sensitive**

First, temporarily set `with_provisioning=True` as the helper default and require the legacy zero-row test to fail. Restore `False`. Second, temporarily remove `with_provisioning=True` from `test_default_guided_projection_serializes_through_dispatch_contract` and require that test to fail with `dispatch_ineligible`. Restore the opt-in and rerun Task 3 GREEN.

- [ ] **Step 6: Commit Task 3**

```bash
git add apps/api/tests/workers/test_livekit_dispatch_outbox.py
git commit -m "test(api): preserve legacy outbox readiness"
```

---

### Task 4: Publish readiness policy `runtime-v5`

**Files:**
- Modify: `apps/api/app/services/customer_readiness_policy.py:94-97`
- Modify: `apps/api/tests/services/test_customer_readiness_policy.py:60-75`
- Modify: `apps/api/tests/services/test_onboarding_service.py:113-134`
- Modify: `apps/api/tests/activation/test_activation_snapshot_service.py:195-215`

**Interfaces:**
- Consumes: the existing `CustomerReadinessPolicy.POLICY_VERSION` response path.
- Produces: exact public value `runtime-v5` in onboarding and activation readiness responses.
- Preserves: response schema, policy evaluation, web parsing, and all persisted data.

- [ ] **Step 1: Change exact contract expectations to create RED**

Change the three existing assertions to:

```python
assert result.policy_version == "runtime-v5"
assert status.policy_version == "runtime-v5"
assert snapshot.runtime_readiness.policy_version == "runtime-v5"
```

- [ ] **Step 2: Run policy-version RED**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/services/test_customer_readiness_policy.py::test_live_snapshot_can_activate_route_and_dispatch \
  tests/services/test_onboarding_service.py::test_get_status_returns_subscription_required_defaults \
  tests/activation/test_activation_snapshot_service.py::test_get_loads_each_authoritative_row_once_and_returns_active_snapshot \
  -q
```

Expected: three failures showing actual `runtime-v4` versus expected `runtime-v5`.

- [ ] **Step 3: Advance the semantic policy constant**

In `CustomerReadinessPolicy`:

```python
    POLICY_VERSION = "runtime-v5"
```

- [ ] **Step 4: Run Task 4 GREEN and scan active code expectations**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/services/test_customer_readiness_policy.py \
  tests/services/test_onboarding_service.py \
  tests/activation/test_activation_snapshot_service.py \
  -q
.venv/bin/ruff check \
  app/services/customer_readiness_policy.py \
  tests/services/test_customer_readiness_policy.py \
  tests/services/test_onboarding_service.py \
  tests/activation/test_activation_snapshot_service.py
.venv/bin/mypy app/services/customer_readiness_policy.py
rg -n 'runtime-v4' app tests
```

Expected: pytest, Ruff, and mypy exit zero. The final `rg` exits one with no matches in active API code/tests; historical documentation is outside this scan.

- [ ] **Step 5: Prove the version contract is mutation-sensitive**

Temporarily restore `POLICY_VERSION = "runtime-v4"`, run the three exact tests from Step 2, and require all three to fail. Restore `runtime-v5` and rerun Task 4 GREEN.

- [ ] **Step 6: Commit Task 4**

```bash
git add \
  apps/api/app/services/customer_readiness_policy.py \
  apps/api/tests/services/test_customer_readiness_policy.py \
  apps/api/tests/services/test_onboarding_service.py \
  apps/api/tests/activation/test_activation_snapshot_service.py
git commit -m "chore(api): advance readiness policy version"
```

---

### Task 5: Complete cross-application and live-runtime verification

**Files:**
- Modify: none unless a verification failure proves a defect owned by Tasks 1–4.

**Interfaces:**
- Consumes: reviewed commits from Tasks 1–4 and the unchanged live local stack.
- Produces: complete test/static evidence, disposable PostgreSQL concurrency evidence, Git integrity evidence, and proof that live service/provider modes were not changed.

- [ ] **Step 1: Run focused cross-boundary API and web gates together**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/activation/test_activation_policy.py \
  tests/activation/test_activation_snapshot_service.py \
  tests/services/test_customer_readiness_policy.py \
  tests/services/test_onboarding_service.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  -q
```

Run from `apps/web`:

```bash
npm test -- --run tests/app/number-milestone.test.tsx
```

- [ ] **Step 2: Run the complete API gates**

Run from `apps/api`:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check app tests
.venv/bin/mypy app
```

Expected: the full suite has no new failure or skip, Ruff exits zero, and mypy exits zero.

- [ ] **Step 3: Run the complete web gates**

Run from `apps/web`:

```bash
npm test -- --run
npm run typecheck
npm run lint
```

Expected: all commands exit zero without weakening Vitest coverage or Biome rules.

- [ ] **Step 4: Run the complete agent gates with the existing locked environment**

Run from `apps/agent`:

```bash
env PYTHONPATH=../../libs/shared:. \
  /home/mo/code/ai/bmad-opevo/apps/agent/.venv/bin/python -m pytest -q
/home/mo/code/ai/bmad-opevo/apps/agent/.venv/bin/ruff check agent tests
/home/mo/code/ai/bmad-opevo/apps/agent/.venv/bin/mypy agent
```

Expected: all commands exit zero. Do not create another worktree-local agent virtual environment.

- [ ] **Step 5: Prove first-bootstrap concurrency against one exact disposable PostgreSQL database**

Use the exact database name `opevo_codex_verify_terminal_readiness_20260804`. First require this query to print `0`; stop without creating or dropping anything if it does not:

```bash
docker exec bmad-opevo-postgres-1 \
  psql -U postgres -d postgres -tAc \
  "SELECT count(*) FROM pg_database WHERE datname = 'opevo_codex_verify_terminal_readiness_20260804'"
```

Create only that database, run the concurrency case, then drop only that database even if pytest fails:

```bash
docker exec bmad-opevo-postgres-1 \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c \
  "CREATE DATABASE opevo_codex_verify_terminal_readiness_20260804"
env TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/opevo_codex_verify_terminal_readiness_20260804 \
  .venv/bin/python -m pytest \
  tests/auth/test_local_auth.py::test_concurrent_first_bootstrap_creates_one_complete_aggregate \
  -q
docker exec bmad-opevo-postgres-1 \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c \
  "DROP DATABASE IF EXISTS opevo_codex_verify_terminal_readiness_20260804 WITH (FORCE)"
```

Repeat the absence query and require `0`. Record test and cleanup results without touching the live `ai_call` database.

- [ ] **Step 6: Verify Git integrity and live service continuity**

Run from the worktree root:

```bash
git diff --check main...HEAD
git status --short --branch
docker inspect --format '{{.Name}} {{.Id}} restarts={{.RestartCount}} status={{.State.Status}}' \
  bmad-opevo-web-1 \
  bmad-opevo-api-1 \
  bmad-opevo-worker-1 \
  bmad-opevo-agent-1 \
  bmad-opevo-postgres-1 \
  bmad-opevo-redis-1 \
  bmad-opevo-minio-1
```

Expected: `git diff --check` is clean; status contains only the preserved `apps/api/.venv`; all exact container IDs match the pre-task baseline and restart counts remain zero.

Verify modes and health without printing secrets:

```bash
docker exec bmad-opevo-api-1 python -c \
  "import os,sys; sys.exit(0 if os.getenv('TELEPHONY_MODE') == 'telnyx' else 1)"
docker exec bmad-opevo-worker-1 python -c \
  "import os,sys; sys.exit(0 if os.getenv('TELEPHONY_MODE') == 'telnyx' else 1)"
docker exec bmad-opevo-web-1 node -e \
  "process.exit(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY ? 0 : 1)"
curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/healthz
curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/
curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/dashboard
```

Expected HTTP codes: `200`, `200`, and `307`. Do not run `docker compose up`, recreate a service, mutate Telnyx, or change the live database.

- [ ] **Step 7: Run final plan-level and whole-branch read-only reviews**

Generate a review package from this plan's committed base through final HEAD. Require reviewers to check 38A/39A/40A against the written design, confirm no semantic test weakening, and distinguish deferred Issues 33–35 from new implementation findings. Resolve any Critical or Important finding with one bounded test-first fix loop and scoped re-review before calling the plan complete.

---

### Task 6: Reject unassigned canonical number links

**Files:**
- Modify: `apps/api/app/services/number_provisioning_facts.py`
- Modify: `apps/api/tests/services/test_number_provisioning_facts.py`
- Modify: `apps/api/tests/activation/test_activation_snapshot_service.py`

**Interfaces:**
- Consumes: the existing provisioning link and assigned phone identity.
- Produces: false when the provisioning link is null, including malformed
  in-memory objects whose phone identity is also unassigned.
- Preserves: every valid persisted exact link and all existing readiness
  vocabulary, stages, schemas, and database constraints.

- [ ] **Step 1: Add the unassigned-identity RED test**

Construct an otherwise successful provisioning and assigned phone without
explicit IDs, prove both attributes are `None`, and require
`number_is_provisioned(...) is False`. Run the named test and require it to fail
because the current comparison accepts `None == None`.

- [ ] **Step 2: Implement the non-null guard and truthful fixtures**

Require `provisioning.phone_number_id is not None` immediately before equality.
Give the pure matrix and activation-snapshot persisted-record fixture explicit
phone UUIDs. Add a matrix case whose provisioning link is explicitly missing.

- [ ] **Step 3: Run Task 6 GREEN and static checks**

From `apps/api`:

```bash
.venv/bin/python -m pytest \
  tests/services/test_number_provisioning_facts.py \
  tests/activation/test_activation_snapshot_service.py \
  -q
.venv/bin/ruff check \
  app/services/number_provisioning_facts.py \
  tests/services/test_number_provisioning_facts.py \
  tests/activation/test_activation_snapshot_service.py
.venv/bin/mypy app/services/number_provisioning_facts.py
```

- [ ] **Step 4: Prove mutation sensitivity and commit**

Temporarily remove the non-null guard and require the unassigned-identity test
to fail. Restore it, rerun GREEN, and commit:

```bash
git add \
  apps/api/app/services/number_provisioning_facts.py \
  apps/api/tests/services/test_number_provisioning_facts.py \
  apps/api/tests/activation/test_activation_snapshot_service.py
git commit -m "fix(api): reject unassigned number links"
```

---

### Task 7: Reject padded explicit local-auth tokens

**Files:**
- Modify: `apps/api/app/core/runtime_validation.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/web/src/lib/auth/server-session.ts`
- Modify: `apps/web/tests/lib/server-session.test.ts`

**Interfaces:**
- Consumes: the existing server-only `LOCAL_AUTH_TOKEN` value in explicit local
  mode.
- Produces: bounded configuration errors for absent, blank, or padded values.
- Preserves: the original exact token, constant-time API comparison, Clerk mode,
  and the client/server secret boundary.

- [ ] **Step 1: Add API and web RED cases**

Extend API runtime/Compose validation with a nonblank padded token and require
the existing safe `LOCAL_AUTH_TOKEN` error. Add a server-session test requiring
the same padded value to throw without returning a normalized token. Run both
named tests and require failures for the expected acceptance/trimming behavior.

- [ ] **Step 2: Implement exact fail-closed validation**

In API local-mode runtime validation, reject when the token is missing or is not
equal to its stripped form. In the server-only web session boundary, reject when
the raw value is absent or differs from `trim()`, then return the raw value.
Do not change `LocalAuthProvider`, Clerk configuration, or client-visible code.

- [ ] **Step 3: Run Task 7 GREEN and static checks**

From `apps/api` and `apps/web`, respectively:

```bash
.venv/bin/python -m pytest \
  tests/test_deployment_readiness.py \
  tests/auth/test_local_auth.py \
  -q
.venv/bin/ruff check \
  app/core/runtime_validation.py \
  tests/test_deployment_readiness.py
.venv/bin/mypy app/core/runtime_validation.py

env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  npm test -- --run tests/lib/server-session.test.ts
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  npm run typecheck
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  npm run lint
```

- [ ] **Step 4: Prove both boundaries are mutation-sensitive and commit**

Temporarily remove the API padded-token rejection and require its test to fail.
Restore it. Temporarily restore web trimming and require its padded-token test to
fail. Restore exact rejection, rerun GREEN, and commit:

```bash
git add \
  apps/api/app/core/runtime_validation.py \
  apps/api/tests/test_deployment_readiness.py \
  apps/web/src/lib/auth/server-session.ts \
  apps/web/tests/lib/server-session.test.ts
git commit -m "fix(auth): reject padded local tokens"
```

---

### Task 8: Verify and review Issues 41A and 42A

**Files:**
- Modify: none unless verification proves a Task 6–7 defect.

- [ ] Run the focused Task 6–7 API/web tests and static checks.
- [ ] Run complete API and web tests plus their documented static gates.
- [ ] Run `git diff --check main...HEAD` and require status to contain only the
  preserved untracked `apps/api/.venv`.
- [ ] Recheck the unchanged live container IDs/restart counts, API/worker Telnyx
  mode, web Clerk presence, and HTTP `200`, `200`, `307` without recreating any
  service or touching live provider/database state.
- [ ] Run independent plan-level and whole-branch scoped re-reviews. Resolve any
  finding test-first before presenting deferred documentation Issues 33–35.
