from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from presvo_contracts import TranscriptAppendRequest


def _app() -> FastAPI:
    from app.core.contract_http import contract_request_openapi, parse_contract_request

    app = FastAPI()

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


def test_contract_http_openapi_requires_schema_version() -> None:
    schema = _app().openapi()["paths"]["/contract"]["post"]["requestBody"]["content"]["application/json"]["schema"]

    assert "schema_version" in schema["properties"]
    assert "schema_version" in schema["required"]
