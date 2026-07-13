import asyncio
from collections.abc import Callable
from typing import cast

import pytest
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import Base
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.repositories.usage_repository import UsageRepository
from app.repositories.webhook_event_repository import WebhookEventRepository


def _constraint_names(model: type) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
        and constraint.name is not None
    }


def _indexes(model: type) -> dict[str, Index]:
    return {index.name: index for index in model.__table__.indexes}


def test_models_expose_exact_integrity_constraint_names() -> None:
    assert "uq_webhook_events_provider_external_event_id" in _constraint_names(WebhookEvent)
    assert "uq_call_messages_call_sequence" in _constraint_names(CallMessage)
    assert "uq_subscriptions_user_id" in _constraint_names(Subscription)
    assert "ck_subscriptions_allocated_minutes_nonnegative" in _constraint_names(Subscription)
    assert "ck_subscriptions_status_allowed" in _constraint_names(Subscription)
    assert "ck_subscriptions_plan_tier_allowed" in _constraint_names(Subscription)
    assert "ck_calls_duration_seconds_nonnegative" in _constraint_names(Call)
    assert "ck_calls_minutes_charged_nonnegative" in _constraint_names(Call)

    usage_indexes = _indexes(UsageLedger)
    assert usage_indexes["uq_usage_ledgers_call_event_type"].unique is True
    assert usage_indexes["uq_usage_ledgers_event_source"].unique is True

    active_index = _indexes(Call)["uq_calls_user_active"]
    assert active_index.unique is True
    assert active_index.dialect_options["postgresql"]["where"] is not None
    sqlite_predicate = active_index.dialect_options["sqlite"]["where"]
    assert sqlite_predicate is not None
    assert str(sqlite_predicate) == (
        "status IN ('pending', 'connected', 'ending', 'finalizing')"
    )


def test_subscription_persists_nullable_stripe_event_ordering_fields() -> None:
    columns = Subscription.__table__.columns

    assert columns["stripe_subscription_created_at"].nullable is True
    assert columns["last_stripe_event_created_at"].nullable is True


class _NamedIntegrityError(Exception):
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class _FakeNestedTransaction:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    async def __aenter__(self):
        self.timeline.append("savepoint_enter")
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        self.timeline.append("savepoint_exit")


class _FakeWebhookSession:
    def __init__(self, flush_error: IntegrityError | None = None) -> None:
        self.flush_error = flush_error
        self.timeline: list[str] = []

    def begin_nested(self) -> _FakeNestedTransaction:
        self.timeline.append("begin_nested")
        return _FakeNestedTransaction(self.timeline)

    def add(self, _event: WebhookEvent) -> None:
        self.timeline.append("add")

    async def flush(self) -> None:
        self.timeline.append("flush")
        if self.flush_error is not None:
            raise self.flush_error


class _FakeUsageSession:
    def __init__(self) -> None:
        self.added: list[UsageLedger] = []
        self.flush_count = 0

    def add(self, ledger: UsageLedger) -> None:
        self.added.append(ledger)

    async def flush(self) -> None:
        self.flush_count += 1


def _record_with_fake_session(session: _FakeWebhookSession) -> bool:
    return asyncio.run(
        WebhookEventRepository(cast(AsyncSession, session)).record_if_new(
            provider="stripe",
            external_event_id="evt_fake",
            event_type="invoice.paid",
            payload={},
        )
    )


def test_webhook_repository_uses_insert_inside_nested_savepoint() -> None:
    session = _FakeWebhookSession()

    assert _record_with_fake_session(session) is True
    assert session.timeline == [
        "begin_nested",
        "savepoint_enter",
        "add",
        "flush",
        "savepoint_exit",
    ]


def test_webhook_repository_returns_false_for_named_identity_conflict() -> None:
    error = IntegrityError(
        "insert",
        {},
        _NamedIntegrityError("uq_webhook_events_provider_external_event_id"),
    )

    assert _record_with_fake_session(_FakeWebhookSession(error)) is False


def test_webhook_repository_reraises_unrelated_integrity_error() -> None:
    error = IntegrityError(
        "insert",
        {},
        _NamedIntegrityError("uq_webhook_events_unrelated"),
    )

    with pytest.raises(IntegrityError) as exc_info:
        _record_with_fake_session(_FakeWebhookSession(error))

    assert exc_info.value is error


def test_usage_repository_accepts_source_id_without_breaking_existing_calls() -> None:
    session = _FakeUsageSession()
    repository = UsageRepository(cast(AsyncSession, session))

    sourced = asyncio.run(
        repository.create(
            user_id="user-id",
            event_type="invoice_paid_reset",
            source_id="in_fake",
            minutes_delta=60,
        )
    )
    unsourced = asyncio.run(
        repository.create(
            user_id="user-id",
            event_type="adjustment",
            minutes_delta=1,
        )
    )

    assert sourced.source_id == "in_fake"
    assert unsourced.source_id is None
    assert session.added == [sourced, unsourced]
    assert session.flush_count == 2


@pytest.fixture
def sqlite_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _sync_user(sqlite_session: Session, *, suffix: str) -> User:
    user = User(clerk_user_id=f"user_{suffix}", email=f"{suffix}@example.com")
    sqlite_session.add(user)
    sqlite_session.flush()
    return user


async def _async_user(db_session: AsyncSession, *, suffix: str) -> User:
    user = User(clerk_user_id=f"user_{suffix}", email=f"{suffix}@example.com")
    db_session.add(user)
    await db_session.flush()
    return user


def test_webhook_identity_is_provider_scoped(sqlite_session: Session) -> None:
    sqlite_session.add_all(
        [
            WebhookEvent(
                provider="stripe",
                external_event_id="evt_same",
                event_type="invoice.paid",
                payload={},
            ),
            WebhookEvent(
                provider="clerk",
                external_event_id="evt_same",
                event_type="user.created",
                payload={},
            ),
        ]
    )
    sqlite_session.flush()

    sqlite_session.add(
        WebhookEvent(
            provider="stripe",
            external_event_id="evt_same",
            event_type="invoice.updated",
            payload={},
        )
    )
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


def test_usage_identities_are_partial_and_source_id_is_nullable(
    sqlite_session: Session,
) -> None:
    user = _sync_user(sqlite_session, suffix="usage")
    completed_call = Call(user_id=user.id, status="completed")
    sqlite_session.add(completed_call)
    sqlite_session.flush()

    sqlite_session.add_all(
        [
            UsageLedger(
                user_id=user.id,
                call_id=completed_call.id,
                event_type="call_completed",
                minutes_delta=-1,
            ),
            UsageLedger(
                user_id=user.id,
                event_type="adjustment",
                source_id=None,
                minutes_delta=1,
            ),
            UsageLedger(
                user_id=user.id,
                event_type="adjustment",
                source_id=None,
                minutes_delta=2,
            ),
            UsageLedger(
                user_id=user.id,
                event_type="invoice_paid_reset",
                source_id="in_same",
                minutes_delta=60,
            ),
        ]
    )
    sqlite_session.flush()

    sqlite_session.add(
        UsageLedger(
            user_id=user.id,
            call_id=completed_call.id,
            event_type="call_completed",
            minutes_delta=-1,
        )
    )
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


def test_usage_event_source_identity_is_unique(sqlite_session: Session) -> None:
    user = _sync_user(sqlite_session, suffix="source")
    sqlite_session.add(
        UsageLedger(
            user_id=user.id,
            event_type="invoice_paid_reset",
            source_id="in_same",
            minutes_delta=60,
        )
    )
    sqlite_session.flush()

    sqlite_session.add(
        UsageLedger(
            user_id=user.id,
            event_type="invoice_paid_reset",
            source_id="in_same",
            minutes_delta=60,
        )
    )
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


@pytest.mark.anyio
async def test_usage_repository_accepts_optional_source_identity(
    db_session: AsyncSession,
) -> None:
    user = await _async_user(db_session, suffix="repository_source")

    sourced = await UsageRepository(db_session).create(
        user_id=user.id,
        event_type="invoice_paid_reset",
        source_id="in_repository",
        minutes_delta=60,
    )
    unsourced = await UsageRepository(db_session).create(
        user_id=user.id,
        event_type="adjustment",
        minutes_delta=1,
    )

    assert sourced.source_id == "in_repository"
    assert unsourced.source_id is None


def test_call_message_sequence_is_unique_per_call(sqlite_session: Session) -> None:
    user = _sync_user(sqlite_session, suffix="message")
    completed_call = Call(user_id=user.id, status="completed")
    sqlite_session.add(completed_call)
    sqlite_session.flush()
    sqlite_session.add(
        CallMessage(
            call_id=completed_call.id,
            speaker="CALLER",
            text="one",
            sequence_number=1,
        )
    )
    sqlite_session.flush()

    sqlite_session.add(
        CallMessage(
            call_id=completed_call.id,
            speaker="AGENT",
            text="two",
            sequence_number=1,
        )
    )
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


def test_subscription_is_unique_per_user(sqlite_session: Session) -> None:
    user = _sync_user(sqlite_session, suffix="subscription")
    sqlite_session.add(
        Subscription(
            user_id=user.id,
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
        )
    )
    sqlite_session.flush()

    sqlite_session.add(
        Subscription(
            user_id=user.id,
            plan_tier="starter",
            status="active",
            allocated_minutes=60,
        )
    )
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


@pytest.mark.parametrize(
    "subscription_status",
    [
        "trialing",
        "active",
        "past_due",
        "unpaid",
        "canceled",
        "incomplete",
        "incomplete_expired",
        "paused",
    ],
)
def test_subscription_accepts_exact_provider_status_set(
    sqlite_session: Session,
    subscription_status: str,
) -> None:
    user = _sync_user(sqlite_session, suffix=f"status_{subscription_status}")
    sqlite_session.add(
        Subscription(
            user_id=user.id,
            plan_tier="starter",
            status=subscription_status,
            allocated_minutes=60,
        )
    )

    sqlite_session.flush()


@pytest.mark.parametrize(
    ("plan_tier", "subscription_status"),
    [("standard", "active"), ("starter", "inactive"), ("starter", "unknown")],
)
def test_subscription_rejects_unsupported_plan_or_status(
    sqlite_session: Session,
    plan_tier: str,
    subscription_status: str,
) -> None:
    user = _sync_user(
        sqlite_session,
        suffix=f"invalid_{plan_tier}_{subscription_status}",
    )
    sqlite_session.add(
        Subscription(
            user_id=user.id,
            plan_tier=plan_tier,
            status=subscription_status,
            allocated_minutes=60,
        )
    )

    with pytest.raises(IntegrityError):
        sqlite_session.flush()


@pytest.mark.parametrize("second_status", ["pending", "connected", "ending", "finalizing"])
def test_only_one_active_call_is_allowed_per_user(
    sqlite_session: Session,
    second_status: str,
) -> None:
    user = _sync_user(sqlite_session, suffix=second_status)
    sqlite_session.add(Call(user_id=user.id, status="pending"))
    sqlite_session.flush()

    sqlite_session.add(Call(user_id=user.id, status=second_status))
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


def test_completed_calls_do_not_block_a_new_active_call(sqlite_session: Session) -> None:
    user = _sync_user(sqlite_session, suffix="completed")
    sqlite_session.add_all(
        [
            Call(user_id=user.id, status="completed"),
            Call(user_id=user.id, status="completed"),
            Call(user_id=user.id, status="pending"),
        ]
    )
    sqlite_session.flush()


@pytest.mark.parametrize(
    "instance_factory",
    [
        lambda user_id: Subscription(
            user_id=user_id,
            plan_tier="starter",
            status="active",
            allocated_minutes=-1,
        ),
        lambda user_id: Call(user_id=user_id, status="completed", duration_seconds=-1),
        lambda user_id: Call(user_id=user_id, status="completed", minutes_charged=-1),
    ],
)
def test_negative_owned_values_are_rejected(
    sqlite_session: Session,
    instance_factory: Callable,
) -> None:
    user = _sync_user(sqlite_session, suffix=f"negative_{id(instance_factory)}")
    sqlite_session.add(instance_factory(user.id))

    with pytest.raises(IntegrityError):
        sqlite_session.flush()


@pytest.mark.anyio
async def test_webhook_repository_inserts_without_a_preflight_select(
    db_session: AsyncSession,
) -> None:
    statements: list[str] = []

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    sync_engine = db_session.bind.sync_engine
    event.listen(sync_engine, "before_cursor_execute", record_statement)
    try:
        inserted = await WebhookEventRepository(db_session).record_if_new(
            provider="stripe",
            external_event_id="evt_insert_only",
            event_type="invoice.paid",
            payload={},
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", record_statement)

    assert inserted is True
    assert not any(statement.lstrip().upper().startswith("SELECT") for statement in statements)


@pytest.mark.anyio
async def test_webhook_duplicate_keeps_outer_transaction_usable(
    db_session: AsyncSession,
) -> None:
    repository = WebhookEventRepository(db_session)
    assert await repository.record_if_new(
        provider="stripe",
        external_event_id="evt_duplicate",
        event_type="invoice.paid",
        payload={},
    ) is True
    await db_session.commit()

    outer_user = User(clerk_user_id="outer_user", email="outer@example.com")
    db_session.add(outer_user)
    assert await repository.record_if_new(
        provider="stripe",
        external_event_id="evt_duplicate",
        event_type="invoice.updated",
        payload={},
    ) is False

    await db_session.commit()
    assert outer_user.id is not None
