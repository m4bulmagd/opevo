import json
from operator import attrgetter
from pathlib import Path

import pytest

from contract_cases import CONTRACT_CASES, ContractCase
from presvo_contracts import SUPPORTED_SCHEMA_VERSIONS, dump_contract


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def assert_fixture_matrix_complete(
    fixture_root: Path,
    supported_versions: frozenset[int],
    expected_names: set[str],
) -> None:
    fixture_versions = {
        int(path.name.removeprefix("v"))
        for path in fixture_root.iterdir()
        if path.is_dir() and path.name.startswith("v")
    }
    assert fixture_versions == supported_versions
    for version in supported_versions:
        assert {path.stem for path in (fixture_root / f"v{version}").glob("*.json")} == expected_names


def test_fixture_policy_rejects_incomplete_future_supported_directory(tmp_path: Path) -> None:
    expected_names = {case.fixture_name for case in CONTRACT_CASES}
    v1 = tmp_path / "v1"
    v1.mkdir()
    for fixture_name in expected_names:
        (v1 / f"{fixture_name}.json").touch()
    (tmp_path / "v2").mkdir()

    with pytest.raises(AssertionError):
        assert_fixture_matrix_complete(tmp_path, frozenset({1, 2}), expected_names)


def test_every_supported_version_has_a_complete_fixture_matrix() -> None:
    assert_fixture_matrix_complete(
        FIXTURE_ROOT,
        SUPPORTED_SCHEMA_VERSIONS,
        {case.fixture_name for case in CONTRACT_CASES},
    )


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=attrgetter("fixture_name"))
def test_v1_fixture_matches_producer_and_consumer(case: ContractCase) -> None:
    fixture = json.loads((FIXTURE_ROOT / "v1" / f"{case.fixture_name}.json").read_text())
    assert dump_contract(case.producer) == fixture
    assert dump_contract(case.parser(fixture)) == fixture
