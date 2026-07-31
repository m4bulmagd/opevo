import json
from pathlib import Path

import pytest
from presvo_contracts import (
    CustomerCallDispatch,
    ForwardingVerificationDispatch,
    dump_contract,
    parse_dispatch,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "libs/shared/tests/fixtures/v1"


@pytest.mark.parametrize(
    ("fixture_name", "contract_type"),
    [
        ("customer_call_dispatch.json", CustomerCallDispatch),
        ("forwarding_verification_dispatch.json", ForwardingVerificationDispatch),
    ],
)
def test_shared_dispatch_fixtures_round_trip_at_the_api_boundary(
    fixture_name: str,
    contract_type: type[CustomerCallDispatch] | type[ForwardingVerificationDispatch],
) -> None:
    fixture = json.loads((FIXTURE_ROOT / fixture_name).read_text())

    parsed = parse_dispatch(json.dumps(fixture))

    assert isinstance(parsed, contract_type)
    assert dump_contract(parsed) == fixture
