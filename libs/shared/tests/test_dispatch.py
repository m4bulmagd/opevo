from uuid import UUID

import pytest

from presvo_contracts import ContractError, create_contract, dump_contract
from presvo_contracts.dispatch import (
    AGENT_NAME_MAX_LENGTH,
    KNOWLEDGE_BASE_MAX_LENGTH,
    OWNER_CONTEXT_MAX_LENGTH,
    OWNER_NAME_MAX_LENGTH,
    SYSTEM_PROMPT_MAX_LENGTH,
    CustomerCallDispatch,
    ForwardingVerificationDispatch,
    parse_dispatch,
)


def valid_customer_dispatch() -> dict[str, object]:
    return {
        "schema_version": 1,
        "job_type": "customer_call",
        "call_id": "12345678-1234-5678-1234-567812345678",
        "user_id": "22345678-1234-5678-1234-567812345678",
        "agent_config_id": "32345678-1234-5678-1234-567812345678",
        "agent_identity": "agent-identity",
        "agent_name": "Agent",
        "owner_name": "Owner",
        "owner_context": "Customer success team",
        "system_prompt": "Be helpful.",
        "knowledge_base": "Knowledge",
        "pipeline_mode": "stt_llm_tts",
        "minutes_remaining": 0,
        "allowed_duration_seconds": 1,
        "dispatch_token": "dispatch-secret",
    }


def valid_verification_dispatch() -> dict[str, object]:
    return {
        "schema_version": 1,
        "job_type": "forwarding_verification",
        "verification_session_id": "42345678-1234-5678-1234-567812345678",
        "user_id": "22345678-1234-5678-1234-567812345678",
        "agent_identity": "agent-identity",
        "completion_token": "completion-secret",
        "message": "Forwarding test successful. Return to Presvo to go live.",
        "tts_provider": "speechmatics",
    }


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("agent_name", AGENT_NAME_MAX_LENGTH),
        ("owner_name", OWNER_NAME_MAX_LENGTH),
        ("owner_context", OWNER_CONTEXT_MAX_LENGTH),
        ("system_prompt", SYSTEM_PROMPT_MAX_LENGTH),
        ("knowledge_base", KNOWLEDGE_BASE_MAX_LENGTH),
    ],
)
def test_customer_content_fields_enforce_exact_length_bounds(
    field: str, limit: int
) -> None:
    for value in ("x", "x" * limit):
        payload = valid_customer_dispatch()
        payload[field] = value
        assert isinstance(parse_dispatch(payload), CustomerCallDispatch)

    for value in ("", "x" * (limit + 1)):
        payload = valid_customer_dispatch()
        payload[field] = value
        with pytest.raises(ContractError) as caught:
            parse_dispatch(payload)
        assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("field", ["agent_identity", "dispatch_token"])
@pytest.mark.parametrize("value", ["", "   "])
def test_customer_identity_and_token_reject_blank_values(field: str, value: str) -> None:
    payload = valid_customer_dispatch()
    payload[field] = value
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("field", ["agent_identity", "completion_token"])
@pytest.mark.parametrize("value", ["", "   "])
def test_verification_identity_and_token_reject_blank_values(
    field: str, value: str
) -> None:
    payload = valid_verification_dispatch()
    payload[field] = value
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize(
    "field",
    ["call_id", "user_id", "agent_config_id"],
)
def test_customer_requires_valid_uuids(field: str) -> None:
    payload = valid_customer_dispatch()
    payload[field] = "not-a-uuid"
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


def test_valid_customer_uuid_fields_are_parsed_as_uuids() -> None:
    dispatch = parse_dispatch(valid_customer_dispatch())
    assert isinstance(dispatch, CustomerCallDispatch)
    assert dispatch.call_id == UUID("12345678-1234-5678-1234-567812345678")


@pytest.mark.parametrize("field", ["verification_session_id", "user_id"])
def test_verification_requires_valid_uuids(field: str) -> None:
    payload = valid_verification_dispatch()
    payload[field] = "not-a-uuid"
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("value", [-1, True, 0.0, "0"])
def test_customer_minutes_remaining_requires_nonnegative_strict_integer(value: object) -> None:
    payload = valid_customer_dispatch()
    payload["minutes_remaining"] = value
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_customer_duration_requires_positive_strict_integer(value: object) -> None:
    payload = valid_customer_dispatch()
    payload["allowed_duration_seconds"] = value
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("mode", ["stt_llm_tts", "sts"])
def test_customer_accepts_both_pipeline_modes(mode: str) -> None:
    payload = valid_customer_dispatch()
    payload["pipeline_mode"] = mode
    dispatch = parse_dispatch(payload)
    assert isinstance(dispatch, CustomerCallDispatch)
    assert dispatch.pipeline_mode == mode


@pytest.mark.parametrize("provider", ["speechmatics", "elevenlabs"])
def test_verification_accepts_both_tts_providers(provider: str) -> None:
    payload = valid_verification_dispatch()
    payload["tts_provider"] = provider
    dispatch = parse_dispatch(payload)
    assert isinstance(dispatch, ForwardingVerificationDispatch)
    assert dispatch.tts_provider == provider


@pytest.mark.parametrize("job_type", ["unknown", "customer-call", ""])
def test_dispatch_rejects_unknown_job_type(job_type: str) -> None:
    payload = valid_customer_dispatch()
    payload["job_type"] = job_type
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


def test_dispatch_requires_explicit_job_type() -> None:
    payload = valid_customer_dispatch()
    payload.pop("job_type")
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"


def test_producer_rejects_extra_dispatch_fields() -> None:
    payload = valid_customer_dispatch()
    payload["future"] = "not producer-safe"
    with pytest.raises(ContractError) as caught:
        create_contract(CustomerCallDispatch, **payload)
    assert caught.value.code == "invalid_payload"


def test_consumer_ignores_additive_dispatch_fields() -> None:
    payload = valid_customer_dispatch()
    payload["future"] = {"nested": {"field": "ignored"}}
    dispatch = parse_dispatch(payload)
    assert dump_contract(dispatch).get("future") is None


def test_tokens_are_hidden_from_representations() -> None:
    customer = parse_dispatch(valid_customer_dispatch())
    verification = parse_dispatch(valid_verification_dispatch())
    assert "dispatch-secret" not in repr(customer)
    assert "completion-secret" not in repr(verification)
