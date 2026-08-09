import os
import subprocess
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]


def test_external_identity_migration_renders_a_data_preserving_rename() -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost/opevo",
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "0018_external_user_identity",
            "--sql",
        ],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered_sql = result.stdout
    assert "ALTER TABLE users RENAME clerk_user_id TO external_user_id" in rendered_sql
    assert (
        "ALTER TABLE users RENAME CONSTRAINT uq_users_clerk_user_id "
        "TO uq_users_external_user_id"
    ) in rendered_sql
    assert (
        "ALTER INDEX ix_users_clerk_user_id RENAME TO ix_users_external_user_id"
        in rendered_sql
    )
