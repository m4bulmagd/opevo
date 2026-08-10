from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.account_deactivation_operation import (
    AccountDeactivationOperation,
)
from app.models.subscription import Subscription
from app.models.user import User
from app.repositories.account_deactivation_repository import (
    AccountDeactivationRepository,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0015_add_account_deactivation_lifecycle.py"
)


def _load_migration(connection) -> object:
    spec = spec_from_file_location("account_deactivation_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.context = SimpleNamespace(is_offline_mode=lambda: False)
    migration.op = Operations(MigrationContext.configure(connection))
    return migration


def test_account_deactivation_model_shape() -> None:
    columns = AccountDeactivationOperation.__table__.c
    assert set(columns.keys()) == {
        "id",
        "user_id",
        "lifecycle_generation",
        "trigger",
        "status",
        "stripe_subscription_id",
        "phone_provider_id",
        "requested_at",
        "routing_disabled_at",
        "subscription_canceled_at",
        "active_call_drained_at",
        "number_released_at",
        "activation_reset_at",
        "completed_at",
        "attempt_count",
        "last_reconciled_at",
        "last_error_code",
        "created_at",
        "updated_at",
    }
    assert {constraint.name for constraint in AccountDeactivationOperation.__table__.constraints} >= {
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


def test_account_deactivation_migration_backfills_and_downgrades(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "account_deactivation.db"
    user_id = uuid4()
    subscription_id = uuid4()
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users ("
                "id CHAR(32) NOT NULL PRIMARY KEY, "
                "clerk_user_id VARCHAR(255) NOT NULL, "
                "email VARCHAR(320) NOT NULL, "
                "status VARCHAR(50) NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE subscriptions ("
                "id CHAR(32) NOT NULL PRIMARY KEY, "
                "user_id CHAR(32) NOT NULL, "
                "plan_tier VARCHAR(50) NOT NULL, "
                "status VARCHAR(50) NOT NULL, "
                "allocated_minutes INTEGER NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, clerk_user_id, email, status, created_at, updated_at) "
                "VALUES (:id, :clerk_user_id, :email, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": user_id.hex,
                "clerk_user_id": f"migration_{user_id}",
                "email": f"migration_{user_id}@example.com",
            },
        )
        connection.execute(
            text(
                "INSERT INTO subscriptions (id, user_id, plan_tier, status, allocated_minutes, "
                "created_at, updated_at) "
                "VALUES (:id, :user_id, 'starter', 'active', 60, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": subscription_id.hex, "user_id": user_id.hex},
        )
        migration = _load_migration(connection)
        migration.upgrade()

    inspector = inspect(engine)
    operation_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("account_deactivation_operations")
    }
    assert bool(
        operation_indexes["uq_account_deactivation_operations_one_incomplete_user"][
            "unique"
        ]
    )
    assert str(
        operation_indexes["uq_account_deactivation_operations_one_incomplete_user"][
            "dialect_options"
        ]["sqlite_where"]
    ) == "completed_at IS NULL"
    with engine.connect() as connection:
        user_generation = connection.scalar(
            text("SELECT lifecycle_generation FROM users WHERE id = :id"),
            {"id": user_id.hex},
        )
        subscription_generation, cancel_at_period_end = connection.execute(
            text(
                "SELECT lifecycle_generation, cancel_at_period_end "
                "FROM subscriptions WHERE id = :id"
            ),
            {"id": subscription_id.hex},
        ).one()
    assert user_generation == 1
    assert subscription_generation == 1
    assert bool(cancel_at_period_end) is False
    direct_user_id = uuid4()
    direct_subscription_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, clerk_user_id, email, status, created_at, updated_at) "
                "VALUES (:id, :clerk_user_id, :email, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": direct_user_id.hex,
                "clerk_user_id": f"direct_{direct_user_id}",
                "email": f"direct_{direct_user_id}@example.com",
            },
        )
        connection.execute(
            text(
                "INSERT INTO subscriptions (id, user_id, plan_tier, status, allocated_minutes, "
                "created_at, updated_at) "
                "VALUES (:id, :user_id, 'starter', 'active', 60, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": direct_subscription_id.hex, "user_id": direct_user_id.hex},
        )
        direct_user_generation = connection.scalar(
            text("SELECT lifecycle_generation FROM users WHERE id = :id"),
            {"id": direct_user_id.hex},
        )
        direct_subscription_generation, direct_cancel_at_period_end = connection.execute(
            text(
                "SELECT lifecycle_generation, cancel_at_period_end "
                "FROM subscriptions WHERE id = :id"
            ),
            {"id": direct_subscription_id.hex},
        ).one()
    assert direct_user_generation == 1
    assert direct_subscription_generation == 1
    assert bool(direct_cancel_at_period_end) is False
    with engine.begin() as connection:
        migration = _load_migration(connection)
        migration.downgrade()

    inspector = inspect(engine)
    assert "account_deactivation_operations" not in inspector.get_table_names()
    assert "users" in inspector.get_table_names()
    assert "subscriptions" in inspector.get_table_names()
    assert "lifecycle_generation" not in {
        column["name"] for column in inspector.get_columns("users")
    }
    assert {
        "lifecycle_generation",
        "cancel_at_period_end",
        "cancellation_effective_at",
    }.isdisjoint({column["name"] for column in inspector.get_columns("subscriptions")})
    engine.dispose()


@pytest.mark.anyio
async def test_account_deactivation_repository_reads_and_locks_operations(
    db_session: AsyncSession,
) -> None:
    user = User(
        external_user_id=f"deactivation_{uuid4().hex}",
        email=f"deactivation_{uuid4().hex}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    repository = AccountDeactivationRepository(db_session)
    requested_at = datetime(2026, 7, 24, tzinfo=UTC)

    operation = await repository.create(
        user_id=user.id,
        lifecycle_generation=1,
        trigger="owner_request",
        stripe_subscription_id="sub_deactivation",
        phone_provider_id="pn_deactivation",
        requested_at=requested_at,
    )

    assert await repository.get_by_id(operation.id) is operation
    assert await repository.get_by_id_for_update(operation.id) is operation
    assert await repository.get_incomplete_by_user_id_for_update(user.id) is operation
    assert await repository.get_latest_by_user_id(user.id) is operation
