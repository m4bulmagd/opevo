from __future__ import annotations

import json

import pytest
from conftest import install_test_api_runtime
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from opevo_contracts import TranscriptAppendRequest


def _app() -> FastAPI:
    from app.core.contract_http import contract_request_openapi, parse_contract_request

    app = FastAPI()
    install_test_api_runtime(app)

    @app.post("/contract", openapi_extra=contract_request_openapi(TranscriptAppendRequest))
    async def contract(request: Request):
        return (await parse_contract_request(request, TranscriptAppendRequest)).model_dump()

    return app


def test_contract_http_parses_versioned_request_and_ignores_additive_fields() -> None:
    response = TestClient(_app()).post(
        "/contract",
        json={
            "schema_version": 1,
            "segment": {"sequence_number": 1, "speaker": "CALLER", "text": "hello"},
            "future": "ignored",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "segment": {"sequence_number": 1, "speaker": "CALLER", "text": "hello"},
    }


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b'{"schema_version":1,"segment":"TRANSCRIPT_SENTINEL"', "malformed_json"),
        (json.dumps(["TRANSCRIPT_SENTINEL"]).encode(), "invalid_payload"),
        (b'{"segment":"TRANSCRIPT_SENTINEL"}', "missing_schema_version"),
        (b'{"schema_version":2,"segment":"TRANSCRIPT_SENTINEL"}', "unsupported_schema_version"),
        (b'{"schema_version":1,"segment":{"sequence_number":0}}', "invalid_payload"),
    ],
)
def test_contract_http_rejects_unsafe_input_without_echoing_it(
    body: bytes, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    response = TestClient(_app()).post(
        "/contract", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": code}}
    assert "TRANSCRIPT_SENTINEL" not in response.text
    assert "TRANSCRIPT_SENTINEL" not in caplog.text


def _resolve_ref(document: dict, ref: str):
    value = document
    for part in ref.removeprefix("#/").split("/"):
        value = value[part]
    return value


def _assert_refs_resolve(value, document: dict) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            assert _resolve_ref(document, ref)
        for child in value.values():
            _assert_refs_resolve(child, document)
    elif isinstance(value, list):
        for child in value:
            _assert_refs_resolve(child, document)


def test_actual_contract_operations_have_complete_openapi_schemas() -> None:
    from app.routers.activation import router as activation_router
    from app.routers.agent import router as agent_router

    app = FastAPI()
    app.include_router(agent_router)
    app.include_router(activation_router)
    document = app.openapi()
    operations = [
        document["paths"]["/api/agent/calls/{call_id}/transcript"]["post"],
        document["paths"]["/api/agent/calls/{call_id}/complete"]["post"],
        document["paths"]["/api/activation/verification/{session_id}/complete"]["post"],
    ]

    for operation in operations:
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert "schema_version" in schema["properties"]
        assert "schema_version" in schema["required"]
        _assert_refs_resolve(schema, document)
    _assert_refs_resolve(document, document)


class _CapturingTelemetry:
    def __init__(self) -> None:
        self.attributes: list[dict[str, str]] = []

    def record_invalid_contract(self, **attributes: str) -> None:
        self.attributes.append(attributes)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "code"),
    [
        (
            b'{"schema_version":1,"segment":"RAW_INPUT_SENTINEL"',
            "malformed_json",
        ),
        (b'["RAW_INPUT_SENTINEL"]', "invalid_payload"),
        (b'{"segment":"RAW_INPUT_SENTINEL"}', "missing_schema_version"),
        (
            b'{"schema_version":2,"segment":"RAW_INPUT_SENTINEL"}',
            "unsupported_schema_version",
        ),
        (
            b'{"schema_version":1,"segment":{"sequence_number":0,"text":"RAW_INPUT_SENTINEL"}}',
            "invalid_payload",
        ),
    ],
)
async def test_parser_records_only_bounded_fields_and_suppresses_sensitive_chain(
    body: bytes,
    code: str,
) -> None:
    from app.core.contract_http import parse_contract_request

    telemetry = _CapturingTelemetry()
    app = FastAPI()
    install_test_api_runtime(app, observability=telemetry)
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "app": app}, receive=receive)

    with pytest.raises(HTTPException) as caught:
        await parse_contract_request(request, TranscriptAppendRequest)

    error = caught.value
    assert error.detail == {"code": code}
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    chain_text = " ".join(
        str(item)
        for item in (error, error.__cause__, error.__context__)
        if item is not None
    )
    assert "RAW_INPUT_SENTINEL" not in chain_text
    assert "'input'" not in chain_text
    assert telemetry.attributes == [
        {
            "contract_name": "TranscriptAppendRequest",
            "code": code,
            "transport": "http",
        }
    ]
