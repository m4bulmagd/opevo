import json
from operator import attrgetter
from pathlib import Path

import pytest

from contract_cases import CONTRACT_CASES, ContractCase
from presvo_contracts import SUPPORTED_SCHEMA_VERSIONS, dump_contract


FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_every_supported_version_has_a_complete_fixture_matrix() -> None:
    fixture_versions = {
        int(path.name.removeprefix("v"))
        for path in FIXTURE_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("v")
    }
    assert fixture_versions == SUPPORTED_SCHEMA_VERSIONS
    assert {path.stem for path in (FIXTURE_ROOT / "v1").glob("*.json")} == {
        case.fixture_name for case in CONTRACT_CASES
    }


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=attrgetter("fixture_name"))
def test_v1_fixture_matches_producer_and_consumer(case: ContractCase) -> None:
    fixture = json.loads((FIXTURE_ROOT / "v1" / f"{case.fixture_name}.json").read_text())
    assert dump_contract(case.producer) == fixture
    assert dump_contract(case.parser(fixture)) == fixture
