# Missing Recording Operation Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve durable, retryable cleanup authority when an exact live
recording egress is discovered after a reclaimed worker removed its operation.

**Architecture:** Extend the singleton attachment and multiple-match conflict
seams so a missing operation is not interpreted as harmless completion after
exact provider evidence. Under the existing call-first SQL lock, restore or
merge one sticky conflict operation and one idempotent reconciliation event,
commit, then stop the union of durable and observed identities outside SQL.

**Tech Stack:** Python 3.12, FastAPI service layer, SQLAlchemy async ORM,
pytest/AnyIO, Ruff, mypy, LiveKit provider boundary.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-19-missing-recording-operation-recovery-design.md` exactly.
- Use strict RED–GREEN–REFACTOR: no production edit before the new failing test
  is observed.
- Keep the existing six bounded reconciliation error codes; add no new code.
- Keep every LiveKit and storage call outside every SQL session context.
- Lock the call before the recording operation for every mutation.
- Commit restored authority and its fresh outbox event before the first provider
  stop attempt.
- Preserve sticky conflict; never perform storage cleanup or remove a conflicted
  operation.
- Do not implement Task 5 runtime cutover or Task 6 webhook behavior.
- Do not start or contact PostgreSQL, Redis, LiveKit, storage, credentials, or
  any external account. Use the provider-free test configuration.

---

### Task 1: Restore durable authority after operation removal

**Files:**

- Modify: `apps/api/tests/workers/test_recording_reconciliation.py`
- Modify: `apps/api/app/workers/jobs/recording_reconciliation.py`
- Create: `.superpowers/sdd/task-4-missing-operation-fix-report.md` (ignored evidence)

**Interfaces:**

- Consumes: `_OperationSnapshot`, `RecordingEgressSnapshot`,
  `RecordingEgressOperation`, `OutboxService.add(topic, aggregate_type,
  aggregate_id, idempotency_key, payload, next_attempt_at)`,
  `RECORDING_AGGREGATE_TYPE`, and the current
  `RecordingReconciler._attach_exact_identity(...)` singleton seam.
- Produces:
  `RecordingReconciler._restore_or_merge_missing_conflict(session, snapshot,
  recovered_provider_id) -> tuple[_PersistenceStatus, _OperationSnapshot |
  None]`, a durable sticky-conflict result usable by both exact-evidence paths
  and the existing `_stop_conflicting_identities` path. A singleton supplies
  its trusted ID; multiple matches supply `None` and remain uncertain.

- [ ] **Step 1: Add a fake that runs the reclaimed cleanup worker during W1's listing**

Add a test provider beside the existing concurrency fakes. Its listing method
must commit the direct `not_started` result, run W2 all the way through storage
deletion and operation removal, assert W2 completed, then return a singleton
exact snapshot to W1:

```python
from sqlalchemy import select

from app.models.outbox_event import OutboxEvent
from app.services.recording_lifecycle_service import RECORDING_AGGREGATE_TYPE


class RemovedOperationDuringListingProvider(ConflictObservingProvider):
    def __init__(
        self,
        tracker: TrackingSessionFactory,
        session_factory,
        operation_id: UUID,
        *,
        exact: RecordingEgressSnapshot,
        cleanup_storage: FakeStorage,
        ensure_failures: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(
            tracker,
            session_factory,
            operation_id,
            snapshots=(exact,),
            ensure_failures=ensure_failures,
        )
        self.cleanup_storage = cleanup_storage

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        self._record("list_room_egresses", room_name)
        async with self.session_factory() as session:
            changed = await RecordingLifecycleService(
                session,
                now_provider=lambda: NOW,
            ).record_start_error(
                self.operation_id,
                outcome="not_started",
                error_code="validation",
            )
            assert changed is not None
            await session.commit()

        cleanup_result = await RecordingReconciler(
            self.tracker,
            FakeProvider(self.tracker),
            self.cleanup_storage,
            now_provider=lambda: NOW + timedelta(seconds=1),
        ).reconcile(self.operation_id)
        assert cleanup_result == ReconciliationResult("complete")
        async with self.session_factory() as session:
            assert await session.get(
                RecordingEgressOperation,
                self.operation_id,
            ) is None
        return self.snapshots
```

- [ ] **Step 2: Write the full lease-reclaim RED test**

Create a `starting` operation with stop and deletion intent, a current
`start_attempted_at`, and a tombstoned call projection. Reconcile W1 through
the fake above and assert the approved outcome:

```python
@pytest.mark.anyio
async def test_exact_evidence_restores_authority_after_reclaimed_cleanup(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="removed during exact listing",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()

    exact = RecordingEgressSnapshot(
        egress_id="EG_exact",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    cleanup_storage = FakeStorage(tracker)
    outer_storage = FakeStorage(tracker)
    provider = RemovedOperationDuringListingProvider(
        tracker,
        base_factory,
        operation_id,
        exact=exact,
        cleanup_storage=cleanup_storage,
    )

    result = await RecordingReconciler(
        tracker,
        provider,
        outer_storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    ).reconcile(operation_id)

    assert result == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert cleanup_storage.calls == [("delete_object", OBJECT_KEY)]
    assert outer_storage.calls == []

    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.start_state == "started"
    assert stored.provider_egress_id == "EG_exact"
    assert stored.stop_requested_at is not None
    assert stored.delete_requested_at is not None
    assert stored.provider_terminal_at is None
    assert stored.object_deleted_at is None
    assert stored.last_error_code == "recording_identity_conflict"
    call = await db_session.get(Call, call_id)
    assert call is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
    event = await db_session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.idempotency_key
            == f"recording.reconcile:{operation_id}:missing-operation-conflict"
        )
    )
    assert event is not None
    assert event.topic == "recording.reconcile"
    assert event.aggregate_type == RECORDING_AGGREGATE_TYPE
    assert event.aggregate_id == operation_id
    assert event.payload == {"operation_id": str(operation_id)}
    assert event.status == "pending"
    assert provider.stop_observations == [
        ("EG_exact", "recording_identity_conflict", True)
    ]
```

Query the outbox by the exact idempotency key
`recording.reconcile:{operation_id}:missing-operation-conflict` and assert its
topic, aggregate type, aggregate ID, reference-only payload, pending status, and
due time.

- [ ] **Step 3: Run the new test and verify RED**

Run outside the restricted sandbox if the known stream-fd failure appears:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache \
  /home/mo/code/ai/bmad-opevo/apps/api/.venv/bin/python -m pytest -q \
  tests/workers/test_recording_reconciliation.py \
  -k exact_evidence_restores_authority_after_reclaimed_cleanup
```

Expected: one assertion failure showing W1 returns `complete`, makes no
`ensure_not_running` call, leaves the operation missing, and creates no recovery
event. Record the exact RED output in the fix report.

- [ ] **Step 4: Implement missing-operation restore-or-merge under the call lock**

Import `OutboxService` and `RECORDING_AGGREGATE_TYPE` inward from the lifecycle
service. Refactor `_attach_exact_identity` so its initial operation miss locks
the call by `snapshot.call_id`, rechecks the operation, and delegates to this
bounded helper within the same transaction:

```python
async def _restore_or_merge_missing_conflict(
    self,
    session: AsyncSession,
    snapshot: _OperationSnapshot,
    recovered_provider_id: str | None,
) -> tuple[_PersistenceStatus, _OperationSnapshot | None]:
    calls = CallRepository(session)
    operations = RecordingEgressOperationRepository(session)
    call = await calls.get_by_id_including_deleted_for_update(snapshot.call_id)
    if call is None:
        await session.rollback()
        return "changed", None

    operation = await operations.get_by_id_for_update(snapshot.operation_id)
    if operation is None:
        operation = await operations.add(
            RecordingEgressOperation(
                id=snapshot.operation_id,
                call_id=snapshot.call_id,
                room_name=snapshot.room_name,
                legacy_incomplete=snapshot.legacy_incomplete,
                expected_object_key=snapshot.expected_object_key,
                provider_egress_id=recovered_provider_id,
                start_state=(
                    "started"
                    if recovered_provider_id is not None
                    else "uncertain"
                ),
                stop_requested_at=snapshot.stop_requested_at,
                delete_requested_at=snapshot.delete_requested_at,
                last_reconciled_at=_as_utc(self.now()),
                last_error_code=RECORDING_IDENTITY_CONFLICT_CODE,
            )
        )
    elif (
        operation.call_id != snapshot.call_id
        or operation.room_name != snapshot.room_name
        or operation.legacy_incomplete != snapshot.legacy_incomplete
        or operation.expected_object_key != snapshot.expected_object_key
    ):
        await session.rollback()
        return "changed", None
    else:
        if operation.provider_egress_id is None:
            operation.start_state = "uncertain"
        operation.last_reconciled_at = _as_utc(self.now())
        operation.last_error_code = RECORDING_IDENTITY_CONFLICT_CODE

    self._hide_playback_projection(call)
    await OutboxService(session, now_provider=self.now).add(
        topic="recording.reconcile",
        aggregate_type=RECORDING_AGGREGATE_TYPE,
        aggregate_id=operation.id,
        idempotency_key=(
            f"recording.reconcile:{operation.id}:missing-operation-conflict"
        ),
        payload={"operation_id": str(operation.id)},
        next_attempt_at=_as_utc(self.now()),
    )
    await session.flush()
    refreshed = self._snapshot(call, operation)
    await session.commit()
    return "conflict", refreshed
```

Keep provider I/O in the existing callers: a singleton passes its exact ID as
`recovered_provider_id`, while the multiple-match path passes `None`. A
`"conflict"` status with the refreshed snapshot goes to
`_stop_conflicting_identities(refreshed, exact_ids)` only after the helper's
session context exits. If the call or immutable operation identity is unsafe,
return `"changed"` and perform no provider/storage I/O.

- [ ] **Step 5: Run the lease-reclaim test and verify GREEN**

Run the Step 3 command again.

Expected: `1 passed`; the stop observer confirms the operation, sticky marker,
hidden projection, and recovery event are already committed when provider I/O
starts.

- [ ] **Step 6: Add stop-failure durability and idempotent restore tests**

Add three focused tests. The first two are shown below. The third repeats the
full removal-inside-listing schedule with `EG_A` and `EG_B`, and asserts the
restored operation is `uncertain` with no provider ID, the recovery event and
projection purge commit before provider I/O, and both distinct IDs are stopped
even if the first stop fails:

```python
assert result == ReconciliationResult(
    "retry",
    "recording_identity_conflict",
)
assert provider.calls == [
    ("list_room_egresses", "room-owned"),
    ("ensure_not_running", "EG_A"),
    ("ensure_not_running", "EG_B"),
]
assert stored.start_state == "uncertain"
assert stored.provider_egress_id is None
assert stored.last_error_code == "recording_identity_conflict"
assert recovery_event.status == "pending"
assert outer_storage.calls == []
```

Then add the stop-failure durability and two-restorer tests:

```python
@pytest.mark.anyio
async def test_restored_authority_survives_stop_failure_and_later_retry(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="removed operation stop failure",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    _, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()
    exact = RecordingEgressSnapshot(
        egress_id="EG_exact",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    cleanup_storage = FakeStorage(tracker)
    outer_storage = FakeStorage(tracker)
    first_provider = RemovedOperationDuringListingProvider(
        tracker,
        base_factory,
        operation_id,
        exact=exact,
        cleanup_storage=cleanup_storage,
        ensure_failures=frozenset({"EG_exact"}),
    )
    first = await RecordingReconciler(
        tracker,
        first_provider,
        outer_storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    ).reconcile(operation_id)
    assert first == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert cleanup_storage.calls == [("delete_object", OBJECT_KEY)]
    assert outer_storage.calls == []
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.last_error_code == "recording_identity_conflict"

    second_provider = FakeProvider(tracker, snapshots=(exact,))
    second = await RecordingReconciler(
        tracker,
        second_provider,
        outer_storage,
        now_provider=lambda: NOW + timedelta(seconds=3),
    ).reconcile(operation_id)
    assert second == ReconciliationResult(
        "retry",
        "recording_identity_conflict",
    )
    assert second_provider.calls == [
        ("list_room_egresses", "room-owned"),
        ("ensure_not_running", "EG_exact"),
    ]
    assert outer_storage.calls == []
    db_session.expire_all()
    stored = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored is not None
    assert stored.last_error_code == "recording_identity_conflict"


@pytest.mark.anyio
async def test_two_stale_restorers_merge_one_operation_and_recovery_event(
    db_session: AsyncSession,
    active_user,
) -> None:
    case = MatrixCase(
        name="two removed operation restorers",
        start_state="starting",
        stop_requested=True,
        delete_requested=True,
    )
    call_id, operation_id = await _persist_operation(
        db_session,
        user_id=active_user.id,
        case=case,
    )
    operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert operation is not None
    operation.start_attempted_at = NOW
    await db_session.commit()
    base_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    tracker = TrackingSessionFactory(base_factory)
    first_provider = FakeProvider(tracker)
    second_provider = FakeProvider(tracker)
    storage = FakeStorage(tracker)
    first_reconciler = RecordingReconciler(
        tracker,
        first_provider,
        storage,
        now_provider=lambda: NOW + timedelta(seconds=2),
    )
    second_reconciler = RecordingReconciler(
        tracker,
        second_provider,
        storage,
        now_provider=lambda: NOW + timedelta(seconds=3),
    )
    stale_snapshot = await first_reconciler._load_and_recover_stale_start(
        operation_id
    )
    assert stale_snapshot is not None
    async with base_factory() as session:
        changed = await RecordingLifecycleService(
            session,
            now_provider=lambda: NOW,
        ).record_start_error(
            operation_id,
            outcome="not_started",
            error_code="validation",
        )
        assert changed is not None
        await session.commit()
    cleanup = await RecordingReconciler(
        tracker,
        FakeProvider(tracker),
        storage,
        now_provider=lambda: NOW + timedelta(seconds=1),
    ).reconcile(operation_id)
    assert cleanup == ReconciliationResult("complete")
    exact_a = RecordingEgressSnapshot(
        egress_id="EG_A",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    exact_b = RecordingEgressSnapshot(
        egress_id="EG_B",
        room_name="room-owned",
        status=1,
        object_key=OBJECT_KEY,
    )
    first_status, first_snapshot = await first_reconciler._attach_exact_identity(
        stale_snapshot,
        exact_a,
    )
    second_status, second_snapshot = (
        await second_reconciler._attach_exact_identity(stale_snapshot, exact_b)
    )
    assert (first_status, second_status) == ("conflict", "conflict")
    assert first_snapshot is not None
    assert second_snapshot is not None
    await first_reconciler._stop_conflicting_identities(
        first_snapshot,
        (exact_a.egress_id,),
    )
    await second_reconciler._stop_conflicting_identities(
        second_snapshot,
        (exact_b.egress_id,),
    )

    db_session.expire_all()
    operations = list(
        await db_session.scalars(
            select(RecordingEgressOperation).where(
                RecordingEgressOperation.call_id == call_id
            )
        )
    )
    events = list(
        await db_session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == f"recording.reconcile:{operation_id}:missing-operation-conflict"
            )
        )
    )
    assert len(operations) == 1
    assert len(events) == 1
    assert operations[0].last_error_code == "recording_identity_conflict"
    assert first_provider.calls == [("ensure_not_running", "EG_A")]
    assert second_provider.calls == [
        ("ensure_not_running", "EG_A"),
        ("ensure_not_running", "EG_B"),
    ]
```

The second test may exercise `_attach_exact_identity` directly because it is a
unit test for the post-listing persistence seam. It must close each transaction
before invoking the provider stop path and must prove the stable idempotency key
does not create duplicates or raise a uniqueness error.

- [ ] **Step 7: Verify the new race set and complete Task 4 files**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache \
  /home/mo/code/ai/bmad-opevo/apps/api/.venv/bin/python -m pytest -q \
  tests/services/test_recording_lifecycle_service.py \
  tests/workers/test_recording_reconciliation.py
```

Expected: every test passes, including the original 14-case matrix and all
sticky-conflict races. Record the exact count and duration.

- [ ] **Step 8: Run prescribed worker and static regressions**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache \
  /home/mo/code/ai/bmad-opevo/apps/api/.venv/bin/python -m pytest -q \
  tests/workers/test_recording_reconciliation.py \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/workers/test_post_call_jobs.py \
  tests/services/test_safe_service_exceptions.py \
  tests/test_observability.py
/home/mo/code/ai/bmad-opevo/apps/api/.venv/bin/ruff check app tests
/home/mo/code/ai/bmad-opevo/apps/api/.venv/bin/mypy app
git diff --check
```

Expected: all tests and static checks pass. Attribute, but do not modify, the
unchanged withdrawn Starlette/httpx baseline warning.

- [ ] **Step 9: Run the definitive provider-free API suite**

```bash
cd apps/api
env -u TEST_DATABASE_URL \
  APP_ENV=test \
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test \
  REDIS_URL=redis://127.0.0.1:6379/0 \
  PYTHONPATH=. UV_CACHE_DIR=/tmp/uv-cache \
  /home/mo/code/ai/bmad-opevo/apps/api/.venv/bin/python -m pytest -q
```

Expected: exit `0`; infrastructure-gated PostgreSQL tests remain skipped and no
PostgreSQL, Redis, LiveKit, storage, or credentials are contacted.

- [ ] **Step 10: Report, self-review, and commit the bounded repair**

Write `.superpowers/sdd/task-4-missing-operation-fix-report.md` with the RED,
GREEN, regression, static, and full-suite commands/results. Re-read the design,
this plan, `.superpowers/sdd/task-4-rereview.md`, and the final diff. Confirm no
Task 5/6 behavior entered the range.

```bash
git add \
  apps/api/app/workers/jobs/recording_reconciliation.py \
  apps/api/tests/workers/test_recording_reconciliation.py
git commit -m "fix: recover removed recording cleanup authority"
```

Expected: a narrow commit, clean worktree, no push or deployment. Generate a
new immutable review package from `868ffeb` through the new HEAD and require a
fresh reviewer to return `Spec: ✅` and `Quality: Approved` with no Critical,
Important, or Minor findings before Task 4 is accepted.
