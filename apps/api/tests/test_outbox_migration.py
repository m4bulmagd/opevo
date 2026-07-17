from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import Column


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0008_add_outbox_and_call_lifecycle.py"
)


def _load_migration() -> ModuleType:
    spec = spec_from_file_location("task5_outbox_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.context = SimpleNamespace(is_offline_mode=lambda: False)
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


def test_upgrade_preflights_subscription_states_before_any_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    operation_names = [name for name, _details in operations.timeline]
    assert operation_names[:2] == ["preflight", "preflight"]
    assert operation_names[2] == "add_column"


def test_upgrade_creates_full_outbox_shape_and_subscription_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    create_table_calls = _ddl_calls(operations, "create_table")
    assert len(create_table_calls) == 1
    args, _kwargs = create_table_calls[0]
    assert args[0] == "outbox_events"
    columns = {
        value.name: value
        for value in args[1:]
        if isinstance(value, Column)
    }
    assert set(columns) == {
        "idempotency_key",
        "topic",
        "aggregate_type",
        "aggregate_id",
        "payload",
        "status",
        "attempt_count",
        "next_attempt_at",
        "last_error_code",
        "delivered_at",
        "created_at",
        "updated_at",
        "id",
    }
    assert columns["next_attempt_at"].nullable is False
    assert columns["last_error_code"].nullable is True
    assert columns["status"].server_default is not None
    assert columns["attempt_count"].server_default is not None
    assert columns["next_attempt_at"].server_default is not None

    indexes = {
        args[0] for args, _kwargs in _ddl_calls(operations, "create_index")
    }
    assert indexes == {
        "ix_outbox_events_topic",
        "ix_outbox_events_aggregate_id",
        "ix_outbox_events_status",
    }

    check_calls = _ddl_calls(operations, "create_check_constraint")
    checks = {args[0]: args[2] for args, _kwargs in check_calls}
    assert checks == {
        "ck_subscriptions_status_allowed": (
            "status IN ('trialing', 'active', 'past_due', 'unpaid', "
            "'canceled', 'incomplete', 'incomplete_expired', 'paused')"
        ),
        "ck_subscriptions_plan_tier_allowed": "plan_tier = 'starter'",
    }

    subscription_columns = {
        args[1].name: args[1]
        for args, _kwargs in _ddl_calls(operations, "add_column")
        if args[0] == "subscriptions"
    }
    assert set(subscription_columns) == {
        "stripe_subscription_created_at",
        "last_stripe_event_created_at",
    }
    assert all(column.nullable is True for column in subscription_columns.values())


def test_preflight_failure_is_opaque_and_runs_no_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations(
        [[{"identity": "opaque-subscription", "duplicate_count": 1}]]
    )
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError) as exc_info:
        migration.upgrade()

    message = str(exc_info.value)
    assert "ck_subscriptions_status_allowed" in message
    assert "identity=opaque-subscription" in message
    assert "count=1" in message
    assert "status=" not in message
    assert "plan_tier=" not in message
    assert all(name == "preflight" for name, _details in operations.timeline)


def test_downgrade_drops_checks_before_outbox_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    calls = [(name, details[0][0]) for name, details in operations.timeline]
    assert calls == [
        ("drop_column", "subscriptions"),
        ("drop_column", "subscriptions"),
        ("drop_constraint", "ck_subscriptions_plan_tier_allowed"),
        ("drop_constraint", "ck_subscriptions_status_allowed"),
        ("drop_index", "ix_outbox_events_status"),
        ("drop_index", "ix_outbox_events_aggregate_id"),
        ("drop_index", "ix_outbox_events_topic"),
        ("drop_table", "outbox_events"),
    ]
