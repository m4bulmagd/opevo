"""Safe FastAPI adapters for versioned wire contracts.

FastAPI's normal validation errors include the rejected input. Agent payloads
contain transcripts, so routes at this boundary must read raw bytes and expose
only stable contract error codes.
"""

from typing import TypeVar

from fastapi import HTTPException, Request, status
from presvo_contracts import ContractError, VersionedContract, parse_contract

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
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": model_type.model_json_schema()}
            },
        }
    }
