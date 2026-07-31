"""Versioned JSON wire-contract primitives."""

import json
from collections.abc import Mapping
from typing import Annotated, Literal, TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
    field_validator,
)


CURRENT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({CURRENT_SCHEMA_VERSION})

ContractErrorCode = Literal[
    "malformed_json",
    "missing_schema_version",
    "unsupported_schema_version",
    "invalid_payload",
]
NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ContractError(ValueError):
    def __init__(self, contract_name: str, code: ContractErrorCode) -> None:
        self._contract_name = contract_name
        self._code = code
        super().__init__(f"{contract_name} rejected: {code}")

    @property
    def contract_name(self) -> str:
        return self._contract_name

    @property
    def code(self) -> ContractErrorCode:
        return self._code


class WireValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionedContract(WireValue):
    schema_version: Literal[1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value


ContractT = TypeVar("ContractT", bound=VersionedContract)


def create_contract(
    model_type: type[ContractT],
    /,
    **values: object,
) -> ContractT:
    payload = dict(values)
    payload.setdefault("schema_version", CURRENT_SCHEMA_VERSION)
    try:
        return model_type.model_validate(payload, extra="forbid")
    except (TypeError, ValidationError):
        raise ContractError(model_type.__name__, "invalid_payload") from None


def parse_contract(
    model_type: type[ContractT],
    value: object,
) -> ContractT:
    payload = _decode_versioned_object(model_type.__name__, value)
    try:
        return model_type.model_validate(payload, extra="ignore")
    except (TypeError, ValidationError):
        raise ContractError(model_type.__name__, "invalid_payload") from None


def dump_contract(contract: VersionedContract) -> dict[str, object]:
    try:
        return cast(dict[str, object], contract.model_dump(mode="json", exclude_none=False))
    except (TypeError, ValueError, ValidationError):
        raise ContractError(type(contract).__name__, "invalid_payload") from None


def dump_contract_json(contract: VersionedContract) -> str:
    try:
        return json.dumps(dump_contract(contract), separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, ValidationError):
        raise ContractError(type(contract).__name__, "invalid_payload") from None


def _decode_versioned_object(contract_name: str, value: object) -> Mapping[str, object]:
    payload = value
    if isinstance(value, (str, bytes)):
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            raise ContractError(contract_name, "malformed_json") from None

    if not isinstance(payload, Mapping):
        raise ContractError(contract_name, "invalid_payload")
    if "schema_version" not in payload:
        raise ContractError(contract_name, "missing_schema_version")

    schema_version = payload["schema_version"]
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ContractError(contract_name, "unsupported_schema_version")
    return cast(Mapping[str, object], payload)
