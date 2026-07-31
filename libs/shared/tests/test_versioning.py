import json
from uuid import UUID

import pytest

from presvo_contracts import (
    CURRENT_SCHEMA_VERSION,
    ContractError,
    VersionedContract,
    create_contract,
    dump_contract,
    dump_contract_json,
    parse_contract,
)


class ProbeContract(VersionedContract):
    value: str


class NestedValue(VersionedContract):
    value: str


class NestedProbeContract(VersionedContract):
    child: NestedValue


class IdentifierContract(VersionedContract):
    identifier: UUID


def test_producer_injects_version_and_forbids_extras() -> None:
    contract = create_contract(ProbeContract, value="known")
    assert contract.schema_version == CURRENT_SCHEMA_VERSION
    with pytest.raises(ContractError) as caught:
        create_contract(ProbeContract, value="known", typo="rejected")
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("value", [{}, {"schema_version": True}, {"schema_version": 2}])
def test_consumer_requires_supported_integer_version(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(ProbeContract, value)
    assert caught.value.code in {
        "missing_schema_version",
        "unsupported_schema_version",
    }


def test_consumer_ignores_additive_fields_but_producer_does_not() -> None:
    parsed = parse_contract(
        ProbeContract,
        {"schema_version": 1, "value": "known", "future": "ignored"},
    )
    assert dump_contract(parsed) == {"schema_version": 1, "value": "known"}


def test_consumer_ignores_additive_fields_in_nested_values() -> None:
    parsed = parse_contract(
        NestedProbeContract,
        {
            "schema_version": 1,
            "child": {"schema_version": 1, "value": "known", "future": "ignored"},
        },
    )
    assert dump_contract(parsed) == {
        "child": {"schema_version": 1, "value": "known"},
        "schema_version": 1,
    }


@pytest.mark.parametrize("value", ["{", b"{", "   ", "not json"])
def test_parser_rejects_malformed_json_without_validation_details(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(ProbeContract, value)
    assert caught.value.code == "malformed_json"
    assert caught.value.__cause__ is None
    assert "Expecting" not in str(caught.value)
    assert "Expecting" not in repr(caught.value)


@pytest.mark.parametrize("value", ["[]", b"null", 1, []])
def test_parser_rejects_non_object_payloads(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(ProbeContract, value)
    assert caught.value.code == "invalid_payload"
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "value",
    [
        {"schema_version": 1.0, "value": "known"},
        {"schema_version": "1", "value": "known"},
        {"schema_version": -1, "value": "known"},
    ],
)
def test_parser_rejects_invalid_versions(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(ProbeContract, value)
    assert caught.value.code == "unsupported_schema_version"
    assert caught.value.__cause__ is None


def test_errors_are_safe_and_hide_raw_validation_input() -> None:
    secret = "do-not-expose-this-value"
    with pytest.raises(ContractError) as caught:
        create_contract(ProbeContract, value=secret, typo="invalid")
    assert str(caught.value) == "ProbeContract rejected: invalid_payload"
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_dumpers_return_json_values_and_stable_json() -> None:
    contract = create_contract(
        IdentifierContract,
        identifier=UUID("12345678-1234-5678-1234-567812345678"),
    )
    dumped = dump_contract(contract)
    assert dumped == {
        "identifier": "12345678-1234-5678-1234-567812345678",
        "schema_version": 1,
    }
    assert all(not isinstance(value, UUID) for value in dumped.values())
    assert dump_contract_json(contract) == (
        '{"identifier":"12345678-1234-5678-1234-567812345678","schema_version":1}'
    )
    assert json.loads(dump_contract_json(contract)) == dumped
