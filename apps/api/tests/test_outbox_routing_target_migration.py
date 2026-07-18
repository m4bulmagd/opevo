from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models.outbox_event import OutboxEvent


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0013_add_outbox_routing_target.py"
)


def _load_migration():
    spec = spec_from_file_location("outbox_routing_target_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Operations:
    def __init__(self) -> None:
        self.timeline: list[tuple[str, tuple, dict]] = []

    def add_column(self, *args, **kwargs) -> None:
        self.timeline.append(("add_column", args, kwargs))

    def drop_column(self, *args, **kwargs) -> None:
        self.timeline.append(("drop_column", args, kwargs))


def test_outbox_model_has_nullable_durable_routing_target() -> None:
    column = OutboxEvent.__table__.c.routing_target_provider_number_id

    assert column.nullable is True
    assert column.type.length == 255


def test_routing_target_migration_follows_activation_domain(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.revision == "0013_outbox_routing_target"
    assert migration.down_revision == "0012_customer_activation"
    assert len(operations.timeline) == 1
    operation, args, kwargs = operations.timeline[0]
    assert operation == "add_column"
    assert kwargs == {}
    assert args[0] == "outbox_events"
    assert args[1].name == "routing_target_provider_number_id"
    assert args[1].nullable is True
    assert args[1].type.length == 255


def test_routing_target_migration_downgrade_removes_column(monkeypatch) -> None:
    migration = _load_migration()
    operations = _Operations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert operations.timeline == [
        (
            "drop_column",
            ("outbox_events", "routing_target_provider_number_id"),
            {},
        )
    ]
