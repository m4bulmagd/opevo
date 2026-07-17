from importlib.util import module_from_spec, spec_from_file_location
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.models.call import Call
from app.models.notification import Notification


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0011_add_call_state_machine.py"
)


def _load_migration():
    assert MIGRATION_PATH.exists(), "Task 10 migration must exist"
    spec = spec_from_file_location("task10_call_state_machine", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows=None) -> None:
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Bind:
    def __init__(self, timeline, results=None) -> None:
        self.timeline = timeline
        self.results = iter(results or [])

    def execute(self, statement):
        self.timeline.append(("preflight", str(statement), {}))
        return _Result(next(self.results, []))


class _Operations:
    def __init__(self, results=None) -> None:
        self.timeline = []
        self.bind = _Bind(self.timeline, results)

    def get_bind(self):
        return self.bind

    def f(self, name: str) -> str:
        return name

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.timeline.append((name, args, kwargs))

        return record


def test_upgrade_preflights_before_mutating_schema(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    names = [name for name, _args, _kwargs in operations.timeline]
    first_ddl = next(index for index, name in enumerate(names) if name != "preflight")
    assert names[:first_ddl] == ["preflight", "preflight"]


def test_upgrade_adds_state_fields_constraints_and_indexes(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    added = {
        args[1].name: args[1]
        for name, args, _kwargs in operations.timeline
        if name == "add_column" and args[0] == "calls"
    }
    assert set(added) == {
        "state_changed_at",
        "finalization_attempt_count",
        "last_reconciled_at",
        "summary_transcript_max_sequence",
    }
    checks = {
        args[0]: args[2]
        for name, args, _kwargs in operations.timeline
        if name == "create_check_constraint" and args[1] == "calls"
    }
    assert checks == {
        "ck_calls_status_allowed": (
            "status IN ('pending', 'connected', 'ending', 'finalizing', "
            "'completed', 'failed')"
        ),
        "ck_calls_finalization_attempt_count_nonnegative": (
            "finalization_attempt_count >= 0"
        ),
        "ck_calls_summary_transcript_max_sequence_nonnegative": (
            "summary_transcript_max_sequence IS NULL OR "
            "summary_transcript_max_sequence >= 0"
        ),
        "ck_calls_failure_status_consistent": (
            "(status = 'failed' AND failure_code IS NOT NULL) OR "
            "(status <> 'failed' AND failure_code IS NULL)"
        ),
    }
    assert any(
        name == "create_index"
        and args[:3] == (
            "ix_calls_reconciliation_stale_work",
            "calls",
            ["status", "state_changed_at", "last_reconciled_at"],
        )
        and kwargs["postgresql_where"] is not None
        for name, args, kwargs in operations.timeline
    )
    assert any(
        name == "create_unique_constraint"
        and args == (
            "uq_notifications_call_notification_type",
            "notifications",
            ["call_id", "notification_type"],
        )
        for name, args, _kwargs in operations.timeline
    )
    state_alter = next(
        (args, kwargs)
        for name, args, kwargs in operations.timeline
        if name == "alter_column"
        and args == ("calls", "state_changed_at")
    )
    assert state_alter[1]["nullable"] is False
    assert state_alter[1]["server_default"] is not None


def test_preflight_rejects_unknown_status_before_ddl(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations(
        results=[[{"identity": "opaque", "duplicate_count": 1}]]
    )
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError, match="call_status_allowed"):
        migration.upgrade()

    assert all(name == "preflight" for name, _args, _kwargs in operations.timeline)


def test_preflight_rejects_duplicate_call_notification_identity_before_ddl(
    monkeypatch,
) -> None:
    migration = _load_migration()
    operations = _Operations(
        results=[[], [{"identity": "opaque", "duplicate_count": 2}]]
    )
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError, match="notification_call_type_identity"):
        migration.upgrade()

    assert [name for name, _args, _kwargs in operations.timeline] == [
        "preflight",
        "preflight",
    ]


def test_models_match_state_machine_schema() -> None:
    call_columns = Call.__table__.columns
    assert call_columns["state_changed_at"].nullable is False
    assert call_columns["finalization_attempt_count"].nullable is False
    assert call_columns["last_reconciled_at"].nullable is True
    assert call_columns["summary_transcript_max_sequence"].nullable is True

    call_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in Call.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_calls_status_allowed" in call_checks
    assert "ck_calls_finalization_attempt_count_nonnegative" in call_checks
    assert "ck_calls_summary_transcript_max_sequence_nonnegative" in call_checks
    assert "ck_calls_failure_status_consistent" in call_checks
    stale_index = next(
        index
        for index in Call.__table__.indexes
        if isinstance(index, Index)
        and index.name == "ix_calls_reconciliation_stale_work"
    )
    assert stale_index.dialect_options["postgresql"]["where"] is not None
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_notifications_call_notification_type"
        for constraint in Notification.__table__.constraints
    )


@pytest.mark.anyio
async def test_postgresql_blank_to_head_backfills_all_states_and_enforces_constraints() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Task 10 migration proof requires TEST_DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    if not database_url.startswith("postgresql+asyncpg://"):
        pytest.skip("TEST_DATABASE_URL must identify PostgreSQL")

    source_url = make_url(database_url)
    admin_url = source_url.set(database="postgres")
    database_name = f"task10_migration_{uuid4().hex}"
    migration_url = source_url.set(database=database_name)
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
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
        run_alembic("0010_durable_livekit_dispatch")
        migration_engine = create_async_engine(migration_url)
        statuses = (
            "pending",
            "connected",
            "ending",
            "finalizing",
            "completed",
            "failed",
        )
        call_ids = {status: uuid4() for status in statuses}
        user_ids = {status: uuid4() for status in statuses}
        created_at = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
        started_at = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
        ended_at = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
        updated_at = datetime(2026, 1, 1, 3, tzinfo=timezone.utc)
        async with migration_engine.begin() as connection:
            for status in statuses:
                await connection.execute(
                    text(
                        "INSERT INTO users "
                        "(id, clerk_user_id, email, status, created_at, updated_at) "
                        "VALUES (:id, :clerk, :email, 'active', :created, :updated)"
                    ),
                    {
                        "id": user_ids[status],
                        "clerk": f"migration_{status}",
                        "email": f"migration_{status}@example.com",
                        "created": created_at,
                        "updated": updated_at,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO calls "
                        "(id, user_id, status, started_at, ended_at, failure_code, "
                        "created_at, updated_at) "
                        "VALUES (:id, :user_id, :status, :started, :ended, :failure, "
                        ":created, :updated)"
                    ),
                    {
                        "id": call_ids[status],
                        "user_id": user_ids[status],
                        "status": status,
                        "started": started_at,
                        "ended": ended_at,
                        "failure": None,
                        "created": created_at,
                        "updated": updated_at,
                    },
                )

        await migration_engine.dispose()
        migration_engine = None
        run_alembic("0011_call_state_machine")
        migration_engine = create_async_engine(migration_url)
        expected = {
            "pending": created_at,
            "connected": started_at,
            "ending": updated_at,
            "finalizing": updated_at,
            "completed": ended_at,
            "failed": ended_at,
        }
        async with migration_engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT status, state_changed_at, failure_code "
                        "FROM calls ORDER BY status"
                    )
                )
            ).mappings().all()
            assert {
                row["status"]: row["state_changed_at"] for row in rows
            } == expected
            failed = next(row for row in rows if row["status"] == "failed")
            assert failed["failure_code"] == "legacy_failure"
            revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            assert revision == "0011_call_state_machine"

        invalid_statements = [
            (
                "INSERT INTO calls (id, user_id, status) "
                "VALUES (:id, :user_id, 'mystery')",
                {"id": uuid4(), "user_id": user_ids["pending"]},
            ),
            (
                "UPDATE calls SET finalization_attempt_count = -1 "
                "WHERE id = :id",
                {"id": call_ids["completed"]},
            ),
            (
                "UPDATE calls SET status = 'failed', failure_code = NULL "
                "WHERE id = :id",
                {"id": call_ids["completed"]},
            ),
            (
                "UPDATE calls SET summary_transcript_max_sequence = -1 "
                "WHERE id = :id",
                {"id": call_ids["completed"]},
            ),
        ]
        for statement, parameters in invalid_statements:
            with pytest.raises(IntegrityError):
                async with migration_engine.begin() as connection:
                    await connection.execute(text(statement), parameters)

        async with migration_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO notifications "
                    "(id, user_id, call_id, notification_type, status, payload) "
                    "VALUES (:id, :user_id, :call_id, "
                    "'call_completed', 'pending', '{}')"
                ),
                {
                    "id": uuid4(),
                    "user_id": user_ids["completed"],
                    "call_id": call_ids["completed"],
                },
            )
        with pytest.raises(IntegrityError):
            async with migration_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO notifications "
                        "(id, user_id, call_id, notification_type, status, payload) "
                        "VALUES (:id, :user_id, :call_id, "
                        "'call_completed', 'pending', '{}')"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_ids["completed"],
                        "call_id": call_ids["completed"],
                    },
                )

        async with migration_engine.begin() as connection:
            inserted_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO calls (id, user_id, status) "
                    "VALUES (:id, :user_id, 'completed')"
                ),
                {"id": inserted_id, "user_id": user_ids["pending"]},
            )
            state_changed_at = await connection.scalar(
                text("SELECT state_changed_at FROM calls WHERE id = :id"),
                {"id": inserted_id},
            )
            assert state_changed_at is not None
    finally:
        if migration_engine is not None:
            await migration_engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{database_name}"')
            )
        await admin_engine.dispose()
