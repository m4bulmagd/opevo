from io import StringIO
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import Base


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0009_complete_transactional_outbox.py"
)


def _load_migration() -> ModuleType:
    spec = spec_from_file_location("task7_outbox_migration", MIGRATION_PATH)
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


def test_upgrade_preflights_existing_rows_before_adding_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    names = [name for name, _details in operations.timeline]
    assert names[:6] == ["preflight"] * 6
    assert names[6] == "execute"
    assert names[7] == "create_check_constraint"


def test_upgrade_adds_outbox_checks_and_due_work_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    checks = {
        args[0]: args[2]
        for args, _kwargs in _ddl_calls(operations, "create_check_constraint")
    }
    assert checks == {
        "ck_outbox_events_status_allowed": (
            "status IN ('pending', 'processing', 'delivered', 'failed')"
        ),
        "ck_outbox_events_attempt_count_nonnegative": "attempt_count >= 0",
        "ck_outbox_events_delivery_consistent": (
            "((status = 'delivered' AND delivered_at IS NOT NULL "
            "AND last_error_code IS NULL) OR "
            "(status <> 'delivered' AND delivered_at IS NULL)) "
            "AND (status <> 'failed' OR last_error_code IS NOT NULL)"
        ),
        "ck_phone_number_provisionings_status_allowed": (
            "status IN ('queued', 'running', 'succeeded', 'failed')"
        ),
        "ck_phone_number_provisionings_attempt_count_nonnegative": (
            "attempt_count >= 0"
        ),
    }
    indexes = _ddl_calls(operations, "create_index")
    assert indexes == [
        (
            (
                "ix_outbox_events_due_work",
                "outbox_events",
                ["status", "next_attempt_at", "created_at", "id"],
            ),
            {"unique": False},
        )
    ]
    normalization = _ddl_calls(operations, "execute")
    assert len(normalization) == 1
    assert "aggregate_type = 'user'" in str(normalization[0][0][0])
    assert "json_build_object('user_id'" in str(normalization[0][0][0])

    added_columns = _ddl_calls(operations, "add_column")
    assert len(added_columns) == 1
    table_name, operation_key = added_columns[0][0]
    assert table_name == "phone_number_provisionings"
    assert operation_key.name == "provider_operation_key"
    assert operation_key.type.length == 255
    assert operation_key.nullable is True

    unique_constraints = _ddl_calls(operations, "create_unique_constraint")
    assert unique_constraints == [
        (
            (
                "uq_phone_number_provisionings_provider_operation_key",
                "phone_number_provisionings",
                ["provider_operation_key"],
            ),
            {},
        )
    ]


def test_preflight_failure_is_opaque_and_prevents_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations(
        [[{"identity": "opaque-event", "duplicate_count": 1}]]
    )
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError) as exc_info:
        migration.upgrade()

    assert "legacy_phone_disable_user_reference" in str(exc_info.value)
    assert "identity=opaque-event" in str(exc_info.value)
    assert all(name == "preflight" for name, _details in operations.timeline)


def test_downgrade_removes_due_index_then_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert [(name, details[0][0]) for name, details in operations.timeline] == [
        ("drop_index", "ix_outbox_events_due_work"),
        (
            "drop_constraint",
            "uq_phone_number_provisionings_provider_operation_key",
        ),
        ("drop_column", "phone_number_provisionings"),
        (
            "drop_constraint",
            "ck_phone_number_provisionings_attempt_count_nonnegative",
        ),
        ("drop_constraint", "ck_phone_number_provisionings_status_allowed"),
        ("drop_constraint", "ck_outbox_events_delivery_consistent"),
        ("drop_constraint", "ck_outbox_events_attempt_count_nonnegative"),
        ("drop_constraint", "ck_outbox_events_status_allowed"),
    ]


def test_downgrade_uses_preformatted_check_names_with_repo_naming_convention() -> None:
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
    migration.op = Operations(context)

    migration.downgrade()

    sql = output.getvalue()
    expected_names = (
        "uq_phone_number_provisionings_provider_operation_key",
        "ck_phone_number_provisionings_attempt_count_nonnegative",
        "ck_phone_number_provisionings_status_allowed",
        "ck_outbox_events_delivery_consistent",
        "ck_outbox_events_attempt_count_nonnegative",
        "ck_outbox_events_status_allowed",
    )
    for name in expected_names:
        assert f"DROP CONSTRAINT {name}" in sql
    assert "ck_phone_number_provisionings_ck_phone_number_provision" not in sql
    assert "ck_outbox_events_ck_outbox_events" not in sql
