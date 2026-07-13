from io import StringIO
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, Column, Index, UniqueConstraint


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0007_add_production_integrity_constraints.py"
)


def _load_migration() -> ModuleType:
    spec = spec_from_file_location("task4_integrity_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _Bind:
    def __init__(
        self,
        timeline: list[tuple[str, object]],
        results: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self.timeline = timeline
        self.results = iter(results or [])

    def execute(self, statement) -> _Result:
        self.timeline.append(("preflight", str(statement)))
        return _Result(next(self.results, []))


class _Operations:
    def __init__(
        self,
        results: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self.timeline: list[tuple[str, object]] = []
        self.bind = _Bind(self.timeline, results)

    def get_bind(self) -> _Bind:
        return self.bind

    def f(self, name: str) -> str:
        return name

    def __getattr__(self, name: str):
        def record(*args, **kwargs) -> None:
            self.timeline.append((name, (args, kwargs)))

        return record


def _ddl_calls(operations: _Operations, name: str) -> list[tuple[tuple, dict]]:
    return [details for operation, details in operations.timeline if operation == name]


def test_upgrade_preflights_all_existing_conditions_before_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    operation_names = [name for name, _details in operations.timeline]
    first_ddl = next(index for index, name in enumerate(operation_names) if name != "preflight")
    assert first_ddl == 8
    assert operation_names[:first_ddl] == ["preflight"] * 8


def test_upgrade_creates_exact_constraints_indexes_and_predicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    added_columns = _ddl_calls(operations, "add_column")
    assert len(added_columns) == 1
    assert added_columns[0][0][0] == "usage_ledgers"
    source_column = added_columns[0][0][1]
    assert isinstance(source_column, Column)
    assert source_column.name == "source_id"
    assert source_column.nullable is True

    unique_names = {
        args[0]
        for args, _kwargs in _ddl_calls(operations, "create_unique_constraint")
    }
    assert unique_names == {
        "uq_webhook_events_provider_external_event_id",
        "uq_call_messages_call_sequence",
        "uq_subscriptions_user_id",
    }

    indexes = {
        args[0]: (args, kwargs)
        for args, kwargs in _ddl_calls(operations, "create_index")
    }
    assert set(indexes) == {
        "uq_usage_ledgers_call_event_type",
        "uq_usage_ledgers_event_source",
        "uq_calls_user_active",
    }
    assert str(indexes["uq_usage_ledgers_call_event_type"][1]["postgresql_where"]) == (
        "call_id IS NOT NULL"
    )
    assert str(indexes["uq_usage_ledgers_event_source"][1]["postgresql_where"]) == (
        "source_id IS NOT NULL"
    )
    assert str(indexes["uq_calls_user_active"][1]["postgresql_where"]) == (
        "status IN ('pending', 'connected', 'ending', 'finalizing')"
    )
    assert all(kwargs["unique"] is True for _args, kwargs in indexes.values())

    check_names = {
        args[0]
        for args, _kwargs in _ddl_calls(operations, "create_check_constraint")
    }
    assert check_names == {
        "ck_subscriptions_allocated_minutes_nonnegative",
        "ck_calls_duration_seconds_nonnegative",
        "ck_calls_minutes_charged_nonnegative",
    }


def test_generated_postgresql_ddl_uses_exact_constraint_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models import Base

    migration = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={
            "as_sql": True,
            "output_buffer": output,
            "target_metadata": Base.metadata,
        },
    )
    monkeypatch.setattr(migration, "op", Operations(context))
    monkeypatch.setattr(migration, "_run_preflight", lambda _connection: None)

    migration.upgrade()

    ddl = output.getvalue()
    assert "CONSTRAINT ck_subscriptions_allocated_minutes_nonnegative" in ddl
    assert "CONSTRAINT ck_calls_duration_seconds_nonnegative" in ddl
    assert "CONSTRAINT ck_calls_minutes_charged_nonnegative" in ddl
    assert "ck_subscriptions_ck_subscriptions" not in ddl
    assert "ck_calls_ck_calls" not in ddl

    output.seek(0)
    output.truncate()
    migration.downgrade()
    downgrade_ddl = output.getvalue()
    assert "DROP CONSTRAINT ck_subscriptions_allocated_minutes_nonnegative" in downgrade_ddl
    assert "DROP CONSTRAINT ck_calls_duration_seconds_nonnegative" in downgrade_ddl
    assert "DROP CONSTRAINT ck_calls_minutes_charged_nonnegative" in downgrade_ddl
    assert downgrade_ddl.index("DROP INDEX uq_usage_ledgers_event_source") < (
        downgrade_ddl.index("DROP COLUMN source_id")
    )


def test_preflight_aborts_without_ddl_and_reports_only_opaque_identity_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    results = [
        [{"identity": "opaque-row-1", "duplicate_count": 2}],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    ]
    operations = _Operations(results)
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError) as exc_info:
        migration.upgrade()

    message = str(exc_info.value)
    assert "uq_webhook_events_provider_external_event_id" in message
    assert "identity=opaque-row-1" in message
    assert "count=2" in message
    assert "payload" not in message
    assert "transcript" not in message
    assert all(name == "preflight" for name, _details in operations.timeline)
    assert len(operations.timeline) == 8


def test_downgrade_reverses_every_task4_change_in_dependency_safe_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    calls = [(name, details[0][0]) for name, details in operations.timeline]
    assert calls == [
        ("drop_constraint", "ck_calls_minutes_charged_nonnegative"),
        ("drop_constraint", "ck_calls_duration_seconds_nonnegative"),
        ("drop_constraint", "ck_subscriptions_allocated_minutes_nonnegative"),
        ("drop_index", "uq_calls_user_active"),
        ("drop_constraint", "uq_subscriptions_user_id"),
        ("drop_constraint", "uq_call_messages_call_sequence"),
        ("drop_index", "uq_usage_ledgers_event_source"),
        ("drop_index", "uq_usage_ledgers_call_event_type"),
        ("drop_constraint", "uq_webhook_events_provider_external_event_id"),
        ("drop_column", "usage_ledgers"),
    ]
    drop_column_args, _drop_column_kwargs = operations.timeline[-1][1]
    assert drop_column_args == ("usage_ledgers", "source_id")


def test_model_metadata_uses_named_task4_objects() -> None:
    from app.models.call import Call
    from app.models.call_message import CallMessage
    from app.models.subscription import Subscription
    from app.models.usage_ledger import UsageLedger
    from app.models.webhook_event import WebhookEvent

    constraints = {
        constraint.name
        for table in (
            WebhookEvent.__table__,
            CallMessage.__table__,
            Subscription.__table__,
            Call.__table__,
        )
        for constraint in table.constraints
        if isinstance(constraint, (CheckConstraint, UniqueConstraint))
    }
    indexes = {
        index.name
        for table in (UsageLedger.__table__, Call.__table__)
        for index in table.indexes
        if isinstance(index, Index)
    }

    assert {
        "uq_webhook_events_provider_external_event_id",
        "uq_call_messages_call_sequence",
        "uq_subscriptions_user_id",
        "ck_subscriptions_allocated_minutes_nonnegative",
        "ck_calls_duration_seconds_nonnegative",
        "ck_calls_minutes_charged_nonnegative",
    } <= constraints
    assert {
        "uq_usage_ledgers_call_event_type",
        "uq_usage_ledgers_event_source",
        "uq_calls_user_active",
    } <= indexes
