"""Safe FastAPI adapters for versioned wire contracts.

FastAPI's normal validation errors include the rejected input. Agent payloads
contain transcripts, so routes at this boundary must read raw bytes and expose
only stable contract error codes.
"""

from copy import deepcopy
from typing import TypeVar

from fastapi import HTTPException, Request, status
from opevo_contracts import ContractError, VersionedContract, parse_contract

from app.core.observability import get_request_observability


ContractT = TypeVar("ContractT", bound=VersionedContract)


async def parse_contract_request(
    request: Request,
    model_type: type[ContractT],
) -> ContractT:
    try:
        return parse_contract(model_type, await request.body())
    except ContractError as error:
        get_request_observability(request).record_invalid_contract(
            contract_name=error.contract_name,
            code=error.code,
            transport="http",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code},
        ) from None


def contract_request_openapi(
    model_type: type[VersionedContract],
) -> dict[str, object]:
    schema = _inline_local_schema_references(model_type.model_json_schema())
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": schema}
            },
        }
    }


def _inline_local_schema_references(schema: dict[str, object]) -> dict[str, object]:
    """Inline Pydantic's operation-local definitions.

    ``openapi_extra`` is merged into an operation, so ``#/$defs/...`` points at
    the OpenAPI document root rather than the schema object where Pydantic put
    its definitions. These wire models are intentionally nonrecursive; a
    focused dereference keeps the generated operation self-contained.
    """

    definitions = schema.get("$defs", {})
    if not isinstance(definitions, dict):
        return schema

    def inline(value: object, resolving: frozenset[str] = frozenset()) -> object:
        if isinstance(value, list):
            return [inline(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value

        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            target = definitions.get(name)
            if not isinstance(target, dict) or name in resolving:
                raise ValueError("invalid or recursive contract OpenAPI schema")
            expanded = inline(deepcopy(target), resolving | {name})
            if not isinstance(expanded, dict):
                raise ValueError("invalid contract OpenAPI definition")
            siblings = {
                key: inline(item, resolving)
                for key, item in value.items()
                if key != "$ref"
            }
            return {**expanded, **siblings}

        return {
            key: inline(item, resolving)
            for key, item in value.items()
            if key != "$defs"
        }

    result = inline(deepcopy(schema))
    if not isinstance(result, dict):
        raise ValueError("invalid contract OpenAPI schema")
    return result
