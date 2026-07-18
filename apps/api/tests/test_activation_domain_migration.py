from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.agent_config import AgentConfig
from app.models.phone_number import PhoneNumber
from app.models.user import User


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0012_add_customer_activation_domain.py"
)


def _load_migration():
    assert MIGRATION_PATH.exists(), "Customer activation migration must exist"
    spec = spec_from_file_location("customer_activation_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.context = SimpleNamespace(is_offline_mode=lambda: False)
    return module


class _Result:
    def __init__(self, scalar: int) -> None:
        self.scalar = scalar

    def scalar_one(self) -> int:
        return self.scalar


class _Bind:
    def __init__(self, timeline: list[tuple[str, tuple, dict]], duplicate_groups: int) -> None:
        self.timeline = timeline
        self.duplicate_groups = duplicate_groups

    def execute(self, statement):
        self.timeline.append(("preflight", (statement,), {}))
        return _Result(self.duplicate_groups)


class _Operations:
    def __init__(self, *, duplicate_groups: int = 0) -> None:
        self.timeline: list[tuple[str, tuple, dict]] = []
        self.bind = _Bind(self.timeline, duplicate_groups)

    def get_bind(self):
        return self.bind

    def f(self, name: str) -> str:
        return name

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.timeline.append((name, args, kwargs))

        return record


def test_activation_models_enforce_one_row_per_user() -> None:
    from app.models.business_profile import BusinessProfile
    from app.models.customer_activation import CustomerActivation

    assert BusinessProfile.__table__.c.user_id.unique is True
    assert CustomerActivation.__table__.c.user_id.unique is True
    assert PhoneNumber.__table__.c.user_id.unique is True


def test_activation_models_define_guardrails_and_projection_fields() -> None:
    from app.models.business_profile import BusinessProfile
    from app.models.customer_activation import CustomerActivation

    profile_checks = {
        constraint.name
        for constraint in BusinessProfile.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    activation_checks = {
        constraint.name
        for constraint in CustomerActivation.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert profile_checks == {
        "ck_business_profiles_confirmed_carrier_allowed",
        "ck_business_profiles_content_revision_positive",
        "ck_business_profiles_routing_revision_positive",
    }
    assert activation_checks == {
        "ck_customer_activations_verification_status_allowed",
        "ck_customer_activations_workflow_version_positive",
    }
    assert AgentConfig.__table__.c.business_display_name.nullable is True
    assert AgentConfig.__table__.c.profile_projection_revision.nullable is False
    assert AgentConfig.__table__.c.profile_projection_revision.default.arg == 0


def test_activation_revision_follows_call_state_machine() -> None:
    migration = _load_migration()

    assert migration.revision == "0012_customer_activation"
    assert migration.down_revision == "0011_call_state_machine"


def test_offline_upgrade_generates_full_chain_through_routing_target_head() -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": (
            "postgresql+asyncpg://migration:password@database.example/presvo"
        ),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=MIGRATION_PATH.parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "0011_call_state_machine -> 0012_customer_activation" in output
    assert "0012_customer_activation -> 0013_outbox_routing_target" in output
    assert "version_num='0013_outbox_routing_target'" in output


def test_upgrade_preflights_duplicate_phone_owners_before_ddl(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations(duplicate_groups=2)
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(
        RuntimeError,
        match=r"^Cannot add uq_phone_numbers_user_id: duplicate_user_groups=2$",
    ) as raised:
        migration.upgrade()

    assert "e164" not in str(raised.value).lower()
    assert [name for name, _args, _kwargs in operations.timeline] == ["preflight"]
    assert "e164" not in str(operations.timeline[0][1][0]).lower()


def test_upgrade_creates_activation_domain_and_backfills_existing_users(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert operations.timeline[0][0] == "preflight"
    created_tables = [
        args[0]
        for name, args, _kwargs in operations.timeline
        if name == "create_table"
    ]
    assert created_tables == [
        "business_profiles",
        "customer_activations",
        "activation_events",
    ]

    agent_columns = {
        args[1].name: args[1]
        for name, args, _kwargs in operations.timeline
        if name == "add_column" and args[0] == "agent_configs"
    }
    assert set(agent_columns) == {
        "business_display_name",
        "profile_projection_revision",
    }
    assert agent_columns["business_display_name"].nullable is True
    assert agent_columns["profile_projection_revision"].nullable is False
    assert str(agent_columns["profile_projection_revision"].server_default.arg) == "0"

    assert any(
        name == "create_unique_constraint"
        and args == (
            "uq_phone_numbers_user_id",
            "phone_numbers",
            ["user_id"],
        )
        for name, args, _kwargs in operations.timeline
    )
    executed_sql = [
        str(args[0])
        for name, args, _kwargs in operations.timeline
        if name == "execute"
    ]
    assert any("INSERT INTO business_profiles" in statement for statement in executed_sql)
    assert any("INSERT INTO customer_activations" in statement for statement in executed_sql)
    assert any(
        name == "alter_column"
        and args == ("agent_configs", "profile_projection_revision")
        and kwargs["server_default"] is None
        for name, args, kwargs in operations.timeline
    )


def test_downgrade_removes_activation_domain_in_dependency_order(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    dropped_tables = [
        args[0]
        for name, args, _kwargs in operations.timeline
        if name == "drop_table"
    ]
    assert dropped_tables == [
        "activation_events",
        "customer_activations",
        "business_profiles",
    ]
    assert any(
        name == "drop_constraint"
        and args == ("uq_phone_numbers_user_id", "phone_numbers")
        and kwargs["type_"] == "unique"
        for name, args, kwargs in operations.timeline
    )
    assert [
        args for name, args, _kwargs in operations.timeline if name == "drop_column"
    ] == [
        ("agent_configs", "profile_projection_revision"),
        ("agent_configs", "business_display_name"),
    ]


@pytest.mark.anyio
async def test_locked_activation_repositories_get_or_create_one_row(
    db_session: AsyncSession,
) -> None:
    from app.repositories.business_profile_repository import BusinessProfileRepository
    from app.repositories.customer_activation_repository import (
        CustomerActivationRepository,
    )

    user = User(
        clerk_user_id=f"activation_repo_{uuid4().hex}",
        email=f"activation_repo_{uuid4().hex}@example.com",
    )
    db_session.add(user)
    await db_session.flush()

    profiles = BusinessProfileRepository(db_session)
    activations = CustomerActivationRepository(db_session)
    profile = await profiles.get_or_create_for_update(user.id)
    activation = await activations.get_or_create_for_update(user.id)

    assert await profiles.get_or_create_for_update(user.id) is profile
    assert await profiles.get_by_user_id(user.id) is profile
    assert await activations.get_or_create_for_update(user.id) is activation
    assert await activations.get_by_user_id(user.id) is activation


@pytest.mark.anyio
async def test_activation_event_append_returns_existing_idempotent_event(
    db_session: AsyncSession,
) -> None:
    from app.models.activation_event import ActivationEvent
    from app.repositories.activation_event_repository import ActivationEventRepository
    from app.repositories.customer_activation_repository import (
        CustomerActivationRepository,
    )

    user = User(
        clerk_user_id=f"activation_event_{uuid4().hex}",
        email=f"activation_event_{uuid4().hex}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    activation = await CustomerActivationRepository(
        db_session
    ).get_or_create_for_update(user.id)
    repository = ActivationEventRepository(db_session)

    first = await repository.append(
        user_id=user.id,
        activation_id=activation.id,
        event_type="profile_confirmed",
        idempotency_key="activation-event:profile-confirmed",
        metadata={"revision": 1},
    )
    duplicate = await repository.append(
        user_id=user.id,
        activation_id=activation.id,
        event_type="ignored_duplicate",
        idempotency_key="activation-event:profile-confirmed",
        metadata={"revision": 2},
    )

    assert duplicate is first
    assert duplicate.event_type == "profile_confirmed"
    assert duplicate.event_metadata == {"revision": 1}
    assert await db_session.scalar(select(func.count()).select_from(ActivationEvent)) == 1


@pytest.mark.anyio
async def test_postgresql_migration_backfills_existing_users() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Customer activation migration proof requires TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify PostgreSQL")

    source_url = make_url(database_url)
    database_name = f"activation_migration_{uuid4().hex}"
    admin_engine = create_async_engine(
        source_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    migration_url = source_url.set(database=database_name)
    migration_engine = None

    def run_alembic(revision: str) -> None:
        env = {
            **os.environ,
            "DATABASE_URL": migration_url.render_as_string(hide_password=False),
        }
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=MIGRATION_PATH.parents[2],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        run_alembic("0011_call_state_machine")
        migration_engine = create_async_engine(migration_url)
        user_id = uuid4()
        async with migration_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, clerk_user_id, email, status, created_at, updated_at) "
                    "VALUES (:id, :clerk, :email, 'active', now(), now())"
                ),
                {"id": user_id, "clerk": f"migration_{user_id}", "email": f"{user_id}@example.com"},
            )
            await connection.execute(
                text(
                    "INSERT INTO agent_configs "
                    "(id, user_id, agent_name, system_prompt, knowledge_base, "
                    "pipeline_mode, is_enabled, created_at, updated_at) "
                    "VALUES (:id, :user_id, 'Assistant', '', '', "
                    "'stt_llm_tts', false, now(), now())"
                ),
                {"id": uuid4(), "user_id": user_id},
            )

        await migration_engine.dispose()
        migration_engine = None
        run_alembic("head")
        migration_engine = create_async_engine(migration_url)
        async with migration_engine.connect() as connection:
            profile_count = await connection.scalar(
                text("SELECT COUNT(*) FROM business_profiles WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            activation_count = await connection.scalar(
                text("SELECT COUNT(*) FROM customer_activations WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            projection_revision = await connection.scalar(
                text(
                    "SELECT profile_projection_revision FROM agent_configs "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            )

        assert profile_count == 1
        assert activation_count == 1
        assert projection_revision == 0
    finally:
        if migration_engine is not None:
            await migration_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        await admin_engine.dispose()
