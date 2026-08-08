# Durable Recording Egress Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every normal Opevo call's LiveKit recording start, stop, and
owner-deletion intent durable so a late or ambiguous provider result cannot
leave an untracked recording, while terminal-call removal remains an immediate
local `204` operation.

**Architecture:** Add a private `recording_egress_operations` coordination
aggregate, a provider-free transactional `RecordingLifecycleService`, and a
provider-facing `RecordingReconciler` invoked through one reference-only
`recording.reconcile` outbox topic. Keep `Call.recording_*` as the visible
playback projection only. Every database mutation follows the lock order call
row, recording operation, then outbox; all LiveKit and S3 I/O happens after the
transaction and locks are closed.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL 17,
SQLite, ARQ transactional outbox, LiveKit API 1.x, MinIO/S3, pytest, Ruff, mypy.

## Global constraints

- Implement only the approved local recording-reconciliation slice. Do not
  deploy, contact real providers, mutate cloud resources, use real credentials,
  push, or publish externally.
- Preserve original audio for a visible call until explicit owner removal.
  Automatic 30-day retention remains disabled and outside this plan.
- `DELETE /api/calls/{call_id}` performs no provider or storage I/O. An
  authorized terminal call is locally purged and tombstoned in one transaction,
  then returns `204` even while provider cleanup is pending.
- Never issue a second recording-start request after an operation enters
  `starting`, including `uncertain` recovery.
- Commit the deterministic object key and `prepared` operation before LiveKit
  start I/O. The provider receives that committed object key and may not derive
  another one.
- Keep all provider and storage calls outside ORM transactions and row locks.
- Use the lock order call row -> recording operation -> outbox everywhere that
  mutates more than one aggregate. PostgreSQL race tests are authoritative.
- The outbox payload is exactly `{"operation_id": "<uuid>"}`. Never store
  room names, object keys, egress IDs, URLs, caller data, transcripts, summaries,
  or raw webhook data in an outbox payload.
- Logs may contain internal call and operation UUIDs plus allow-listed error
  classes. Logs and metric labels must not contain room names, object keys,
  provider egress IDs, URLs, credentials, or customer call content.
- Preserve the existing call state machine, finalization generation, usage
  accounting, summary behavior, and tombstone guards.
- Keep SQLite development support, but require the isolated PostgreSQL suite
  with zero skips before completion.
- Use test-driven development for every task: add the focused failing test,
  observe the expected failure, add the smallest implementation, rerun the
  focused test, then run the task regression set and commit.

## File map

### Create

- `apps/api/app/models/recording_egress_operation.py`
- `apps/api/app/repositories/recording_egress_operation_repository.py`
- `apps/api/app/services/recording_lifecycle_service.py`
- `apps/api/app/workers/jobs/recording_reconciliation.py`
- `apps/api/alembic/versions/0014_add_recording_egress_operations.py`
- `apps/api/tests/test_recording_egress_operation_migration.py`
- `apps/api/tests/services/test_recording_lifecycle_service.py`
- `apps/api/tests/workers/test_recording_reconciliation.py`
- `apps/api/tests/integration/test_recording_egress_concurrency.py`

### Modify

- `apps/api/app/models/__init__.py`
- `apps/api/alembic/env.py`
- `apps/api/app/repositories/call_repository.py`
- `apps/api/app/repositories/outbox_repository.py`
- `apps/api/app/providers/livekit_recording/base.py`
- `apps/api/app/providers/livekit_recording/livekit.py`
- `apps/api/app/services/outbox_service.py`
- `apps/api/app/services/livekit_recording_service.py`
- `apps/api/app/services/livekit_dispatch_service.py`
- `apps/api/app/services/call_lifecycle_service.py`
- `apps/api/app/services/call_history_service.py`
- `apps/api/app/services/recording_service.py`
- `apps/api/app/webhooks/livekit.py`
- `apps/api/app/workers/jobs/outbox_delivery.py`
- `apps/api/app/workers/jobs/outbox_topics.py`
- `apps/api/app/core/observability.py`
- `apps/api/app/routers/calls.py`
- focused tests under `apps/api/tests/{agent,calls,integration,livekit,providers,services,workers}` that currently assert `recording.stop` or synchronous deletion
- `apps/api/tests/test_observability.py`
- `apps/api/tests/test_activation_domain_migration.py`
- `apps/api/tests/test_migration_revision_ids.py`
- `README.md`
- `docs/PROJECT_STATUS.md`
- `docs/architecture/integration-endpoints.md`
- `docs/engineering/2026-07-18-production-readiness-handoff.md`

---

## Task 1: Add the recording-operation aggregate and migration

**Produces:** Durable operation identity and database invariants, including
legacy backfill and reference-only reconciliation events.

**Consumes:** Existing `calls`, `outbox_events`, migration head
`0013_outbox_routing_target`, and deterministic path
`calls/{user_id}/{call_id}.ogg`.

**Files:**

- Create: `apps/api/app/models/recording_egress_operation.py`
- Create: `apps/api/app/repositories/recording_egress_operation_repository.py`
- Create: `apps/api/alembic/versions/0014_add_recording_egress_operations.py`
- Create: `apps/api/tests/test_recording_egress_operation_migration.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/alembic/env.py`
- Modify: `apps/api/tests/test_activation_domain_migration.py`
- Modify: `apps/api/tests/test_migration_revision_ids.py`

- [ ] Add model-shape tests that assert the exact columns, nullable fields,
  five allowed start states, two uniqueness constraints, six state/timestamp
  checks, restrictive call foreign key, and due-work index. Add repository tests
  proving one row per call and one row per egress ID.

```python
def test_recording_operation_model_has_private_coordination_shape() -> None:
    columns = RecordingEgressOperation.__table__.c
    assert set(columns.keys()) == {
        "id", "call_id", "room_name", "legacy_incomplete",
        "expected_object_key", "provider_egress_id", "start_state",
        "start_attempted_at", "stop_requested_at", "delete_requested_at",
        "provider_terminal_at", "object_deleted_at", "last_reconciled_at",
        "last_error_code", "created_at", "updated_at",
    }
    assert columns.call_id.nullable is False
    assert columns.room_name.nullable is True
    assert columns.expected_object_key.nullable is False
    assert columns.provider_egress_id.nullable is True
    assert {
        constraint.name
        for constraint in RecordingEgressOperation.__table__.constraints
    } >= {
        "uq_recording_egress_operations_call_id",
        "uq_recording_egress_operations_provider_egress_id",
        "ck_recording_egress_operations_start_state_allowed",
        "ck_recording_egress_operations_provider_identity_consistent",
        "ck_recording_egress_operations_legacy_room_consistent",
        "ck_recording_egress_operations_prepared_attempt_consistent",
        "ck_recording_egress_operations_delete_implies_stop",
        "ck_recording_egress_operations_object_delete_implies_request",
    }
```

- [ ] Run the new test and confirm it fails because the model and migration do
  not exist.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_recording_egress_operation_migration.py
```

Expected: collection/import failure for
`app.models.recording_egress_operation`.

- [ ] Implement `RecordingEgressOperation` with the following core shape. Use
  the SQLAlchemy `conv` wrapper for already-complete check-constraint names so the repository
  naming convention does not prefix them twice.

```python
RECORDING_START_STATES = frozenset(
    {"prepared", "starting", "started", "not_started", "uncertain"}
)

class RecordingEgressOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "recording_egress_operations"
    __table_args__ = (
        UniqueConstraint("call_id", name="uq_recording_egress_operations_call_id"),
        UniqueConstraint(
            "provider_egress_id",
            name="uq_recording_egress_operations_provider_egress_id",
        ),
        CheckConstraint(
            "start_state IN ('prepared','starting','started','not_started','uncertain')",
            name=conv("ck_recording_egress_operations_start_state_allowed"),
        ),
        CheckConstraint(
            "(start_state = 'started' AND provider_egress_id IS NOT NULL) OR "
            "(start_state <> 'started' AND provider_egress_id IS NULL)",
            name=conv("ck_recording_egress_operations_provider_identity_consistent"),
        ),
        CheckConstraint(
            "(legacy_incomplete = false AND room_name IS NOT NULL) OR "
            "(legacy_incomplete = true AND room_name IS NULL AND "
            "start_state IN ('started','uncertain'))",
            name=conv("ck_recording_egress_operations_legacy_room_consistent"),
        ),
        CheckConstraint(
            "start_state <> 'prepared' OR start_attempted_at IS NULL",
            name=conv("ck_recording_egress_operations_prepared_attempt_consistent"),
        ),
        CheckConstraint(
            "delete_requested_at IS NULL OR stop_requested_at IS NOT NULL",
            name=conv("ck_recording_egress_operations_delete_implies_stop"),
        ),
        CheckConstraint(
            "object_deleted_at IS NULL OR delete_requested_at IS NOT NULL",
            name=conv("ck_recording_egress_operations_object_delete_implies_request"),
        ),
        Index(
            "ix_recording_egress_operations_due_work",
            "start_state", "stop_requested_at", "delete_requested_at", "updated_at",
        ),
    )

    call_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("calls.id", ondelete="RESTRICT"), nullable=False
    )
    room_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legacy_incomplete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    expected_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_egress_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_state: Mapped[str] = mapped_column(String(32), nullable=False)
    start_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    object_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

- [ ] Implement a focused repository with `get_by_id`,
  `get_by_id_for_update`, `get_by_call_id_for_update`,
  `get_by_room_name`, `add`, `delete`, and a later-used observability snapshot.
  Repository methods flush but never commit.

- [ ] Implement Alembic revision `0014_recording_egress_ops` with
  `down_revision = "0013_outbox_routing_target"`. Import the new model in both
  `app/models/__init__.py` and `alembic/env.py`.

  The migration must perform this exact transaction-local backfill:

  - select calls containing any `recording_object_key`, `recording_egress_id`,
    or `recording_url`;
  - use the call UUID as the initial operation UUID so the backfill is
    deterministic in PostgreSQL offline SQL and SQLite online tests;
  - copy `recording_object_key`, otherwise derive
    `calls/{user_id}/{call_id}.ogg`;
  - set `started` when `recording_egress_id` exists, otherwise `uncertain`;
  - set `legacy_incomplete=true` only when `livekit_room_id` is absent;
  - set stop intent for `completed` and `failed`, and both stop and delete intent
    when `deleted_at` is non-null;
  - insert `recording.reconcile` events only for work that must be inspected:
    `:delete` for deleted calls, `:stop` for terminal calls, and `:start` for an
    active uncertain call;
  - after inserting each replacement event, remove only unfinished legacy
    `recording.stop` queue rows for that call; keep delivered legacy rows as
    historical delivery records;
  - perform no LiveKit or storage I/O.

  Use dialect-specific backfill helpers: bound Python/SQLAlchemy rows with
  `uuid4()` for online SQLite, and literal PostgreSQL SQL using
  `gen_random_uuid()` for PostgreSQL and offline SQL generation. Both helpers
  must produce payloads containing only `operation_id`.

- [ ] Add migration tests for known ID, object-only uncertain, derived key,
  missing room, terminal stop, deleted stop/delete, active uncertain start,
  legacy queue replacement, reference-only payload, preserved call playback
  fields, downgrade order, and complete offline SQL through the new head.

```python
assert backfilled.start_state == "started"
assert backfilled.provider_egress_id == "EG_test"
assert backfilled.expected_object_key == f"calls/{user.id}/{call.id}.ogg"
assert backfilled.stop_requested_at is not None
assert event.topic == "recording.reconcile"
assert event.aggregate_type == "recording-egress-operation"
assert event.aggregate_id == backfilled.id
assert event.payload == {"operation_id": str(backfilled.id)}
assert call.recording_egress_id == "EG_test"
```

- [ ] Run the focused migration/model set.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_recording_egress_operation_migration.py \
  tests/test_activation_domain_migration.py \
  tests/test_migration_revision_ids.py \
  tests/test_integrity_models.py
```

Expected: all selected tests pass; the offline output advances
`0013_outbox_routing_target -> 0014_recording_egress_ops`.

- [ ] Commit the aggregate and migration.

```bash
git add apps/api/app/models apps/api/app/repositories/recording_egress_operation_repository.py apps/api/alembic apps/api/tests/test_recording_egress_operation_migration.py apps/api/tests/test_activation_domain_migration.py apps/api/tests/test_migration_revision_ids.py
git commit -m "feat: add durable recording egress operations"
```

---

## Task 2: Deepen the LiveKit recording-provider contract

**Produces:** Deterministic caller-supplied object keys, typed start outcomes,
and sanitized room egress discovery.

**Consumes:** The operation's committed room name and expected object key.

**Files:**

- Modify: `apps/api/app/providers/livekit_recording/base.py`
- Modify: `apps/api/app/providers/livekit_recording/livekit.py`
- Modify: `apps/api/app/services/livekit_recording_service.py`
- Modify: `apps/api/tests/providers/test_livekit_recording_provider.py`
- Modify: `apps/api/tests/services/test_livekit_recording_service.py`

- [ ] Add provider tests proving the exact key is passed into
  `EncodedFileOutput`, start errors expose `not_started` versus `unknown`, room
  listing returns sanitized snapshots, file paths are extracted from both room
  composite and file-result shapes, an empty/malformed start result is
  `unknown`, and no SDK object leaks through the base interface.

```python
@pytest.mark.anyio
async def test_start_uses_committed_object_key() -> None:
    result = await provider.start_room_recording(
        room_name="room-owned",
        object_key="calls/user-id/call-id.ogg",
    )
    request = egress_client.start_requests[0]
    assert request.file.filepath == "calls/user-id/call-id.ogg"
    assert result.object_key == "calls/user-id/call-id.ogg"

@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (twirp_error(code="invalid_argument", status=400), "not_started"),
        (twirp_error(code="permission_denied", status=403), "not_started"),
        (TimeoutError(), "unknown"),
        (ConnectionError(), "unknown"),
        (twirp_error(code="internal", status=500), "unknown"),
        (twirp_error(code="already_exists", status=409), "unknown"),
    ],
)
def test_start_outcome_classification(error, outcome) -> None:
    assert LiveKitRecordingProvider.start_outcome_for(error) == outcome
```

- [ ] Run the focused tests and confirm failures mention the old `user_id` /
  `call_id` signature and missing listing contract.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers/test_livekit_recording_provider.py \
  tests/services/test_livekit_recording_service.py
```

- [ ] Add these provider-facing value objects and signatures.

```python
StartOutcome = Literal["not_started", "unknown"]

@dataclass(frozen=True)
class RecordingEgressResult:
    egress_id: str
    object_key: str
    url: str | None

@dataclass(frozen=True)
class RecordingEgressSnapshot:
    egress_id: str
    room_name: str
    status: int
    object_key: str | None

def build_recording_object_key(*, user_id: UUID | str, call_id: UUID | str) -> str:
    return f"calls/{user_id}/{call_id}.ogg"

class RecordingProvider:
    async def start_room_recording(
        self, *, room_name: str, object_key: str
    ) -> RecordingEgressResult:
        raise NotImplementedError

    async def list_room_egresses(
        self, *, room_name: str
    ) -> tuple[RecordingEgressSnapshot, ...]:
        raise NotImplementedError

    async def ensure_not_running(self, egress_id: str) -> None:
        raise NotImplementedError
```

- [ ] Extend `LiveKitRecordingProviderError` with an immutable
  `start_outcome`. Only explicit local validation, authentication, permission,
  and provider validation rejections are `not_started`. Timeout, connection
  loss, 409, 429, 5xx, malformed responses, and unknown exceptions are
  `unknown`. Preserve the existing safe `category` and `error_class` fields for
  retry and telemetry.

- [ ] Implement `list_room_egresses(room_name=room_name)` with
  `api.ListEgressRequest(room_name=room_name)`. Return only egress ID, exact room
  name, numeric status, and one normalized file path. If a response exposes
  multiple conflicting file paths for one egress, return `object_key=None` so
  orchestration fails closed. Never return raw SDK values.

- [ ] Update `LiveKitRecordingService` to expose the same deep interface and to
  keep LiveKit client creation/close inside its provider session. During this
  task only, keep the wrapper's current `user_id`/`call_id` call shape as a
  compatibility adapter that calls `build_recording_object_key` before entering
  the provider. The provider itself accepts only `object_key`. Task 5 removes
  the wrapper adapter when dispatch is cut over atomically. Update direct
  provider fakes in this focused test set to the deep interface.

- [ ] Rerun provider tests, Ruff, and mypy for the changed modules.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/providers/test_livekit_recording_provider.py \
  tests/services/test_livekit_recording_service.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/providers/livekit_recording app/services/livekit_recording_service.py tests/providers tests/services/test_livekit_recording_service.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app/providers/livekit_recording app/services/livekit_recording_service.py
```

Expected: all commands exit zero.

- [ ] Commit the provider boundary.

```bash
git add apps/api/app/providers/livekit_recording apps/api/app/services/livekit_recording_service.py apps/api/tests/providers/test_livekit_recording_provider.py apps/api/tests/services/test_livekit_recording_service.py
git commit -m "refactor: expose recording reconciliation provider facts"
```

---

## Task 3: Implement transactional lifecycle commands and outbox acceleration

**Produces:** The provider-free `RecordingLifecycleService`, a tested two-minute
start-result lease, durable start/stop/delete events, and safe acceleration of
the earliest pending event.

**Consumes:** Task 1 operation repository and Task 2 deterministic key helper.

**Files:**

- Create: `apps/api/app/services/recording_lifecycle_service.py`
- Create: `apps/api/tests/services/test_recording_lifecycle_service.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Modify: `apps/api/app/repositories/outbox_repository.py`
- Modify: `apps/api/app/services/outbox_service.py`
- Modify: `apps/api/tests/services/test_outbox_service.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`

- [ ] Add outbox tests for an explicit future due time and for accelerating only
  the oldest pending event in an aggregate. Cover a processing earliest event:
  its lease must remain unchanged and the later event must remain blocked.

```python
@pytest.mark.anyio
async def test_acceleration_never_steals_processing_lease(db_session) -> None:
    operation_id = uuid4()
    first = await add_recording_event(db_session, operation_id, phase="start")
    second = await add_recording_event(db_session, operation_id, phase="stop")
    now = datetime(2026, 7, 19, tzinfo=UTC)
    first.created_at = now - timedelta(minutes=1)
    second.created_at = now
    first.status = "processing"
    first.next_attempt_at = datetime(2030, 1, 1, tzinfo=UTC)
    second.next_attempt_at = datetime(2030, 1, 2, tzinfo=UTC)
    await db_session.flush()

    changed = await OutboxRepository(db_session).make_oldest_pending_due(
        aggregate_type="recording-egress-operation",
        aggregate_id=operation_id,
        due_at=now,
    )

    assert changed is True
    assert first.next_attempt_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert second.next_attempt_at == now
    assert await OutboxRepository(db_session).claim_batch(limit=1, now=now) == []
```

- [ ] Add lifecycle tests for:

  - `prepare_start` atomically inserts `prepared` plus a `:start` event due
    exactly `START_RESULT_LEASE` later;
  - repeated prepare is idempotent;
  - `begin_start` compare-and-sets only `prepared -> starting`, writes the
    injected time, and refuses a stopped, deleted, non-connected, or tombstoned
    call;
  - success stores operation identity and projects only to a visible call;
  - known rejection records `not_started`; ambiguous error records `uncertain`;
  - no error or recovery method changes `uncertain` back to `starting`;
  - direct result, stop, and delete accelerate the earliest pending event;
  - call end creates stop intent without an egress ID;
  - repeated stop/delete preserve their first timestamps and event identity;
  - deletion creates or repairs an operation from legacy playback metadata,
    without placing any metadata in the event payload.

- [ ] Run the lifecycle and outbox tests and observe failures for the missing
  service and `next_attempt_at` argument.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_recording_lifecycle_service.py \
  tests/services/test_outbox_service.py
```

- [ ] Extend `OutboxService.add` with an optional `next_attempt_at`; default to
  the injected/current UTC time. Add this repository update, which never
  modifies `processing` work.

- [ ] Add `recording.reconcile` to `SUPPORTED_OUTBOX_TOPICS` and require exactly
  `{"operation_id"}` in `REFERENCE_PAYLOAD_FIELDS`. Keep `recording.stop`
  temporarily for the existing runtime until Task 5. Add payload tests for a
  valid UUID, extra fields, a missing field, and a malformed UUID.

```python
async def make_oldest_pending_due(
    self,
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    due_at: datetime,
) -> bool:
    oldest_pending_id = (
        select(OutboxEvent.id)
        .where(
            OutboxEvent.aggregate_type == aggregate_type,
            OutboxEvent.aggregate_id == aggregate_id,
            OutboxEvent.status == "pending",
        )
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .limit(1)
        .scalar_subquery()
    )
    result = await self.session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == oldest_pending_id,
            OutboxEvent.status == "pending",
            OutboxEvent.next_attempt_at > due_at,
        )
        .values(next_attempt_at=due_at)
    )
    return bool(result.rowcount)
```

- [ ] Add `CallRepository.get_by_id_including_deleted_for_update` and
  `get_by_id_for_user_including_deleted_for_update`. Late results and owner
  removal can then follow the global call-before-operation lock order without
  reopening tombstoned content.

- [ ] Implement these public lifecycle values and commands. The service flushes
  but never performs provider I/O; its caller owns commit/rollback.

```python
START_RESULT_LEASE = timedelta(minutes=2)
RECORDING_AGGREGATE_TYPE = "recording-egress-operation"

@dataclass(frozen=True)
class RecordingStartClaim:
    operation_id: UUID
    call_id: UUID
    room_name: str
    expected_object_key: str

@dataclass(frozen=True)
class RecordingEgressEventFact:
    external_event_id: str
    event_type: Literal["egress_started", "egress_updated", "egress_ended"]
    egress_id: str
    room_name: str
    status: int
    object_key: str | None

class RecordingLifecycleService:
    async def prepare_start(self, call: Call) -> RecordingEgressOperation:
        raise NotImplementedError

    async def begin_start(self, operation_id: UUID) -> RecordingStartClaim | None:
        raise NotImplementedError

    async def record_start_success(
        self, operation_id: UUID, result: RecordingEgressResult
    ) -> RecordingEgressOperation | None:
        raise NotImplementedError

    async def record_start_error(
        self, operation_id: UUID, *, outcome: StartOutcome, error_code: str
    ) -> RecordingEgressOperation | None:
        raise NotImplementedError

    async def request_stop(self, call: Call) -> RecordingEgressOperation | None:
        raise NotImplementedError

    async def request_deletion(self, call: Call) -> RecordingEgressOperation | None:
        raise NotImplementedError

    async def accept_egress_event(
        self, fact: RecordingEgressEventFact
    ) -> Literal["accepted", "duplicate", "missing", "mismatch", "conflict"]:
        raise NotImplementedError
```

- [ ] Validate `error_code` against a recording-specific safe allow-list before
  storing it. `prepare_start` creates the deterministic object key and adds:

```python
await self.outbox_service.add(
    topic="recording.reconcile",
    aggregate_type=RECORDING_AGGREGATE_TYPE,
    aggregate_id=operation.id,
    idempotency_key=f"recording.reconcile:{operation.id}:start",
    payload={"operation_id": str(operation.id)},
    next_attempt_at=self.now() + START_RESULT_LEASE,
)
```

- [ ] Implement one private `_request(operation, phase)` helper. `stop` and
  `delete` use distinct idempotency keys, preserve existing timestamps, and
  call `make_oldest_pending_due` with the operation aggregate and current time
  after adding the latest phase event.
  If a legacy visible/terminal call has recording metadata but no operation,
  create a compatible operation first. Use `uncertain` when the old call may
  have attempted recording, and `not_started` only when local facts prove it
  never reached the connected recording boundary.

- [ ] Rerun the focused suite. Add a test that introspects every resulting
  recording payload and asserts its keys are exactly `{"operation_id"}`.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/services/test_recording_lifecycle_service.py \
  tests/services/test_outbox_service.py \
  tests/workers/test_post_call_outbox_handlers.py
```

Expected: all selected tests pass, including the processing-lease case.

- [ ] Commit lifecycle commands and outbox scheduling.

```bash
git add apps/api/app/repositories/call_repository.py apps/api/app/repositories/outbox_repository.py apps/api/app/services/outbox_service.py apps/api/app/services/recording_lifecycle_service.py apps/api/tests/services/test_outbox_service.py apps/api/tests/services/test_recording_lifecycle_service.py apps/api/tests/workers/test_post_call_outbox_handlers.py
git commit -m "feat: persist recording lifecycle intent"
```

---

## Task 4: Build non-exhausting recording reconciliation

**Produces:** A worker-safe reconciler for known, uncertain, stopped, deleted,
conflicting, and missing operations.

**Consumes:** Task 2 provider snapshots, the current S3 delete boundary, and
Task 3 lifecycle/outbox commands.

**Files:**

- Create: `apps/api/app/workers/jobs/recording_reconciliation.py`
- Create: `apps/api/tests/workers/test_recording_reconciliation.py`
- Modify: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/services/outbox_service.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- Modify: `apps/api/tests/workers/test_post_call_jobs.py`
- Modify: `apps/api/tests/services/test_safe_service_exceptions.py`
- Modify: `apps/api/tests/test_observability.py`

- [ ] Add a table-driven worker test matrix with these exact cases:

| Start/intent facts | Provider/storage result | Expected durable result |
| --- | --- | --- |
| stale `prepared` | no I/O | `not_started`, applicable event delivered |
| stale `starting` | no exact match | `uncertain`, non-exhausting retry |
| `started`, no stop | no I/O | start event delivered, operation retained |
| `started`, stop | known active then terminal | `provider_terminal_at`, object retained |
| `started`, delete | terminal then object deleted | operation removed |
| `not_started`, delete | missing object | operation removed |
| `uncertain`, one exact room/path match | provider returns identity | attach identity, never start again |
| `uncertain`, mismatched path only | no stop | safe mismatch, non-exhausting retry |
| `uncertain`, empty active list | no proof | non-exhausting retry |
| `uncertain`, multiple exact matches | stop every exact match | conflict retained, projection hidden, retry |
| `legacy_incomplete`, unknown ID | no room lookup possible | observable non-exhausting retry |
| missing operation | no I/O | idempotent success |
| storage transient failure | provider already terminal | non-exhausting retry |
| crash after operation deletion | handler retried | idempotent success |

- [ ] Add an I/O boundary test using a session-factory spy. It must fail if
  `list_room_egresses`, `ensure_not_running`, or `delete_object` is invoked while
  any session context is open.

```python
assert provider.calls == [
    ("ensure_not_running", "EG_exact"),
]
assert storage.calls == [
    ("delete_object", "calls/user-id/call-id.ogg"),
]
assert session_tracker.open_count_during_provider_calls == 0
```

- [ ] Run the new tests and observe import failure for the reconciliation
  module.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/workers/test_recording_reconciliation.py
```

- [ ] Implement a `RecordingReconciler` with short snapshot and persistence
  transactions around provider-free I/O.

```python
@dataclass(frozen=True)
class ReconciliationResult:
    outcome: Literal["complete", "retry"]
    error_code: str | None = None

class RecordingReconciler:
    def __init__(self, session_factory, provider, storage, *, now_provider):
        self.session_factory = session_factory
        self.provider = provider
        self.storage = storage
        self.now = now_provider

    async def reconcile(self, operation_id: UUID) -> ReconciliationResult:
        snapshot = await self._load_and_recover_stale_start(operation_id)
        if snapshot is None:
            return ReconciliationResult("complete")
        if snapshot.provider_egress_id is not None:
            return await self._reconcile_known(snapshot)
        if snapshot.start_state == "not_started":
            return await self._finish_definite_non_start(snapshot)
        return await self._reconcile_unknown(snapshot)
```

- [ ] In `_load_and_recover_stale_start`, use the injected clock and
  `START_RESULT_LEASE`: stale `prepared -> not_started`, stale
  `starting -> uncertain`. Never invoke the start method. Persist
  `last_reconciled_at` and bounded error codes in a fresh transaction.

- [ ] For a known egress, close the snapshot transaction, call
  `ensure_not_running` only when stop intent exists and terminality is not yet
  proven, then reopen a transaction following call-before-operation lock order
  to store `provider_terminal_at`. Failed, aborted, limit-reached, and complete
  statuses all count as non-running through the provider's existing
  `ensure_not_running` contract. Missing or uncertain provider identity remains
  retryable.

- [ ] For an unknown identity, call `list_room_egresses` only when room identity
  is complete. Filter by exact room and exact expected object path:

```python
exact = tuple(
    item
    for item in snapshots
    if item.room_name == operation.room_name
    and item.object_key == operation.expected_object_key
)
```

  One exact match is attached under a fresh lock; zero exact matches never prove
  non-start; multiple exact matches call `ensure_not_running` for every exact
  egress ID and retain `recording_identity_conflict`. Never stop a different
  object path and never project a conflicting recording onto the call.

- [ ] For deletion, call storage only after `provider_terminal_at` exists or
  start is definitively `not_started`. Treat `FileNotFoundError` and the storage
  provider's idempotent missing-object result as success. Persist
  `object_deleted_at`, then delete the private operation. The customer call
  remains tombstoned. A subsequent outbox retry sees the missing operation and
  succeeds.

- [ ] Register Task 3's `recording.reconcile` topic in default delivery. It is
  not part of call-ID binding because its aggregate is an operation UUID. Keep
  the existing `recording.stop` handler temporarily because the runtime cutover
  has not happened yet. The focused new handler validates topic, aggregate type,
  aggregate ID, and payload equality before invoking the reconciler.

```python
async def deliver_recording_reconcile(ctx: dict[str, Any], event: OutboxEvent) -> None:
    operation_id = validate_recording_reconcile_event(event)
    reconciler = build_recording_reconciler(ctx)
    result = await reconciler.reconcile(operation_id)
    if result.outcome == "retry":
        raise OutboxDeliveryError(
            result.error_code or "recording_unresolved",
            retryable=True,
            exhaustible=False,
        )
```

- [ ] Add only these bounded worker error codes:
  `recording_unresolved`, `recording_provider_unavailable`,
  `recording_storage_unavailable`, `recording_identity_mismatch`,
  `recording_identity_conflict`, and `recording_legacy_incomplete`. Map them to
  safe error classes. Confirm non-exhausting attempts reuse the existing maximum
  two-hour backoff after the listed delays.

- [ ] Register `recording.reconcile` alongside the temporary old
  `recording.stop` handler. Add an assertion that no new recording lifecycle
  code introduced in Tasks 1-4 produces `recording.stop`; existing call
  lifecycle producers remain unchanged until Task 5. The migration owns
  unfinished legacy queue conversion.

- [ ] Run worker regressions.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_recording_reconciliation.py \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_post_call_jobs.py \
  tests/services/test_safe_service_exceptions.py \
  tests/test_observability.py
```

Expected: all selected tests pass and handler/supported-topic sets are equal.
Both recording handlers exist at this intermediate compatibility checkpoint;
only the new lifecycle service produces `recording.reconcile`.

- [ ] Commit reconciliation.

```bash
git add apps/api/app/workers/jobs apps/api/app/services/outbox_service.py apps/api/tests/workers apps/api/tests/services/test_safe_service_exceptions.py apps/api/tests/test_observability.py
git commit -m "feat: reconcile recording cleanup without exhaustion"
```

---

## Task 5: Cut dispatch, call end, and call removal over atomically

**Produces:** Durable prepare-before-start, stop intent on every terminal path,
and immediate provider-free owner removal.

**Consumes:** Tasks 3 and 4 as one complete path; do not merge a partial cutover.

**Files:**

- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/services/call_lifecycle_service.py`
- Modify: `apps/api/app/services/call_history_service.py`
- Modify: `apps/api/app/services/recording_service.py`
- Modify: `apps/api/app/routers/calls.py`
- Modify: `apps/api/app/services/outbox_service.py`
- Modify: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_service.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`
- Modify: `apps/api/tests/integration/test_livekit_dispatch_concurrency.py`
- Modify: `apps/api/tests/integration/test_call_state_machine_concurrency.py`
- Modify: `apps/api/tests/calls/test_call_history_api.py`
- Modify: `apps/api/tests/calls/test_call_finalization_state_machine.py`
- Modify: `apps/api/tests/agent/test_call_completion.py`
- Modify: `apps/api/tests/integration/test_forwarding_verification_privacy.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- Modify: `apps/api/tests/test_observability.py`

- [ ] First rewrite integration expectations around customer-visible behavior:

  - connected call, prepared operation, and delayed start event are committed
    before the fake provider is entered;
  - provider receives the committed expected key;
  - a stop racing the two-minute lease makes the oldest event due immediately;
  - completion racing success leaves terminal call facts unchanged and persists
    stop intent;
  - deletion racing in-flight start returns `204`, purges content, and leaves
    operation cleanup durable;
  - late success after deletion updates only the operation;
  - no provider or storage double records a call during DELETE;
  - active deletion is `409`, unknown/cross-owner is `404`, first and repeated
    terminal deletion are `204`;
  - a visible terminal call may gain a valid late playback projection.
  - a prepare/commit database failure rolls back the call connection,
    operation, and outbox together and makes zero provider calls;
  - a local deletion transaction failure rolls back the cleanup intent,
    message purge, projection purge, and tombstone together;
  - an untyped provider exception is recorded as `unknown` using only a safe
    error class and never triggers another start.

```python
assert response.status_code == 204
assert provider.calls_during_delete == []
assert storage.calls_during_delete == []
assert deleted_call.deleted_at is not None
assert deleted_call.caller_number is None
assert deleted_call.summary_text is None
assert deleted_call.summary_data is None
assert deleted_call.recording_object_key is None
assert deleted_call.recording_egress_id is None
assert deleted_call.recording_url is None
assert operation.stop_requested_at is not None
assert operation.delete_requested_at is not None
```

- [ ] Run the focused dispatch/removal set and observe failures from old
  synchronous deletion and `recording.stop` expectations.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/livekit/test_durable_dispatch_service.py \
  tests/livekit/test_dispatch_service.py \
  tests/calls/test_call_history_api.py \
  tests/calls/test_call_finalization_state_machine.py
```

- [ ] Change agent join to this transaction sequence:

```python
connected_call = await self.call_repository.connect_if_pending(call_id=call.id)
if connected_call is None:
    await self.session.commit()
    return DispatchJoinResult("ignored")
operation = await self.recording_lifecycle.prepare_start(connected_call)
await self.session.commit()

claim = await self.recording_lifecycle.begin_start(operation.id)
await self.session.commit()
if claim is None:
    await self._best_effort_outbox_wakeup()
    return DispatchJoinResult("connected", str(connected_call.id))
```

  Call `start_room_recording(room_name=claim.room_name,
  object_key=claim.expected_object_key)` after the session transaction is
  committed, with no active transaction or row lock. In a new short
  transaction, record success or typed error, commit, accelerate reconciliation,
  and best-effort wake the outbox. Delete the late best-effort stop/fallback
  branch entirely; the private operation is now its durable replacement.

- [ ] Inject `RecordingLifecycleService` into `CallLifecycleService` and replace
  `_add_recording_stop_intent` with `request_stop(call)` on both agent and SIP
  terminal transitions. Remove the extra finalization-time `recording.stop`
  producer. Finalization remains provider-free and still creates summary and
  balance/routing events.

- [ ] Change `CallHistoryService.delete_call` to lock the owner-scoped call
  including tombstones, return idempotently for an existing tombstone, reject
  active states, call `request_deletion(call)`, delete messages, purge customer
  content, and commit once. Do not catch a provider exception because none can
  occur in this request.

- [ ] Remove `CallDeleteRetryableError`, the router's
  `call_delete_retryable`/`503` mapping, `RecordingDeleteRetryableError`,
  `RecordingEgressStopper`, and `RecordingService.delete_recording`.
  `RecordingService` remains a small playback URL service only.

- [ ] Update the call-history dependency wiring to inject both the playback-only
  `RecordingService` and the session-bound `RecordingLifecycleService` for read
  endpoints. Give DELETE a provider-free dependency that constructs the same
  history service with recording playback disabled; it must not initialize a
  storage or LiveKit client.

- [ ] Update all old `recording.stop` assertions to operation-scoped
  `recording.reconcile` assertions. Forwarding-verification calls must still
  create no recording operation, no transcript, no summary, no recording, and
  no usage charge.

- [ ] After every producer uses the operation aggregate, remove
  `recording.stop` from supported/reference topics, call-ID binding, default
  handlers, safe observability topics, and active tests. Delivered legacy rows
  stay inert in the database; revision 0014 replaces unfinished rows before
  this code is run. Also remove the Task 2 wrapper compatibility arguments so
  `LiveKitRecordingService.start_room_recording` accepts only committed
  `room_name` and `object_key`.

- [ ] Run the complete changed behavior set.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/livekit/test_durable_dispatch_service.py \
  tests/livekit/test_dispatch_service.py \
  tests/integration/test_livekit_dispatch_concurrency.py \
  tests/integration/test_call_state_machine_concurrency.py \
  tests/integration/test_forwarding_verification_privacy.py \
  tests/calls/test_call_history_api.py \
  tests/calls/test_call_finalization_state_machine.py \
  tests/agent/test_call_completion.py
```

Expected: all selected tests pass and a repository search shows no active
`recording.stop` producer or synchronous recording deletion method.

```bash
rg -n 'topic="recording\.stop"|delete_recording|CallDeleteRetryableError' apps/api/app
```

Expected: no matches.

- [ ] Commit the atomic runtime cutover.

```bash
git add apps/api/app/services apps/api/app/routers/calls.py apps/api/app/workers/jobs apps/api/app/core/observability.py apps/api/tests/livekit apps/api/tests/integration apps/api/tests/calls apps/api/tests/agent/test_call_completion.py apps/api/tests/workers apps/api/tests/test_observability.py
git commit -m "feat: make call recording lifecycle durable"
```

---

## Task 6: Accept sanitized signed LiveKit egress events

**Produces:** Idempotent database-only recovery from `egress_started`,
`egress_updated`, and `egress_ended` webhooks.

**Consumes:** The existing verified LiveKit webhook receiver and Task 3 event
fact boundary.

**Files:**

- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/app/services/recording_lifecycle_service.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_webhook.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`

- [ ] Add converter tests for dict and SDK-like objects. Egress events expose
  only external event ID/type, egress ID, room name, numeric status, and one file
  path. Participant events keep their current sanitized shape. Generic webhook
  persistence must still store `{}`.

```python
assert convert_livekit_event(raw) == {
    "id": "EV_egress",
    "event": "egress_started",
    "egress": {
        "egress_id": "EG_exact",
        "room_name": "room-owned",
        "status": 1,
        "object_key": "calls/user-id/call-id.ogg",
    },
}
assert stored_webhook.payload == {}
```

- [ ] Add signed webhook behavior tests for exact match, duplicate external ID,
  missing required identity, mismatched room, mismatched path, conflicting
  egress ID, terminal event, and outbox wakeup failure. Assert provider and
  storage fakes receive no calls in every webhook test.

- [ ] Run the webhook tests and observe failures because egress facts are
  currently discarded.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/livekit/test_durable_dispatch_webhook.py \
  tests/livekit/test_dispatch_service.py
```

- [ ] Extend `convert_livekit_event` only for the three allow-listed egress
  event types. Reuse the provider's normalized file-path extraction helper so
  webhook and list reconciliation cannot disagree about an output path. Reject
  multiple conflicting paths by setting the sanitized path to `None`.

- [ ] In the verified webhook transaction, record the generic event first. For
  a new egress event, build `RecordingEgressEventFact`, call
  `accept_egress_event`, and commit. Exact events may attach an unknown identity,
  update the visible projection only when the call is not tombstoned, mark
  terminality for an exact known ID, and accelerate pending reconciliation.
  Mismatch/conflict results do not expose playback data.

  An unknown operation may attach an egress ID only when room and object path
  both match exactly. An operation with an already-known egress ID may accept an
  event with no output path when room and ID match; if a path is present it must
  still match. Missing room/egress identity or any present mismatched path is a
  safe non-mutation.

- [ ] After commit, best-effort enqueue `outbox_delivery_job`. A queue outage
  must still return `202` because the SQL event is authoritative. No provider or
  storage call is permitted in the webhook request.

- [ ] Rerun webhook and privacy tests.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/livekit/test_durable_dispatch_webhook.py \
  tests/livekit/test_dispatch_service.py \
  tests/integration/test_forwarding_verification_privacy.py
```

Expected: all selected tests pass; duplicates remain idempotent and stored
generic payloads remain empty objects.

- [ ] Commit webhook reconciliation.

```bash
git add apps/api/app/webhooks/livekit.py apps/api/app/services/recording_lifecycle_service.py apps/api/tests/livekit apps/api/tests/integration/test_forwarding_verification_privacy.py
git commit -m "feat: reconcile signed recording egress events"
```

---

## Task 7: Prove races, privacy, and low-cardinality observability

**Produces:** PostgreSQL concurrency evidence and operational signals without
customer/provider values as labels.

**Consumes:** The fully integrated lifecycle from Tasks 1-6.

**Files:**

- Create: `apps/api/tests/integration/test_recording_egress_concurrency.py`
- Modify: `apps/api/app/repositories/recording_egress_operation_repository.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/tests/test_observability.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/api/tests/test_integrity_models.py`

- [ ] Add PostgreSQL-only concurrent-session tests with barriers for:

  1. end before start claim: the claim loses and provider start count is zero;
  2. end after start claim: one start occurs and stop intent survives;
  3. deletion while provider start is in flight: DELETE commits, late success
     stores only private identity, and cleanup remains due;
  4. simultaneous success and deletion: call remains tombstoned, projection
     remains empty, operation retains identity;
  5. duplicate webhook and direct success: one provider ID and one visible
     projection at most;
  6. two exact matches: neither can become the playback projection;
  7. concurrent repeated deletion: one operation and one delete idempotency key;
  8. an active outbox processing lease is not stolen by stop acceleration;
  9. two stale listing workers restoring exact evidence after reclaimed cleanup:
     the call lock serializes restoration, one operation and one recovery event
     remain, the stored/observed identity union is stopped, and a later durable
     call tombstone restores deletion intent without replacing an earlier stop.

```python
assert provider.start_count == 1
assert deleted_call.deleted_at is not None
assert deleted_call.recording_egress_id is None
assert operation.provider_egress_id == "EG_late"
assert operation.stop_requested_at is not None
assert operation.delete_requested_at is not None
assert reconcile_event.status in {"pending", "processing"}
```

- [ ] Add `RecordingOperationObservabilitySnapshot` with counts by the five
  states, oldest unresolved age, pending stop count/age, and pending deletion
  count/age. Keep repository queries label-free and return zeroes for empty
  tables.

- [ ] Add allow-listed telemetry methods for:

  - operation state gauges;
  - oldest unresolved, stop, and deletion age gauges;
  - reconciliation result counter with safe result categories;
  - webhook mismatch counter;
  - multiple-exact-match conflict counter.

  Labels may be only approved state/result/category values. Add
  `list_recording_egresses` to the LiveKit provider-operation allow-list and
  assert `recording.reconcile` is the sole active recording topic in the
  outbox-topic allow-list.

- [ ] Extend `outbox_reconciliation_job` to collect both the outbox snapshot and
  recording-operation snapshot in independent failure-isolated blocks. A metric
  exporter error must not fail delivery.

- [ ] Add privacy tests that seed distinctive forbidden values and use `caplog`
  plus a fake metric exporter to prove none appear in log text, attributes, or
  metric labels. Scan outbox payloads and persisted private-operation columns to
  prove only approved coordination fields are stored.

- [ ] Update deployment-readiness tests to require the new model import,
  migration head, supported topic/handler, provider list capability, signed
  webhook behavior, and valid private storage configuration. Keep all current
  credential/configuration checks fail-closed. Do not add or require
  `LIVEKIT_EVAL_MODEL`; that variable is only for separately opted-in behavioral
  evaluations, not runtime recording coordination.

- [ ] Run SQLite observability/readiness tests.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_observability.py \
  tests/test_deployment_readiness.py \
  tests/test_integrity_models.py
```

- [ ] Run the authoritative isolated PostgreSQL/Redis integration slice using
  the repository's disposable test-compose environment.

```bash
set -e
cd /home/mo/code/ai/bmad-opevo
cleanup_recording_pg() {
  COMPOSE_PROJECT_NAME=opevo-recording-pg POSTGRES_PORT=55434 REDIS_PORT=56381 docker compose -f compose.dev.yaml down --volumes --remove-orphans
}
trap cleanup_recording_pg EXIT
COMPOSE_PROJECT_NAME=opevo-recording-pg POSTGRES_PORT=55434 REDIS_PORT=56381 docker compose -f compose.dev.yaml up -d --wait postgres redis
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call TEST_REDIS_URL=redis://127.0.0.1:56381/0 UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/integration/test_recording_egress_concurrency.py tests/test_recording_egress_operation_migration.py
cd ../..
cleanup_recording_pg
trap - EXIT
```

Expected: zero failures and zero skips. The disposable Compose project is
removed even if pytest fails.

- [ ] Commit concurrency and observability evidence.

```bash
git add apps/api/app/repositories/recording_egress_operation_repository.py apps/api/app/core/observability.py apps/api/app/workers/jobs/outbox_delivery.py apps/api/tests/integration/test_recording_egress_concurrency.py apps/api/tests/test_observability.py apps/api/tests/test_deployment_readiness.py apps/api/tests/test_integrity_models.py
git commit -m "test: prove recording reconciliation races"
```

---

## Task 8: Update product truth and run every local gate

**Produces:** Documentation that accurately distinguishes implemented local
readiness from production certification, plus fresh full-regression evidence.

**Consumes:** All implementation and test evidence from Tasks 1-7.

**Files:**

- Modify: `README.md`
- Modify: `docs/PROJECT_STATUS.md`
- Modify: `docs/architecture/integration-endpoints.md`
- Modify: `docs/engineering/2026-07-18-production-readiness-handoff.md`
- Modify: `docs/superpowers/specs/2026-07-19-recording-egress-synchronization-design.md` only if implementation names differ without changing approved behavior

- [ ] Update README and architecture docs to say:

  - Opevo is production-oriented and locally verified, not
    production-certified;
  - normal calls commit a private recording operation before provider start;
  - call completion requests `recording.reconcile` regardless of whether an
    egress ID is known;
  - owner removal immediately purges local customer content and returns `204`;
  - provider stop and object deletion are asynchronous and non-exhausting;
  - original audio remains for visible calls until owner removal;
  - automatic 30-day retention, account deletion/export, legal approval, cloud
    deployment, and provider certification remain unfinished.

- [ ] Update `docs/PROJECT_STATUS.md` from the old synchronous deletion and
  `recording.stop` wording to evidence-backed durable reconciliation. Remove the
  recording start/delete race from remaining blockers only after Task 7 passes.
  Do not remove unrelated compliance, staging, load, restore, accessibility,
  realtime, or cloud blockers.

- [ ] Update the durable handoff with commit range, exact test counts, any known
  warning, and the next unimplemented production gap. Do not claim provider
  certification from fakes or local tests.

- [ ] Scan implementation and docs for stale contracts and unsafe placeholders.

```bash
cd /home/mo/code/ai/bmad-opevo
rg -n 'recording\.stop|call_delete_retryable|delete_recording|synchronous.*recording' apps/api/app README.md docs/PROJECT_STATUS.md docs/architecture/integration-endpoints.md
rg -n 'TO''DO|TB''D|FIX''ME|implement lat''er|appropriate err''or' apps/api/app apps/api/tests README.md docs/PROJECT_STATUS.md docs/architecture/integration-endpoints.md
```

Expected: the first command has no active-contract matches; the second has no
new recording-slice placeholders. Historical design documents may retain old
terms only where explicitly marked superseded.

- [ ] Run API static checks and the complete SQLite suite.

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: Ruff and mypy exit zero; all API tests pass with only the explicitly
marked PostgreSQL-only skips and the already-known upstream warning.

- [ ] Run the complete isolated PostgreSQL 17/Redis 7 API suite.

```bash
set -e
cd /home/mo/code/ai/bmad-opevo
cleanup_recording_pg() {
  COMPOSE_PROJECT_NAME=opevo-recording-pg POSTGRES_PORT=55434 REDIS_PORT=56381 docker compose -f compose.dev.yaml down --volumes --remove-orphans
}
trap cleanup_recording_pg EXIT
COMPOSE_PROJECT_NAME=opevo-recording-pg POSTGRES_PORT=55434 REDIS_PORT=56381 docker compose -f compose.dev.yaml up -d --wait postgres redis
cd apps/api
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call TEST_REDIS_URL=redis://127.0.0.1:56381/0 UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
cd ../..
cleanup_recording_pg
trap - EXIT
```

Expected: every API test passes with zero skips and cleanup always runs. Record
the actual count in the handoff; do not reuse an earlier count.

- [ ] Run agent gates. Credentialed LiveKit evaluations remain deselected and
  are not production evidence.

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q -m 'not livekit_eval'
```

- [ ] Run web gates under the project-supported Node version, sequentially to
  avoid false timeouts from CPU contention.

```bash
cd apps/web
export PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
node --version
npm run check
npm run typecheck
npm run test:ci
npm run build
```

Expected: Node reports `v22.23.1`; formatting/lint, TypeScript, Vitest, and
production build all pass.

- [ ] Run provider-free end-to-end activation and restart/resume acceptance.

```bash
cd /home/mo/code/ai/bmad-opevo
bash scripts/run-local-e2e.sh
```

Expected: activation, forwarding verification, go-live, missed-call handling,
summary/outcomes, original-audio display, call removal, and restart/resume pass
using local fake providers. Confirm disposable containers, networks, and volumes
are removed afterward.

- [ ] Run final repository safety gates.

```bash
cd /home/mo/code/ai/bmad-opevo
bash -n scripts/*.sh
git diff --check
git status --short
```

Expected: shell syntax and whitespace checks pass; status contains only the
intended documentation changes for this task before the final commit.

- [ ] Commit documentation and verified status.

```bash
git add README.md docs/PROJECT_STATUS.md docs/architecture/integration-endpoints.md docs/engineering/2026-07-18-production-readiness-handoff.md docs/superpowers/specs/2026-07-19-recording-egress-synchronization-design.md
git commit -m "docs: record durable recording readiness evidence"
```

- [ ] Run a final post-commit check and preserve the exact evidence in the
  handoff.

```bash
git status --short --branch
git log --oneline --decorate -10
```

Expected: clean local branch, no push or deployment, and a commit history that
maps one-to-one to the eight tasks above.

## Acceptance traceability

| Approved acceptance criterion | Implemented/proven by |
| --- | --- |
| Durable identity before provider start | Tasks 1, 3, 5, 7 |
| DELETE never waits on LiveKit/S3 | Tasks 3, 5, 7 |
| Immediate local content inaccessibility | Tasks 5, 7, 8 |
| Late/uncertain start retains cleanup intent | Tasks 3, 4, 5, 7 |
| No second start after ambiguity | Tasks 3, 4, 5, 7 |
| Non-exhausting, observable cleanup | Tasks 4, 7 |
| Operation removed only after safe cleanup | Tasks 4, 7 |
| Visible playback/finalization preserved | Tasks 5, 6, 8 |
| PostgreSQL races and all regressions pass | Tasks 7, 8 |
| Production-oriented, not certified wording | Task 8 |
