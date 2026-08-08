# Account Deactivation Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immediate, reversible account deactivation that stops new service,
cancels billing, drains an existing call, releases the assigned number, preserves
historical data, and supports a generation-safe reactivation journey.

**Architecture:** Make `User.status` and `User.lifecycle_generation` the
authoritative account projection, with one private
`AccountDeactivationOperation` coordinating provider cleanup through a
reference-only transactional outbox event. Keep Stripe and Telnyx I/O behind
small provider adapters and outside database transactions; every worker
delivery reloads current state and advances only committed, idempotent steps.
Expose a safe account API and dashboard while retaining existing owner-scoped
historical reads.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL 17,
SQLite, ARQ transactional outbox, Stripe Python 15, Telnyx Python 2, Next.js 16,
React 19, TypeScript 5.9, Tailwind CSS 4, Vitest, Playwright, pytest, Ruff, mypy.

## Global Constraints

- Implement account deactivation, not permanent account deletion. Do not add a
  `DELETE /api/account` endpoint or erase customer/historical data.
- Owner-requested deactivation is effective locally at the first commit,
  immediately cancels Stripe without automatic proration or refund, disables
  routing, drains any already-admitted call, and permanently releases the
  assigned Opevo number.
- Subscription-only cancellation stays in the Stripe-hosted Billing Portal and
  remains active until the end of the paid period.
- The exact owner confirmation is case-sensitive `DEACTIVATE`.
- The only customer account states are `active`, `deactivating`, and `inactive`;
  the only valid transitions are `active -> deactivating -> inactive -> active`.
- A deactivating or inactive account cannot admit calls or mutate profile,
  receptionist, provisioning, verification, routing, or go-live state. An
  already-running call may append transcripts and finalize normally.
- Preserve identity, confirmed profile, receptionist configuration, calls,
  transcripts, summaries, recordings, usage, notifications, and billing
  history indefinitely. Clear only the released phone assignment and
  number-specific activation state.
- Reactivation requires a generation-matched new subscription, fresh number
  consent, a new number, forwarding verification, and explicit go-live
  approval. Payment alone must not make the account call-ready.
- Provider calls never run while an ORM transaction or business row lock is
  open. Provider payloads, credentials, phone numbers, signed URLs, and raw
  errors never enter outbox payloads, customer responses, logs, or metric
  labels.
- Validate the typed confirmation in memory only; never store or log
  `DEACTIVATE` or the submitted confirmation value.
- The `account.deactivate` outbox payload is exactly
  `{"operation_id": "<uuid>"}` and its aggregate type is
  `account-deactivation-operation`.
- Retryable cleanup failures use bounded exponential backoff without exhausting
  into false completion. Terminal contract/authentication/identity failures
  leave the account non-serving and set safe operator-attention state.
- Keep SQLite development support, but treat isolated PostgreSQL concurrency
  tests and the provider-free restart acceptance journey as release gates.
- Do not deploy, contact real providers, use real credentials, push, or publish
  externally as part of this plan.
- Use test-driven development in every task: add the focused failing test,
  observe the expected failure, implement the smallest production change, run
  the focused and stated regression sets, then commit.

## File Map

### Create

- `apps/api/app/models/account_deactivation_operation.py`
- `apps/api/app/repositories/account_deactivation_repository.py`
- `apps/api/app/schemas/account.py`
- `apps/api/app/services/account_access_policy.py`
- `apps/api/app/services/account_lifecycle_service.py`
- `apps/api/app/providers/subscriptions/__init__.py`
- `apps/api/app/providers/subscriptions/base.py`
- `apps/api/app/providers/subscriptions/factory.py`
- `apps/api/app/providers/subscriptions/fake.py`
- `apps/api/app/providers/subscriptions/stripe.py`
- `apps/api/app/workers/jobs/account_deactivation.py`
- `apps/api/app/routers/account.py`
- `apps/api/alembic/versions/0015_add_account_deactivation_lifecycle.py`
- `apps/api/tests/test_account_deactivation_migration.py`
- `apps/api/tests/services/test_account_lifecycle_service.py`
- `apps/api/tests/services/test_account_access_policy.py`
- `apps/api/tests/activation/test_account_state_mutation_guards.py`
- `apps/api/tests/providers/test_subscription_providers.py`
- `apps/api/tests/workers/test_account_deactivation.py`
- `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- `apps/web/src/lib/types/account.ts`
- `apps/web/src/lib/api/account.ts`
- `apps/web/src/app/(app)/dashboard/account/actions.ts`
- `apps/web/src/app/(app)/dashboard/account/page.tsx`
- `apps/web/src/components/account/account-lifecycle-banner.tsx`
- `apps/web/src/components/account/account-status-card.tsx`
- `apps/web/src/components/account/deactivate-account-dialog.tsx`
- `apps/web/tests/app/account-actions.test.ts`
- `apps/web/tests/app/account-page.test.tsx`
- `apps/web/tests/e2e/deactivation-start.spec.ts`

### Modify

- API models and migrations:
  `apps/api/app/models/{__init__,user,subscription}.py`,
  `apps/api/alembic/env.py`,
  `apps/api/tests/{test_activation_domain_migration,test_migration_revision_ids,test_integrity_models}.py`
- API repositories:
  `apps/api/app/repositories/{agent_config,call,customer_activation,phone_number,phone_number_provisioning,subscription,user}_repository.py`
- API services and schemas:
  `apps/api/app/services/{activation_go_live,activation_provisioning,agent_config,billing_query,billing_service,billing_session,business_profile,carrier_lookup,customer_readiness_policy,customer_readiness_service,forwarding_verification,local_billing,outbox,subscription_access_policy}.py`,
  `apps/api/app/schemas/{activation,agent,billing_api}.py`
- API providers and workers:
  `apps/api/app/providers/telephony/{base,fake,telnyx}.py`,
  `apps/api/app/workers/jobs/{outbox_delivery,outbox_topics,phone_provisioning}.py`,
  `apps/api/app/workers/arq_worker.py`
- API boundaries and configuration:
  `apps/api/app/{main.py,core/config.py,core/observability.py,core/runtime_validation.py,routers/activation.py,routers/agent.py,routers/billing.py,routers/development.py}`,
  `.env.example`, `compose.yaml`, `compose.dev.yaml`
- Focused API tests under
  `apps/api/tests/{agent,integration,providers,services,workers}` plus
  `apps/api/tests/{test_deployment_readiness,test_observability,test_readiness}.py`
- Web:
  `apps/web/src/app/(app)/dashboard/{layout.tsx,agent/page.tsx,billing/page.tsx,billing/actions.ts}`,
  `apps/web/src/app/(app)/dashboard/_components/sidebar/app-sidebar.tsx`,
  `apps/web/src/components/agent/agent-settings-form.tsx`,
  `apps/web/src/lib/{api/activation.ts,api/billing.ts,types/billing.ts}`,
  `apps/web/tests/app/{activation-page,agent-page,billing-page,app-shell}.test.tsx`,
  `apps/web/tests/e2e/{activation,restart-resume}.spec.ts`,
  `scripts/run-local-e2e.sh`
- Documentation:
  `docs/PROJECT_STATUS.md`,
  `docs/engineering/2026-07-18-production-readiness-handoff.md`,
  `docs/architecture/local-self-service-activation.md`,
  `docs/architecture/integration-endpoints.md`,
  `docs/runbooks/deploy.md`

---

## Task 1: Add the lifecycle and operation persistence model

**Interfaces:**

- Consumes: current migration head
  `0014_add_recording_egress_operations`, `User`, `Subscription`, and existing
  UUID/timestamp model mixins.
- Produces:
  `AccountDeactivationOperation`,
  `AccountDeactivationRepository.get_incomplete_by_user_id_for_update(user_id:
  UUID) -> AccountDeactivationOperation | None`,
  `get_latest_by_user_id(user_id: UUID) -> AccountDeactivationOperation | None`,
  `get_by_id(operation_id: UUID) -> AccountDeactivationOperation | None`,
  `get_by_id_for_update(operation_id: UUID) -> AccountDeactivationOperation |
  None`, and `create(...) -> AccountDeactivationOperation`.

**Files:**

- Create: `apps/api/app/models/account_deactivation_operation.py`
- Create: `apps/api/app/repositories/account_deactivation_repository.py`
- Create: `apps/api/alembic/versions/0015_add_account_deactivation_lifecycle.py`
- Create: `apps/api/tests/test_account_deactivation_migration.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/app/models/user.py`
- Modify: `apps/api/app/models/subscription.py`
- Modify: `apps/api/alembic/env.py`
- Modify: `apps/api/tests/test_activation_domain_migration.py`
- Modify: `apps/api/tests/test_migration_revision_ids.py`
- Modify: `apps/api/tests/test_integrity_models.py`

- [ ] **Step 1: Write failing model and migration tests.**

Assert this exact public model shape and database invariants:

```python
def test_account_deactivation_model_shape() -> None:
    columns = AccountDeactivationOperation.__table__.c
    assert set(columns.keys()) == {
        "id", "user_id", "lifecycle_generation", "trigger", "status",
        "stripe_subscription_id", "phone_provider_id", "requested_at",
        "routing_disabled_at", "subscription_canceled_at",
        "active_call_drained_at", "number_released_at",
        "activation_reset_at", "completed_at", "attempt_count",
        "last_reconciled_at", "last_error_code", "created_at", "updated_at",
    }
    assert {constraint.name for constraint in
            AccountDeactivationOperation.__table__.constraints} >= {
        "uq_account_deactivation_operations_user_generation",
        "ck_account_deactivation_operations_trigger_allowed",
        "ck_account_deactivation_operations_status_allowed",
        "ck_account_deactivation_operations_generation_positive",
        "ck_account_deactivation_operations_completion_consistent",
        "ck_account_deactivation_operations_attempt_count_nonnegative",
        "ck_account_deactivation_operations_step_order",
    }
    assert User.__table__.c.lifecycle_generation.nullable is False
    assert Subscription.__table__.c.cancel_at_period_end.nullable is False
    assert Subscription.__table__.c.lifecycle_generation.nullable is False
```

The Alembic test must upgrade a pre-0015 fixture, prove existing users and
subscriptions receive generation `1`, prove `cancel_at_period_end = false`,
prove downgrade removes only new lifecycle structures, and inspect the partial
unique index `uq_account_deactivation_operations_one_incomplete_user` with
predicate `completed_at IS NULL`.

- [ ] **Step 2: Run the tests and confirm the missing model/revision failure.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_account_deactivation_migration.py \
  tests/test_migration_revision_ids.py \
  tests/test_integrity_models.py
```

Expected: collection fails because
`app.models.account_deactivation_operation` and revision `0015` do not exist.

- [ ] **Step 3: Implement the model, repository, and revision.**

Use these constants and fields:

```python
ACCOUNT_STATUSES = frozenset({"active", "deactivating", "inactive"})
DEACTIVATION_TRIGGERS = frozenset({"owner_request", "subscription_ended"})
DEACTIVATION_STATUSES = frozenset(
    {"pending", "processing", "attention_required", "completed"}
)
DeactivationTrigger = Literal["owner_request", "subscription_ended"]

class AccountDeactivationOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "account_deactivation_operations"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    lifecycle_generation: Mapped[int] = mapped_column(nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255))
    phone_provider_id: Mapped[str | None] = mapped_column(String(255))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    routing_disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    subscription_canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    active_call_drained_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    number_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    activation_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
```

Add a unique constraint on `(user_id, lifecycle_generation)`, the named partial
unique index on `user_id WHERE completed_at IS NULL`, and the named checks from
the test. The completion check is:

```sql
(status = 'completed' AND completed_at IS NOT NULL)
OR (status <> 'completed' AND completed_at IS NULL)
```

The step-order check requires each non-null timestamp to have its predecessor:
routing disable -> subscription cancel -> active-call drain -> number release
-> activation reset -> completion. When no provider resource exists, the worker
still records the applicable step timestamp so the invariant remains uniform.

Add `User.lifecycle_generation` with server default `1`, a named user-status
check, and add to `Subscription`:

```python
lifecycle_generation: Mapped[int] = mapped_column(
    nullable=False, default=1, server_default=text("1")
)
cancel_at_period_end: Mapped[bool] = mapped_column(
    nullable=False, default=False, server_default=false()
)
cancellation_effective_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True)
)
```

The migration must backfill before adding non-null constraints, use explicit
named checks, set `revision = "0015_account_deactivation"` and
`down_revision = "0014_recording_egress_ops"`, and
keep server defaults for generation/boolean so direct inserts remain valid.

- [ ] **Step 4: Run focused tests and static checks.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_account_deactivation_migration.py \
  tests/test_activation_domain_migration.py \
  tests/test_migration_revision_ids.py \
  tests/test_integrity_models.py \
  tests/repositories/test_subscription_repository.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/models app/repositories/account_deactivation_repository.py \
  alembic/versions/0015_add_account_deactivation_lifecycle.py \
  tests/test_account_deactivation_migration.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the persistence foundation.**

```bash
git add apps/api/app/models apps/api/app/repositories/account_deactivation_repository.py \
  apps/api/alembic apps/api/tests/test_account_deactivation_migration.py \
  apps/api/tests/test_activation_domain_migration.py \
  apps/api/tests/test_migration_revision_ids.py apps/api/tests/test_integrity_models.py \
  apps/api/tests/repositories/test_subscription_repository.py
git commit -m "feat: add account deactivation persistence"
```

---

## Task 2: Add the authoritative account command and query API

**Interfaces:**

- Consumes: Task 1 repository and models, `UserRepository`,
  `SubscriptionRepository`, `PhoneNumberRepository`, `AgentConfigRepository`,
  `OutboxService`, and `CustomerReadinessService`.
- Produces:
  `AccountLifecycleService.get_account(user_id: UUID) -> AccountStatusResponse`,
  `request_owner_deactivation(user_id: UUID, confirmation: str) ->
  AccountStatusResponse`,
  `request_in_transaction(user_id: UUID, trigger: DeactivationTrigger,
  stripe_subscription_id: str | None = None) ->
  AccountDeactivationOperation | None`, and routes `GET /api/account` and
  `POST /api/account/deactivate`.

**Files:**

- Create: `apps/api/app/schemas/account.py`
- Create: `apps/api/app/services/account_lifecycle_service.py`
- Create: `apps/api/app/routers/account.py`
- Create: `apps/api/tests/services/test_account_lifecycle_service.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/repositories/agent_config_repository.py`
- Modify: `apps/api/app/repositories/user_repository.py`
- Modify: `apps/api/app/services/outbox_service.py`
- Modify: `apps/api/tests/services/test_outbox_service.py`
- Modify: `apps/api/tests/fakes.py`

- [ ] **Step 1: Write failing API, transaction, privacy, and idempotency tests.**

Use an authenticated owner fixture and assert:

```python
assert client.post(
    "/api/account/deactivate", json={"confirmation": "deactivate"}
).status_code == 422

response = client.post(
    "/api/account/deactivate", json={"confirmation": "DEACTIVATE"}
)
assert response.status_code == 202
assert response.json() == {
    "status": "deactivating",
    "serving": False,
    "deactivation": {
        "state": "requested",
        "requested_at": response.json()["deactivation"]["requested_at"],
    },
    "reactivation_allowed": False,
    "blocker": "account_deactivating",
}
```

Assert the first request increments generation once, sets user
`deactivating`, sets `AgentConfig.is_enabled = false`, sets the current
`PhoneNumber.is_active = false`, creates one operation, and creates one outbox
event with payload keys exactly `{"operation_id"}`. Assert a repeated request
returns the same safe response without another generation increment, operation,
or event. Assert responses never contain `stripe_subscription_id`,
`phone_provider_id`, attempt counts, or raw errors. Assert inactive `GET`
returns `reactivation_allowed = true` only when no incomplete operation exists
and no phone assignment remains. A direct repeat after completion returns the
current inactive response without changing generation. A repeated final Stripe
event for the same stored subscription returns the matching completed
operation; a stale terminal event for an older subscription after a later
reactivation returns `None` and cannot start another deactivation.
For an active account, assert `serving` mirrors the central readiness result:
it is false during onboarding and true only when routing is ready.

- [ ] **Step 2: Run the tests and confirm route/service failures.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_account_lifecycle_service.py \
  tests/services/test_outbox_service.py
```

Expected: import/404 failures for the new account surface and unsupported
`account.deactivate` outbox topic.

- [ ] **Step 3: Implement schemas, lifecycle service, outbox contract, and router.**

Use these schema contracts:

```python
class AccountDeactivateRequest(BaseModel):
    confirmation: Literal["DEACTIVATE"]

class DeactivationProgressResponse(BaseModel):
    state: Literal[
        "requested", "disabling_routing", "canceling_subscription",
        "draining_call", "releasing_number", "finalizing",
        "attention_required",
    ]
    requested_at: datetime

class AccountStatusResponse(BaseModel):
    status: Literal["active", "deactivating", "inactive"]
    serving: bool
    deactivation: DeactivationProgressResponse | None
    reactivation_allowed: bool
    blocker: Literal[
        "account_deactivating", "account_inactive",
        "deactivation_attention_required", "reactivation_not_ready",
        "customer_not_ready",
    ] | None
```

`request_in_transaction` must lock the user first and return an incomplete
operation if one exists. Before creating anything for an inactive account,
return the latest completed operation when it contains the same stored
subscription identity, and return `None` for a stale subscription identity
after reactivation. Owner requests against an already-inactive account return
the safe current account response. Otherwise snapshot the current subscription
ID and private phone provider ID, increment the generation, set the local
non-serving projections, create the operation, and call:

```python
await outbox_service.add(
    topic="account.deactivate",
    aggregate_type="account-deactivation-operation",
    aggregate_id=operation.id,
    idempotency_key=f"account.deactivate:{operation.id}",
    payload={"operation_id": str(operation.id)},
)
```

For `trigger == "subscription_ended"`, set
`subscription_canceled_at=requested_at`. Public owner requests commit and wake
the existing outbox delivery mechanism only after the transaction succeeds.
Map operation timestamps to the first unfinished safe state; terminal state
maps to `attention_required`.

`get_account` obtains `serving` from the central customer-readiness snapshot,
not from `status == "active"` alone. `deactivating` and `inactive` always
return `serving = false`.

Protect `POST /api/account/deactivate` with the existing owner authentication
and rate limiter at `5/minute`; return `202` for first and repeat calls.

- [ ] **Step 4: Run API, outbox, and auth regressions.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_account_lifecycle_service.py \
  tests/services/test_outbox_service.py \
  tests/test_outbox_migration.py \
  tests/test_health.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/schemas/account.py app/services/account_lifecycle_service.py \
  app/routers/account.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the account API.**

```bash
git add apps/api/app/schemas/account.py \
  apps/api/app/services/account_lifecycle_service.py \
  apps/api/app/routers/account.py apps/api/app/main.py \
  apps/api/app/repositories/agent_config_repository.py \
  apps/api/app/repositories/user_repository.py \
  apps/api/app/services/outbox_service.py \
  apps/api/tests/services/test_account_lifecycle_service.py \
  apps/api/tests/services/test_outbox_service.py apps/api/tests/fakes.py
git commit -m "feat: add account deactivation API"
```

---

## Task 3: Enforce account state at every mutation and admission boundary

**Interfaces:**

- Consumes: Task 1 `User.status` and `User.lifecycle_generation`.
- Produces:
  `AccountStateBlockedError(code: Literal["account_deactivating",
  "account_inactive"])`,
  `require_active_account(user: User) -> None`, distinct readiness blocker
  values `ACCOUNT_DEACTIVATING` and `ACCOUNT_INACTIVE`, and stable HTTP `409`
  responses for blocked owner mutations.

**Files:**

- Create: `apps/api/app/services/account_access_policy.py`
- Create: `apps/api/tests/services/test_account_access_policy.py`
- Create: `apps/api/tests/activation/test_account_state_mutation_guards.py`
- Modify: `apps/api/app/services/customer_readiness_policy.py`
- Modify: `apps/api/app/services/customer_readiness_service.py`
- Modify: `apps/api/app/services/business_profile_service.py`
- Modify: `apps/api/app/services/agent_config_service.py`
- Modify: `apps/api/app/services/carrier_lookup_service.py`
- Modify: `apps/api/app/services/activation_provisioning_service.py`
- Modify: `apps/api/app/services/forwarding_verification_service.py`
- Modify: `apps/api/app/services/activation_go_live_service.py`
- Modify: `apps/api/app/workers/jobs/phone_provisioning.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/routers/activation.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/tests/auth/test_clerk_sync.py`
- Modify: focused tests under `apps/api/tests/{agent,services,workers,livekit}`
- Modify: `apps/api/tests/test_readiness.py`

- [ ] **Step 1: Write failing policy and boundary tests.**

Parameterize `deactivating` and `inactive` and prove:

```python
@pytest.mark.parametrize(
    ("status", "code"),
    [("deactivating", "account_deactivating"),
     ("inactive", "account_inactive")],
)
def test_account_state_blocks_owner_mutations(status: str, code: str) -> None:
    user = User(status=status)
    with pytest.raises(AccountStateBlockedError) as raised:
        require_active_account(user)
    assert raised.value.code == code
```

Add service/route tests for business-profile save/confirm, agent update, carrier
lookup, provisioning confirm/retry, verification-window open, and go-live.
Each must return `409` with the stable code and leave the database/outbox
unchanged. Add worker tests proving claimed `phone.provision` and `phone.enable`
and `livekit.verification_dispatch` events recheck current status immediately
before provider I/O. Add a dispatch race test proving no customer call is
admitted after the deactivation commit.

Keep explicit positive tests showing inactive owners can list/get calls, obtain
an owner-scoped recording playback URL, read billing/account state, and that a
pre-existing call can append transcripts and finalize while the user is
`deactivating`. Add a Clerk resynchronization test proving login/bootstrap does
not change an existing `deactivating` or `inactive` user back to `active`.

- [ ] **Step 2: Run the focused suite and confirm the new guards fail.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_customer_readiness_policy.py \
  tests/services/test_account_access_policy.py \
  tests/activation/test_account_state_mutation_guards.py \
  tests/agent/test_agent_config_api.py \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/livekit/test_forwarding_verification_dispatch.py \
  tests/calls/test_call_history_api.py \
  tests/agent/test_transcript_append.py \
  tests/agent/test_call_completion.py
```

Expected: missing policy module and mutation paths still accept non-active
accounts.

- [ ] **Step 3: Implement one pure policy and apply it inside command services.**

```python
AccountBlockerCode = Literal["account_deactivating", "account_inactive"]

class AccountStateBlockedError(RuntimeError):
    def __init__(self, code: AccountBlockerCode) -> None:
        super().__init__(code)
        self.code = code

def require_active_account(user: User) -> None:
    if user.status == "deactivating":
        raise AccountStateBlockedError("account_deactivating")
    if user.status == "inactive":
        raise AccountStateBlockedError("account_inactive")
    if user.status != "active":
        raise AccountStateBlockedError("account_inactive")
```

Every listed command service must load or lock the user and call this function
before its first mutation or outbox write. Do not add broad HTTP middleware:
call-scoped transcript/finalization and owner-scoped historical reads must
remain available.

Change readiness policy version from `runtime-v3` to `runtime-v4`, replace the
generic inactive blocker with `ACCOUNT_DEACTIVATING` and `ACCOUNT_INACTIVE`,
and make the routing/provisioning handlers reload `User.status` immediately
before provider enable/provision calls. The router exception handlers return:

```json
{"detail": {"code": "account_inactive"}}
```

- [ ] **Step 4: Run all affected service/worker/read tests.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_account_access_policy.py \
  tests/services/test_customer_readiness_policy.py \
  tests/activation/test_account_state_mutation_guards.py \
  tests/activation/test_business_profile_service.py \
  tests/activation/test_activation_provisioning_service.py \
  tests/activation/test_forwarding_verification_service.py \
  tests/activation/test_activation_go_live_service.py \
  tests/agent/test_agent_config_api.py \
  tests/auth/test_clerk_sync.py \
  tests/workers/test_phone_routing_readiness.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/livekit/test_forwarding_verification_dispatch.py \
  tests/calls/test_call_history_api.py \
  tests/agent/test_transcript_append.py \
  tests/agent/test_call_completion.py \
  tests/test_readiness.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the account-state policy.**

```bash
git add apps/api/app/services/account_access_policy.py \
  apps/api/app/services/customer_readiness_policy.py \
  apps/api/app/services/customer_readiness_service.py \
  apps/api/app/services/business_profile_service.py \
  apps/api/app/services/agent_config_service.py \
  apps/api/app/services/carrier_lookup_service.py \
  apps/api/app/services/activation_provisioning_service.py \
  apps/api/app/services/forwarding_verification_service.py \
  apps/api/app/services/activation_go_live_service.py \
  apps/api/app/workers/jobs/phone_provisioning.py \
  apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/app/routers/activation.py apps/api/app/routers/agent.py \
  apps/api/tests/services/test_account_access_policy.py \
  apps/api/tests/activation/test_account_state_mutation_guards.py \
  apps/api/tests/activation/test_business_profile_service.py \
  apps/api/tests/activation/test_activation_provisioning_service.py \
  apps/api/tests/activation/test_forwarding_verification_service.py \
  apps/api/tests/activation/test_activation_go_live_service.py \
  apps/api/tests/agent/test_agent_config_api.py \
  apps/api/tests/auth/test_clerk_sync.py \
  apps/api/tests/workers/test_phone_routing_readiness.py \
  apps/api/tests/workers/test_livekit_dispatch_outbox.py \
  apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py \
  apps/api/tests/livekit/test_forwarding_verification_dispatch.py \
  apps/api/tests/calls/test_call_history_api.py \
  apps/api/tests/agent/test_transcript_append.py \
  apps/api/tests/agent/test_call_completion.py apps/api/tests/test_readiness.py
git commit -m "feat: enforce inactive account boundaries"
```

---

## Task 4: Add explicit Stripe cancellation and portal contracts

**Interfaces:**

- Consumes: `Settings.billing_mode`, Task 1 subscription identity.
- Produces:
  `SubscriptionProvider.cancel_immediately(subscription_id: str) -> None`,
  `SubscriptionProviderError(category: Literal["provider_retryable",
  "provider_terminal"], error_class: Literal["timeout", "rate_limited",
  "unavailable", "authentication", "validation", "conflict", "unknown"])`,
  and `build_subscription_provider(settings: Settings) ->
  SubscriptionProvider`.

**Files:**

- Create: `apps/api/app/providers/subscriptions/__init__.py`
- Create: `apps/api/app/providers/subscriptions/base.py`
- Create: `apps/api/app/providers/subscriptions/factory.py`
- Create: `apps/api/app/providers/subscriptions/fake.py`
- Create: `apps/api/app/providers/subscriptions/stripe.py`
- Create: `apps/api/tests/providers/test_subscription_providers.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/runtime_validation.py`
- Modify: `apps/api/app/services/billing_session_service.py`
- Modify: `apps/api/tests/services/test_billing_session_service.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `compose.dev.yaml`

- [ ] **Step 1: Write failing provider, session, and deployment-contract tests.**

Test the fake adapter, a stubbed Stripe module, and settings validation:

```python
await provider.cancel_immediately("sub_current")
stripe.Subscription.cancel.assert_called_once_with(
    "sub_current",
    invoice_now=False,
    prorate=False,
    api_key="sk_test_value",
)
```

Assert timeouts, `429`, connection errors, and `5xx` become retryable safe
errors; Stripe `resource_missing`/`404` for the exact stored subscription is
idempotent success; authentication/permission, other invalid requests, `409`,
and `422` become terminal safe errors. Assert raw Stripe messages are neither
returned nor logged. Assert `BillingSessionService.create_portal_session`
passes the configured portal configuration ID:

```python
stripe.billing_portal.Session.create.assert_called_once_with(
    customer="cus_123",
    return_url="https://app.example.test/dashboard/billing",
    configuration="bpc_period_end_cancel",
)
```

Production validation must reject Stripe mode when
`STRIPE_BILLING_PORTAL_CONFIGURATION_ID` is absent. Worker configuration must
reject Stripe mode without `STRIPE_SECRET_KEY`; fake development mode must not
require it.

- [ ] **Step 2: Run the tests and confirm missing provider/config failures.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers/test_subscription_providers.py \
  tests/services/test_billing_session_service.py \
  tests/test_deployment_readiness.py
```

Expected: import failure for `app.providers.subscriptions` and missing portal
configuration validation.

- [ ] **Step 3: Implement the provider boundary and pinned portal behavior.**

```python
ProviderErrorCategory = Literal["provider_retryable", "provider_terminal"]
ProviderErrorClass = Literal[
    "timeout", "rate_limited", "unavailable", "authentication",
    "validation", "conflict", "unknown",
]

class SubscriptionProvider(ABC):
    @abstractmethod
    async def cancel_immediately(self, subscription_id: str) -> None:
        raise NotImplementedError

class SubscriptionProviderError(RuntimeError):
    def __init__(self, category: ProviderErrorCategory, *,
                 error_class: ProviderErrorClass) -> None:
        super().__init__(error_class)
        self.category = category
        self.error_class = error_class
```

The fake provider validates a non-empty ID and records no external state.
`StripeSubscriptionProvider` initializes Stripe with the same bounded `(5, 30)`
connect/read timeout and two network retries as `BillingSessionService`, calls
`stripe.Subscription.cancel` through `asyncio.to_thread`, and validates the
returned object has the requested ID and `status == "canceled"`.

Add:

```python
stripe_billing_portal_configuration_id: str | None = None
```

to settings, pass it as `configuration` in portal session creation, and require
it for production Stripe mode. Put `BILLING_MODE` and `STRIPE_SECRET_KEY` in the
shared worker environment in `compose.yaml`; put `BILLING_MODE=fake` in the
worker environment in `compose.dev.yaml`. Document the new environment value
in `.env.example`.

The configured Stripe Portal object must be created outside this codebase with
`features.subscription_cancel.enabled=true`,
`features.subscription_cancel.mode=at_period_end`, and proration disabled.
The application stores only its `bpc_...` ID and startup validation proves it
was supplied; real-provider certification remains a deployment gate.

- [ ] **Step 4: Run provider, session, and runtime validation suites.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers/test_subscription_providers.py \
  tests/services/test_billing_session_service.py \
  tests/test_deployment_readiness.py \
  tests/test_health.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/providers/subscriptions app/services/billing_session_service.py \
  app/core/config.py app/core/runtime_validation.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the subscription-provider boundary.**

```bash
git add apps/api/app/providers/subscriptions apps/api/app/core/config.py \
  apps/api/app/core/runtime_validation.py \
  apps/api/app/services/billing_session_service.py \
  apps/api/tests/providers/test_subscription_providers.py \
  apps/api/tests/services/test_billing_session_service.py \
  apps/api/tests/test_deployment_readiness.py \
  .env.example compose.yaml compose.dev.yaml
git commit -m "feat: add immediate subscription cancellation provider"
```

---

## Task 5: Add idempotent Telnyx number release

**Interfaces:**

- Consumes: current `TelephonyProvider` and `TelephonyProviderError`.
- Produces:
  `TelephonyProvider.release_number(*, provider_number_id: str) -> None` in
  fake and Telnyx implementations, with success when Telnyx confirms deletion
  or the exact provider number is already absent.

**Files:**

- Modify: `apps/api/app/providers/telephony/base.py`
- Modify: `apps/api/app/providers/telephony/fake.py`
- Modify: `apps/api/app/providers/telephony/telnyx.py`
- Modify: `apps/api/app/providers/telephony/twilio.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/tests/providers/test_fake_telephony_provider.py`
- Modify: `apps/api/tests/providers/test_integrations.py`
- Modify: `apps/api/tests/telephony/test_telnyx_provider.py`
- Modify: `apps/api/tests/test_observability.py`

- [ ] **Step 1: Write failing release-contract tests.**

Assert fake release accepts its deterministic provider ID, rejects empty or
foreign IDs, and is safe when repeated. Stub `telnyx.PhoneNumber.delete` and
assert:

```python
await provider.release_number(provider_number_id="123456789")
phone_number_resource.delete.assert_called_once_with(
    "123456789", api_key="KEY"
)
```

Test returned `{"data": {"id": "123456789", "status": "deleted"}}`, repeated
`404` as success, timeouts/`429`/`5xx` as retryable, and authentication,
deletion-lock/identity conflict, malformed response, and wrong returned ID as
terminal. Assert the observability provider-operation allowlist contains
`release_number`.

Provider contracts verified on 2026-07-24:
[Stripe immediate cancellation](https://docs.stripe.com/api/subscriptions/cancel?lang=python),
[Stripe period-end cancellation](https://docs.stripe.com/billing/subscriptions/cancel),
[Stripe Portal cancellation mode](https://docs.stripe.com/api/customer_portal/configurations/object?lang=python),
and [Telnyx number deletion](https://developers.telnyx.com/api-reference/phone-number-configurations/delete-a-phone-number).

- [ ] **Step 2: Run tests and confirm the abstract method is absent.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers/test_fake_telephony_provider.py \
  tests/providers/test_integrations.py \
  tests/telephony/test_telnyx_provider.py \
  tests/test_observability.py
```

Expected: release calls fail because `TelephonyProvider.release_number` is not
defined.

- [ ] **Step 3: Implement release in the provider contract and adapters.**

```python
class TelephonyProvider(ABC):
    @abstractmethod
    async def release_number(self, *, provider_number_id: str) -> None:
        raise NotImplementedError
```

Use the installed Telnyx 2.x resource operation:

```python
result = await asyncio.to_thread(
    self._phone_number_resource.delete,
    provider_number_id,
    api_key=self.api_key,
)
```

Normalize the SDK object/dict into `data.id` and `data.status`, require the
exact ID and `deleted`, and reuse the existing Telnyx error classifier. Treat a
Telnyx `404` for this exact stored resource ID as idempotent success. Keep
deletion lock/`422`, ambiguous identity, wrong ID, and malformed responses
terminal; do not log the provider ID or response body.

Implement the new method on the dormant `TelephonyTwilio` adapter as an
explicit `NotImplementedError`, matching its existing provision/enable/disable
methods, so the abstract contract remains complete without implying Twilio
support.

- [ ] **Step 4: Run provider and static checks.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers/test_fake_telephony_provider.py \
  tests/providers/test_integrations.py \
  tests/telephony/test_telnyx_provider.py \
  tests/test_observability.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/providers/telephony app/core/observability.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the release capability.**

```bash
git add apps/api/app/providers/telephony apps/api/app/core/observability.py \
  apps/api/tests/providers/test_fake_telephony_provider.py \
  apps/api/tests/providers/test_integrations.py \
  apps/api/tests/telephony/test_telnyx_provider.py \
  apps/api/tests/test_observability.py
git commit -m "feat: add telephony number release"
```

---

## Task 6: Build the restart-safe deactivation reconciler

**Interfaces:**

- Consumes: Task 2 `account.deactivate` operation/event, Task 4
  `SubscriptionProvider`, Task 5 `TelephonyProvider`, current outbox delivery
  retry contract.
- Produces:
  `deliver_account_deactivation(ctx: dict[str, Any], event: OutboxEvent) ->
  None`, repository cleanup methods
  `CallRepository.has_active_by_user_id(user_id: UUID) -> bool`,
  `CallRepository.detach_phone_number(phone_number_id: UUID) -> int`,
  `CustomerActivationRepository.reset_number_cycle(user_id: UUID) -> None`,
  and deactivation observability snapshots.

**Files:**

- Create: `apps/api/app/workers/jobs/account_deactivation.py`
- Create: `apps/api/tests/workers/test_account_deactivation.py`
- Modify: `apps/api/app/repositories/account_deactivation_repository.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Modify: `apps/api/app/repositories/customer_activation_repository.py`
- Modify: `apps/api/app/repositories/phone_number_repository.py`
- Modify: `apps/api/app/repositories/phone_number_provisioning_repository.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/tests/workers/test_arq_worker.py`
- Modify: `apps/api/tests/integration/test_outbox_delivery.py`
- Modify: `apps/api/tests/test_observability.py`

- [ ] **Step 1: Write failing phase, restart, transaction, and privacy tests.**

Build a fake operation and assert this strict order:

```python
assert provider_calls == [
    ("telephony.disable", stored_phone_provider_id),
    ("subscription.cancel", stored_stripe_subscription_id),
    ("telephony.release", stored_phone_provider_id),
]
```

For `subscription_ended`, assert the cancellation call is absent. For an active
call, assert disable/cancel timestamps commit, release is absent, and delivery
raises:

```python
OutboxDeliveryError(
    "account_call_draining",
    retryable=True,
    exhaustible=False,
)
```

Redeliver after call completion and assert release/reset/completion happen once.
Restart at each committed step timestamp and prove earlier provider steps are
not repeated. Instrument the session and assert every provider call occurs
with no active transaction. Test retryable provider failures as non-exhausting;
test terminal provider failures set `status="attention_required"` and
`last_error_code` to a safe allow-listed value while leaving user
`deactivating`. Assert logs/outbox/customer projections contain no provider
IDs, E.164 values, raw errors, or customer content.

- [ ] **Step 2: Run the tests and confirm the handler is missing.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_account_deactivation.py \
  tests/integration/test_outbox_delivery.py \
  tests/workers/test_arq_worker.py \
  tests/test_observability.py
```

Expected: import/handler-registry failure for
`deliver_account_deactivation`.

- [ ] **Step 3: Implement one convergent handler with committed phases.**

Register:

```python
DEFAULT_OUTBOX_HANDLERS["account.deactivate"] = (
    deliver_account_deactivation
)
```

The handler validates payload keys exactly `{"operation_id"}`, then repeats
these phases, opening and closing a short transaction for every load/mark:

```text
1. reload operation/user; return only if completed; otherwise set processing,
   increment attempt_count, set last_reconciled_at, and clear a prior
   retryable last_error_code
2. disable stored phone provider ID; commit routing_disabled_at
3. owner_request: if local subscription is already terminal, mark the step;
   otherwise cancel stored Stripe ID; in the following transaction set the
   matching local subscription to canceled, clear cancel_at_period_end, set
   cancellation_effective_at, and commit subscription_canceled_at
   subscription_ended: verify local subscription is terminal; mark the step
4. query active call; if present, persist safe progress and retry later
5. commit active_call_drained_at
6. release stored phone provider ID; commit number_released_at
7. lock user and current projections; detach call phone FKs, delete old
   provisioning and phone rows, reset number-only activation fields
8. commit activation_reset_at, completed_at, status=completed,
   user.status=inactive
```

`reset_number_cycle` clears provisioning consent/key, carrier/verification
window/session/result/fingerprints, forwarding verification, go-live
request/approval, activation time, and number-specific failure code. It keeps
profile confirmation/revision and business/receptionist content. Agent content
remains and `is_enabled` remains false.

Map provider retryable classes to non-exhausting `OutboxDeliveryError`; map
terminal classes to the safe codes
`subscription_authentication`, `subscription_contract`,
`telephony_authentication`, `telephony_release_conflict`, or
`provider_contract`. Mark attention in a separate committed transaction before
raising an exhaustible terminal delivery error.

Extend outbox reconciliation so due `account.deactivate` events continue using
existing bounded exponential backoff. Add low-cardinality observations for
trigger, safe phase/outcome, incomplete count, oldest age, completion latency,
and attention count; labels must never include IDs.

Use the exact instruments
`opevo.account_deactivation.operations`,
`opevo.account_deactivation.oldest_incomplete_age`,
`opevo.account_deactivation.reconciliation_results`,
`opevo.account_deactivation.attention`, and
`opevo.account_deactivation.completion_duration`. Permit only trigger,
operation status, safe step, safe outcome, and safe error class as attributes.

- [ ] **Step 4: Run worker, preservation, and regression tests.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_account_deactivation.py \
  tests/integration/test_outbox_delivery.py \
  tests/workers/test_arq_worker.py \
  tests/workers/test_call_finalization_worker.py \
  tests/calls/test_call_finalization_state_machine.py \
  tests/services/test_recording_service.py \
  tests/test_observability.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/workers/jobs/account_deactivation.py \
  app/repositories/account_deactivation_repository.py \
  app/repositories/call_repository.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit durable cleanup.**

```bash
git add apps/api/app/workers/jobs/account_deactivation.py \
  apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/app/workers/jobs/outbox_delivery.py \
  apps/api/app/workers/arq_worker.py \
  apps/api/app/repositories/account_deactivation_repository.py \
  apps/api/app/repositories/call_repository.py \
  apps/api/app/repositories/customer_activation_repository.py \
  apps/api/app/repositories/phone_number_repository.py \
  apps/api/app/repositories/phone_number_provisioning_repository.py \
  apps/api/app/core/observability.py \
  apps/api/tests/workers/test_account_deactivation.py \
  apps/api/tests/workers/test_arq_worker.py \
  apps/api/tests/integration/test_outbox_delivery.py \
  apps/api/tests/test_observability.py
git commit -m "feat: reconcile account deactivation"
```

---

## Task 7: Converge Stripe period-end cancellation and generation-safe reactivation

**Interfaces:**

- Consumes: Task 1 subscription lifecycle columns, Task 2
  `AccountLifecycleService.request_in_transaction`, Task 3 state policy.
- Produces: persisted scheduled-cancellation projection, final Stripe
  cancellation convergence on the deactivation operation, checkout metadata
  `lifecycle_generation`, and reactivation only for a matching current
  generation.

**Files:**

- Modify: `apps/api/app/repositories/subscription_repository.py`
- Modify: `apps/api/app/services/subscription_access_policy.py`
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/app/services/billing_session_service.py`
- Modify: `apps/api/app/services/local_billing_service.py`
- Modify: `apps/api/app/services/billing_query_service.py`
- Modify: `apps/api/app/services/activation_provisioning_service.py`
- Modify: `apps/api/app/schemas/billing_api.py`
- Modify: `apps/api/app/routers/billing.py`
- Modify: `apps/api/tests/billing/test_billing_api.py`
- Modify: `apps/api/tests/billing/test_stripe_webhooks.py`
- Modify: `apps/api/tests/repositories/test_subscription_repository.py`
- Modify: `apps/api/tests/services/test_subscription_access_policy.py`
- Modify: `apps/api/tests/services/test_subscription_service_sessions.py`
- Modify: `apps/api/tests/services/test_billing_session_service.py`
- Modify: `apps/api/tests/integration/test_subscription_disable_intent.py`
- Modify: `apps/api/tests/integration/test_postgres_subscription_service_sessions.py`
- Modify: `apps/api/tests/integration/test_local_activation_to_number.py`

- [ ] **Step 1: Write failing webhook, checkout, reversal, and stale-event tests.**

Cover these cases with explicit assertions:

```python
# customer.subscription.updated
assert subscription.cancel_at_period_end is True
assert subscription.cancellation_effective_at == period_end
assert user.status == "active"
assert phone.is_active is True
assert deactivation_operation is None

# cancellation reversal before period end
assert subscription.cancel_at_period_end is False
assert subscription.cancellation_effective_at is None
assert user.status == "active"

# customer.subscription.deleted for current subscription/generation
assert user.status == "deactivating"
assert operation.trigger == "subscription_ended"
assert operation.subscription_canceled_at is not None
assert account_outbox.payload == {"operation_id": str(operation.id)}
```

Assert owner checkout is rejected while `deactivating`, and inactive checkout
is rejected when an incomplete operation or phone row exists. Assert Stripe
Checkout session and subscription metadata both include:

```python
{
    "clerk_user_id": user.clerk_user_id,
    "user_id": str(user.id),
    "plan_tier": "starter",
    "lifecycle_generation": str(user.lifecycle_generation),
}
```

Assert an active/trialing new subscription reactivates only an `inactive`
account with matching metadata generation. Older subscription/invoice events,
missing-generation replacement events, and events for the canceled ID cannot
set the user active, enable a phone, or grant a current-generation service
transition. Preserve legacy generation `1` event support only for accounts
that have never advanced beyond generation `1`.

For fake billing, assert generated subscription and invoice/grant IDs include
the current generation and that a second lifecycle gets a different fake
subscription and number-provisioning operation key.

- [ ] **Step 2: Run the billing suites and confirm lifecycle cases fail.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/billing/test_billing_api.py \
  tests/billing/test_stripe_webhooks.py \
  tests/repositories/test_subscription_repository.py \
  tests/services/test_subscription_access_policy.py \
  tests/services/test_subscription_service_sessions.py \
  tests/services/test_billing_session_service.py \
  tests/integration/test_subscription_disable_intent.py \
  tests/integration/test_postgres_subscription_service_sessions.py \
  tests/integration/test_local_activation_to_number.py
```

Expected: scheduled fields/generation metadata are absent and final
cancellation still emits only `phone.disable`.

- [ ] **Step 3: Persist schedule state and route final cancellation through the lifecycle service.**

Extend the repository signature exactly:

```python
async def upsert_by_stripe_subscription_id(
    self, *, user_id: UUID, stripe_customer_id: str | None,
    stripe_subscription_id: str, plan_tier: str | None, status: str,
    allocated_minutes: int | None, current_period_start: datetime | None,
    current_period_end: datetime | None,
    stripe_subscription_created_at: datetime | None = None,
    last_stripe_event_created_at: datetime | None = None,
    lifecycle_generation: int,
    cancel_at_period_end: bool = False,
    cancellation_effective_at: datetime | None = None,
) -> Subscription | None:
```

Extract `cancel_at_period_end` and `cancel_at`/`current_period_end` from Stripe.
An updated event only changes the schedule projection. A terminal current
subscription calls:

```python
await account_lifecycle_service.request_in_transaction(
    user_id=subscription.user_id,
    trigger="subscription_ended",
    stripe_subscription_id=subscription.stripe_subscription_id,
)
```

Do not separately emit `phone.disable` for final cancellation; the lifecycle
operation owns cleanup. Keep Stripe event watermarks and duplicate handling.
If owner-requested cancellation has already advanced the user generation, a
terminal event for the exact subscription stored on the incomplete operation
converges on that operation. Once a later generation is active, terminal
events for older subscription IDs are stale and cannot deactivate it.

- [ ] **Step 4: Implement checkout and reactivation generation rules.**

Change the checkout access decision to consume account status, current
subscription status, incomplete-operation presence, and phone presence. For
inactive accounts, permit checkout only when the operation is complete, the
phone row is absent, and the old subscription is replaceable.

Include generation in checkout metadata and require it when accepting a
replacement subscription. Set `User.status = "active"` only for
`active|trialing`, exact-current-generation subscription state. Do not enable a
phone: activation readiness remains blocked until a new number, forwarding
verification, and go-live.

Extend the hosted-session boundary with
`create_checkout_session(..., lifecycle_generation: int) -> HostedSession` and
copy the same four metadata keys into both top-level `metadata` and
`subscription_data["metadata"]`. The route reads the generation from the
locked eligibility decision, closes the transaction before Stripe I/O, and
never derives it again from request data.

Use these fake identities:

```python
stripe_subscription_id = (
    f"local_subscription_{user.id}_g{user.lifecycle_generation}"
)
invoice_id = f"local_invoice_{user.id}_g{user.lifecycle_generation}"
provider_operation_key = (
    f"activation:provision:{activation.id}:g{user.lifecycle_generation}"
)
```

Expose `cancel_at_period_end` and `cancellation_effective_at` from billing query
and `schemas/billing_api.py`. Keep portal access available whenever a Stripe
customer exists.

- [ ] **Step 5: Run focused billing, activation, and PostgreSQL race tests.**

```bash
set -e
cd /home/mo/code/ai/bmad-opevo
cleanup_account_pg() {
  COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
    docker compose -f compose.dev.yaml down --volumes --remove-orphans
}
trap cleanup_account_pg EXIT
COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
  docker compose -f compose.dev.yaml up -d --wait postgres redis
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call \
  TEST_REDIS_URL=redis://127.0.0.1:56381/0 \
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/billing/test_billing_api.py \
  tests/billing/test_stripe_webhooks.py \
  tests/repositories/test_subscription_repository.py \
  tests/services/test_subscription_access_policy.py \
  tests/services/test_subscription_service_sessions.py \
  tests/services/test_billing_session_service.py \
  tests/integration/test_subscription_disable_intent.py \
  tests/integration/test_postgres_subscription_service_sessions.py \
  tests/integration/test_local_activation_to_number.py
cd ../..
cleanup_account_pg
trap - EXIT
```

Expected: all tests pass with the isolated test PostgreSQL/Redis services at
ports `55434` and `56381`, with no skip in the PostgreSQL files.

- [ ] **Step 6: Commit billing convergence and reactivation.**

```bash
git add apps/api/app/repositories/subscription_repository.py \
  apps/api/app/services/subscription_access_policy.py \
  apps/api/app/services/billing_service.py \
  apps/api/app/services/billing_session_service.py \
  apps/api/app/services/local_billing_service.py \
  apps/api/app/services/billing_query_service.py \
  apps/api/app/services/activation_provisioning_service.py \
  apps/api/app/schemas/billing_api.py apps/api/app/routers/billing.py \
  apps/api/tests/billing/test_billing_api.py \
  apps/api/tests/billing/test_stripe_webhooks.py \
  apps/api/tests/repositories/test_subscription_repository.py \
  apps/api/tests/services/test_subscription_access_policy.py \
  apps/api/tests/services/test_subscription_service_sessions.py \
  apps/api/tests/services/test_billing_session_service.py \
  apps/api/tests/integration/test_subscription_disable_intent.py \
  apps/api/tests/integration/test_postgres_subscription_service_sessions.py \
  apps/api/tests/integration/test_local_activation_to_number.py
git commit -m "feat: converge subscription lifecycle with account state"
```

---

## Task 8: Prove concurrency, drainage, and data preservation in PostgreSQL

**Interfaces:**

- Consumes: Tasks 1–7 complete API/worker lifecycle.
- Produces: authoritative isolated-PostgreSQL evidence for request/webhook
  convergence, admission ordering, call drainage, stale-work rejection,
  lifecycle generations, and historical-data preservation.

**Files:**

- Create: `apps/api/tests/integration/test_account_deactivation_concurrency.py`
- Modify: `apps/api/tests/integration/test_livekit_dispatch_concurrency.py`
- Modify: `apps/api/tests/integration/test_forwarding_verification_privacy.py`
- Modify: `apps/api/tests/integration/test_integrity_constraints.py`
- Modify: `apps/api/tests/calls/test_call_history_api.py`

- [ ] **Step 1: Add failing race and preservation tests.**

Use two independent `AsyncSession`s and explicit barriers. Cover:

1. Two owner requests create one operation, one generation increment, and one
   reference-only event.
2. Owner request racing final Stripe cancellation converges on that operation.
3. Call admission racing the account lock has one valid result: a call
   committed first may finish; a deactivation committed first rejects the call.
4. Release does not run while a call has a status in
   `pending|connected|ending|finalizing`.
5. Stale `phone.enable`, invoice, provisioning, verification, and go-live work
   cannot change a deactivating account or invoke its provider.
6. Checkout/provisioning for generation `N+1` cannot begin while generation
   `N` cleanup is incomplete.
7. Completion deletes only phone/provisioning projection state and clears
   number-specific activation fields.
8. Profile, agent content, calls/messages/summaries, recording metadata,
   notifications, usage ledger, and canceled subscription history remain
   owner-scoped and readable.

Use row counts and stable IDs for evidence; never compare only HTTP text.

- [ ] **Step 2: Run and observe at least one race/preservation failure.**

```bash
set -e
cd /home/mo/code/ai/bmad-opevo
cleanup_account_pg() {
  COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
    docker compose -f compose.dev.yaml down --volumes --remove-orphans
}
trap cleanup_account_pg EXIT
COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
  docker compose -f compose.dev.yaml up -d --wait postgres redis
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call \
  TEST_REDIS_URL=redis://127.0.0.1:56381/0 \
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_account_deactivation_concurrency.py \
  tests/integration/test_livekit_dispatch_concurrency.py \
  tests/integration/test_forwarding_verification_privacy.py \
  tests/integration/test_integrity_constraints.py
cd ../..
cleanup_account_pg
trap - EXIT
```

Expected: new tests expose any missing lock/revalidation/cleanup behavior.

- [ ] **Step 3: Make only the concurrency corrections exposed by the tests.**

Maintain the lifecycle transaction's multi-aggregate lock order:

```text
user -> subscription -> phone -> deactivation operation -> outbox
```

Do not hold these locks during provider I/O. Keep call finalization on its
existing call-first order and never lock the user and call rows together:
deactivation queries active-call existence in a separate transaction after the
account is already non-serving, then retries instead of waiting on the call
lock. Before projection reset, query active-call existence again in its own
transaction; because post-commit admission is blocked, an empty result cannot
gain a new call.

- [ ] **Step 4: Run the isolated PostgreSQL suite with zero skips.**

```bash
set -e
cd /home/mo/code/ai/bmad-opevo
cleanup_account_pg() {
  COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
    docker compose -f compose.dev.yaml down --volumes --remove-orphans
}
trap cleanup_account_pg EXIT
COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
  docker compose -f compose.dev.yaml up -d --wait postgres redis
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call \
  TEST_REDIS_URL=redis://127.0.0.1:56381/0 \
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration
cd ../..
cleanup_account_pg
trap - EXIT
```

Expected: exit `0`; the output contains no `skipped`.

- [ ] **Step 5: Commit concurrency evidence and corrections.**

```bash
git add apps/api/app/services/account_lifecycle_service.py \
  apps/api/app/services/livekit_dispatch_service.py \
  apps/api/app/services/billing_service.py \
  apps/api/app/services/activation_provisioning_service.py \
  apps/api/app/workers/jobs/account_deactivation.py \
  apps/api/tests/integration/test_account_deactivation_concurrency.py \
  apps/api/tests/integration/test_livekit_dispatch_concurrency.py \
  apps/api/tests/integration/test_forwarding_verification_privacy.py \
  apps/api/tests/integration/test_integrity_constraints.py \
  apps/api/tests/calls/test_call_history_api.py
git commit -m "test: prove account deactivation concurrency"
```

---

## Task 9: Add Account, read-only, and scheduled-cancellation web UX

**Interfaces:**

- Consumes: Task 2 `GET/POST /api/account`, Task 7 billing response fields.
- Produces:
  `getAccount(): Promise<AccountStatus>`,
  `deactivateAccount(confirmation: string): Promise<ActionResult>`,
  `reactivateAccount(): Promise<HostedActionResult>`,
  `/dashboard/account`, global lifecycle banner, read-only configuration UI,
  and inactive reactivation action.

**Files:**

- Create: `apps/web/src/lib/types/account.ts`
- Create: `apps/web/src/lib/api/account.ts`
- Create: `apps/web/src/app/(app)/dashboard/account/actions.ts`
- Create: `apps/web/src/app/(app)/dashboard/account/page.tsx`
- Create: `apps/web/src/components/account/account-lifecycle-banner.tsx`
- Create: `apps/web/src/components/account/account-status-card.tsx`
- Create: `apps/web/src/components/account/deactivate-account-dialog.tsx`
- Create: `apps/web/tests/app/account-actions.test.ts`
- Create: `apps/web/tests/app/account-page.test.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/_components/sidebar/app-sidebar.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Modify: `apps/web/src/components/agent/agent-settings-form.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/billing/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/billing/actions.ts`
- Modify: `apps/web/src/lib/api/billing.ts`
- Modify: `apps/web/src/lib/types/billing.ts`
- Modify: `apps/web/src/lib/api/activation.ts`
- Modify: focused files under `apps/web/tests/app`

- [ ] **Step 1: Write failing component, action, navigation, and state tests.**

Assert the dialog button stays disabled until the exact input is
`DEACTIVATE`, and render this complete consequence copy:

```text
New calls stop immediately.
Your subscription is canceled immediately with no automatic prorated refund.
An active call may finish before cleanup completes.
Your current Opevo number is permanently released.
Your calls, recordings, billing history, and saved configuration are retained.
Reactivation requires a new subscription and a newly provisioned number.
```

Assert:

- active state renders a danger zone;
- deactivating state renders “Opevo is no longer accepting new calls” and
  “Finishing account deactivation” with no provider IDs;
- attention state keeps the same truthful non-serving copy;
- inactive state renders `Reactivate Opevo`, retained-data copy, and never
  presents the old number as assigned;
- local-development reactivation calls `/api/development/activate-starter` and
  returns to `/activate`, while Stripe mode creates a hosted Checkout URL;
- Calls and Billing remain in navigation;
- agent controls and save action are disabled for deactivating/inactive state;
- activation redirects a non-active owner to `/dashboard/account`;
- billing renders “Cancels at the end of your paid period” and the localized
  effective date without describing the account as inactive.

- [ ] **Step 2: Run the focused web tests and confirm missing UI/API failures.**

```bash
cd apps/web
npm run test:ci -- \
  tests/app/account-actions.test.ts \
  tests/app/account-page.test.tsx \
  tests/app/app-shell.test.tsx \
  tests/app/agent-page.test.tsx \
  tests/app/activation-page.test.tsx \
  tests/app/billing-page.test.tsx
```

Expected: import/render failures for the account types, API, page, and
components.

- [ ] **Step 3: Implement typed account API and server actions.**

```typescript
export type AccountStatus = {
  status: "active" | "deactivating" | "inactive";
  serving: boolean;
  deactivation: {
    state:
      | "requested" | "disabling_routing" | "canceling_subscription"
      | "draining_call" | "releasing_number" | "finalizing"
      | "attention_required";
    requested_at: string;
  } | null;
  reactivation_allowed: boolean;
  blocker:
    | "account_deactivating" | "account_inactive"
    | "deactivation_attention_required" | "reactivation_not_ready"
    | "customer_not_ready"
    | null;
};
```

`getAccount` uses the existing authenticated backend client. The server action
posts `{confirmation}`, maps only safe API validation/conflict codes,
revalidates `/dashboard`, `/dashboard/account`, `/dashboard/agent`,
`/dashboard/billing`, and the activation route, then redirects to the Account
page on success.

`reactivateAccount` uses `getDevelopmentCapabilities()`: with local fake
billing it calls the existing `activateDevelopmentStarter()` API and returns
`/activate`; otherwise it calls `createCheckoutSession("starter")` and returns
the hosted URL. It never changes account status in the web process.

- [ ] **Step 4: Implement the page, banner, confirmation dialog, and read-only states.**

Add an `Account` sidebar entry using `UserRound`. Fetch account state in the
dashboard layout and show the banner for deactivating/inactive. The account
page renders one primary state card and the danger zone only when active.

Pass `readOnly: boolean` to `AgentSettingsForm`; disable inputs, switches, and
submit while preserving visible saved values. Guard its server action remains
API-enforced. Redirect non-active activation pages to Account before rendering
mutable milestones. Inactive `Reactivate Opevo` calls the existing checkout
or local-development activation boundary selected by the server action; the API
owns all eligibility checks. Add scheduled-cancellation fields to the billing
type/card.

- [ ] **Step 5: Run web tests, type checking, formatting, and build.**

```bash
cd apps/web
npm run test:ci -- \
  tests/app/account-actions.test.ts \
  tests/app/account-page.test.tsx \
  tests/app/app-shell.test.tsx \
  tests/app/agent-page.test.tsx \
  tests/app/activation-page.test.tsx \
  tests/app/billing-page.test.tsx
npm run check
npm run typecheck
npm run build
```

Expected: all commands exit `0`.

- [ ] **Step 6: Commit the Account web experience.**

```bash
git add apps/web/src apps/web/tests/app
git commit -m "feat: add account deactivation experience"
```

---

## Task 10: Extend the provider-free restart acceptance journey

**Interfaces:**

- Consumes: complete fake-provider lifecycle and existing local E2E runner.
- Produces: development-only active-call fixture endpoints and a browser
  acceptance path that deactivates, restarts during drainage, completes,
  preserves history, reactivates, and provisions a different fake number.

**Files:**

- Create: `apps/web/tests/e2e/deactivation-start.spec.ts`
- Modify: `apps/api/app/routers/development.py`
- Modify: `apps/api/tests/integration/test_local_activation_to_number.py`
- Modify: `apps/web/tests/e2e/activation.spec.ts`
- Modify: `apps/web/tests/e2e/restart-resume.spec.ts`
- Modify: `scripts/run-local-e2e.sh`

- [ ] **Step 1: Write failing development-fixture and Playwright acceptance tests.**

Add authenticated development-only endpoints:

```text
POST /api/development/call-drain-fixture/start
POST /api/development/call-drain-fixture/finish
```

The start endpoint creates one owner-scoped connected call only in
`APP_ENV=development` and `TELEPHONY_MODE=fake`; it returns only
`{"call_id": "<uuid>"}`. The finish endpoint accepts that call ID, verifies
ownership, and advances it through the real lifecycle to `completed`; it does
not mutate account state directly. Both endpoints are absent in production.

The browser test must:

1. record the assigned fake number and one visible historical call identifier
   into the Node-only JSON path supplied by `E2E_STATE_FILE`;
2. start the active-call fixture using Playwright's server-side
   `request` context and `E2E_LOCAL_AUTH_TOKEN`;
3. submit exact `DEACTIVATE`;
4. prove the account immediately shows non-serving state and activation/new
   routing is blocked;
5. exit while cleanup is waiting for the call.

After the runner restarts services, the resume test finishes the fixture,
polls Account until `inactive`, proves the historical call remains, reactivates
through fake billing, gives fresh number consent, and asserts the new displayed
fake number differs from the stored old number.

- [ ] **Step 2: Run the API fixture test and observe missing endpoints.**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_local_activation_to_number.py
```

Expected: fixture endpoint tests return `404`.

- [ ] **Step 3: Implement the development fixture through real call services.**

Reuse `CallLifecycleService`/repositories to create and complete the call so
active-call detection uses production statuses. Require local authenticated
ownership, generate no external LiveKit/Telnyx work, and reject use unless both
development and fake telephony checks pass. Keep route registration behind the
existing development-router configuration.

- [ ] **Step 4: Update the E2E runner for a restart between deactivation phases.**

Use `mktemp -d` for the state directory, export:

```bash
export E2E_STATE_FILE="${e2e_state_dir}/account-lifecycle.json"
export E2E_API_BASE_URL="http://127.0.0.1:${API_PORT}"
export E2E_LOCAL_AUTH_TOKEN="opevo-local-development-token"
```

Run `activation.spec.ts` and `deactivation-start.spec.ts`, restart API and
worker containers without deleting volumes, then run `restart-resume.spec.ts`.
The existing trap removes the temporary directory. Never place the auth token
in browser local storage, screenshots, test output, or a client-prefixed
environment variable.

- [ ] **Step 5: Run the complete provider-free acceptance journey.**

```bash
bash scripts/run-local-e2e.sh
```

Expected: exit `0`; the account is inactive after restart/drain, retained
history is visible, reactivation resumes at consent, and the new fake number is
different.

- [ ] **Step 6: Commit the restart acceptance path.**

```bash
git add apps/api/app/routers/development.py \
  apps/api/tests/integration/test_local_activation_to_number.py \
  apps/web/tests/e2e scripts/run-local-e2e.sh
git commit -m "test: cover account deactivation restart journey"
```

---

## Task 11: Update operations documentation and run final release gates

**Interfaces:**

- Consumes: all prior tasks.
- Produces: accurate project status, architecture/runbook contracts, and final
  local evidence without claiming deployment or provider certification.

**Files:**

- Modify: `docs/PROJECT_STATUS.md`
- Modify: `docs/engineering/2026-07-18-production-readiness-handoff.md`
- Modify: `docs/architecture/local-self-service-activation.md`
- Modify: `docs/architecture/integration-endpoints.md`
- Modify: `docs/runbooks/deploy.md`

- [ ] **Step 1: Update documentation with implemented and unimplemented scope.**

Document:

- the three account states and transition rules;
- exact immediate versus period-end cancellation behavior;
- no automatic prorated refund;
- active-call drainage before Telnyx release;
- retained data and read-only inactive access;
- lifecycle generation and reactivation prerequisites;
- `GET /api/account`, `POST /api/account/deactivate`, and development-only
  fixture endpoints;
- `BILLING_MODE`, `STRIPE_SECRET_KEY`, and
  `STRIPE_BILLING_PORTAL_CONFIGURATION_ID` requirements;
- the Stripe Portal requirement for period-end cancellation and disabled
  proration;
- Telnyx release error/attention behavior and operator observations.
- alerting instructions: page on every increment of
  `opevo.account_deactivation.attention`, and alert when
  `opevo.account_deactivation.oldest_incomplete_age` exceeds
  `MAX_CALL_DURATION_SECONDS + 900` seconds.
- operator recovery: remediate the credential/contract/identity fault, requeue
  only the failed reference-only outbox event for the recorded operation ID,
  and verify the operation reaches `completed` before closing the incident.

Keep permanent account deletion, export, retention, backup erasure, cloud
deployment, legal approval, and real Stripe/Telnyx certification explicitly
open.

- [ ] **Step 2: Run API format, type, migration, unit, and integration gates.**

```bash
set -e
cd /home/mo/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check .
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
cd ../..
cleanup_account_pg() {
  COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
    docker compose -f compose.dev.yaml down --volumes --remove-orphans
}
trap cleanup_account_pg EXIT
COMPOSE_PROJECT_NAME=opevo-account-pg POSTGRES_PORT=55434 REDIS_PORT=56381 \
  docker compose -f compose.dev.yaml up -d --wait postgres redis
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call \
  TEST_REDIS_URL=redis://127.0.0.1:56381/0 \
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
cd ../..
cleanup_account_pg
trap - EXIT
```

Expected: every command exits `0`; PostgreSQL integration output contains no
skips.

- [ ] **Step 3: Run web and provider-free acceptance gates.**

```bash
cd apps/web
npm run check
npm run typecheck
npm run test:ci
npm run build
cd ../..
bash scripts/run-local-e2e.sh
```

Expected: every command exits `0`.

- [ ] **Step 4: Review privacy, provider-I/O, and diff invariants.**

```bash
if rg -n \
  'stripe_subscription_id|phone_provider_id|provider_number_id|raw_error|response_body' \
  apps/api/app/schemas/account.py apps/api/app/routers/account.py; then
  exit 1
fi
rg -n 'account\\.deactivate' apps/api/app apps/api/tests
git diff --check
git status --short
```

Expected: the first guard finds no customer-response exposure; private
model/repository use outside those two files is expected. Every
`account.deactivate` event test asserts the reference-only payload; diff check
passes; status shows only the intended documentation edits before commit.

- [ ] **Step 5: Commit documentation and final evidence.**

```bash
git add docs/PROJECT_STATUS.md \
  docs/engineering/2026-07-18-production-readiness-handoff.md \
  docs/architecture/local-self-service-activation.md \
  docs/architecture/integration-endpoints.md docs/runbooks/deploy.md
git commit -m "docs: document account deactivation lifecycle"
```

- [ ] **Step 6: Request review before any merge or deployment decision.**

Run the `superpowers:requesting-code-review` skill against the complete branch,
address findings through `superpowers:receiving-code-review`, rerun the affected
gates, and use `superpowers:finishing-a-development-branch` to present merge,
PR, or cleanup choices. Do not deploy or contact real providers from this
implementation workflow.
