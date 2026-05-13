from pathlib import Path
import re


ALEMBIC_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
REVISION_RE = re.compile(r'^revision:\s*str\s*=\s*"([^"]+)"', re.MULTILINE)


def test_alembic_revision_ids_fit_version_table() -> None:
    revision_ids: list[str] = []

    for path in ALEMBIC_VERSIONS_DIR.glob("*.py"):
        match = REVISION_RE.search(path.read_text())
        if match is not None:
            revision_ids.append(match.group(1))

    assert revision_ids
    assert all(len(revision_id) <= 32 for revision_id in revision_ids)
