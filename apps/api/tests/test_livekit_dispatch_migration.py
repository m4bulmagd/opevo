from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from sqlalchemy import UniqueConstraint

from app.models.call import Call


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0010_add_durable_livekit_dispatch.py"
)


def _load_migration() -> ModuleType:
    spec = spec_from_file_location("task8_livekit_dispatch_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Operations:
    def __init__(self) -> None:
        self.timeline: list[tuple[str, tuple, dict]] = []

    def f(self, name: str) -> str:
        return name

    def __getattr__(self, name: str):
        def record(*args, **kwargs) -> None:
            self.timeline.append((name, args, kwargs))

        return record


def test_upgrade_adds_nullable_dispatch_identity_and_agent_reference(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    columns = {
        args[1].name: args[1]
        for name, args, _kwargs in operations.timeline
        if name == "add_column"
    }
    assert set(columns) == {"agent_config_id", "livekit_dispatch_id", "failure_code"}
    assert all(column.nullable is True for column in columns.values())
    assert columns["livekit_dispatch_id"].type.length == 255
    assert columns["failure_code"].type.length == 100
    assert any(
        name == "create_foreign_key"
        and args == (
            "fk_calls_agent_config_id_agent_configs",
            "calls",
            "agent_configs",
            ["agent_config_id"],
            ["id"],
        )
        for name, args, _kwargs in operations.timeline
    )
    assert any(
        name == "create_index"
        and args == ("ix_calls_agent_config_id", "calls", ["agent_config_id"])
        for name, args, _kwargs in operations.timeline
    )
    assert any(
        name == "create_unique_constraint"
        and args == (
            "uq_calls_livekit_dispatch_id",
            "calls",
            ["livekit_dispatch_id"],
        )
        for name, args, _kwargs in operations.timeline
    )


def test_downgrade_removes_task8_columns_in_dependency_order(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert [(name, args[0]) for name, args, _kwargs in operations.timeline] == [
        ("drop_constraint", "uq_calls_livekit_dispatch_id"),
        ("drop_index", "ix_calls_agent_config_id"),
        ("drop_constraint", "fk_calls_agent_config_id_agent_configs"),
        ("drop_column", "calls"),
        ("drop_column", "calls"),
        ("drop_column", "calls"),
    ]
    assert [args[1] for name, args, _kwargs in operations.timeline if name == "drop_column"] == [
        "failure_code",
        "livekit_dispatch_id",
        "agent_config_id",
    ]


def test_call_model_exposes_nullable_unique_dispatch_fields() -> None:
    columns = Call.__table__.columns
    assert columns["agent_config_id"].nullable is True
    assert columns["agent_config_id"].index is True
    assert columns["livekit_dispatch_id"].nullable is True
    assert columns["failure_code"].nullable is True
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_calls_livekit_dispatch_id"
        for constraint in Call.__table__.constraints
    )
