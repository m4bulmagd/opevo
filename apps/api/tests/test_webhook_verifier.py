import hashlib
import hmac
import json
import time

import pytest


def _unknown_stripe_event(source: dict) -> dict:
    return {**source, "id": "evt_signature_test", "type": "signature.test"}


def _stripe_header_signed_with(payload: dict, secret: bytes) -> dict[str, str]:
    timestamp = int(time.time())
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        secret,
        f"{timestamp}.".encode("utf-8") + payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return {"stripe-signature": f"t={timestamp},v1={signature}"}


@pytest.mark.anyio
async def test_valid_stripe_signature_is_accepted(
    async_client,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    payload = _unknown_stripe_event(stripe_subscription_created_payload)

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(payload),
    )

    assert response.status_code == 202


@pytest.mark.anyio
@pytest.mark.parametrize(
    "signature_header",
    [
        pytest.param("old", id="older-than-five-minutes"),
        pytest.param("future", id="future-beyond-five-minutes"),
        pytest.param("malformed", id="malformed-component"),
        pytest.param("wrong-secret", id="wrong-secret"),
        pytest.param("missing", id="missing-header"),
    ],
)
async def test_invalid_stripe_signatures_are_bad_requests_not_server_errors(
    async_client,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
    signature_header: str,
) -> None:
    payload = _unknown_stripe_event(stripe_subscription_created_payload)
    if signature_header == "old":
        headers = signed_stripe_headers_factory(payload, timestamp=int(time.time()) - 600)
    elif signature_header == "future":
        headers = signed_stripe_headers_factory(payload, timestamp=int(time.time()) + 600)
    elif signature_header == "malformed":
        headers = {"stripe-signature": f"t={int(time.time())},v1"}
    elif signature_header == "wrong-secret":
        headers = _stripe_header_signed_with(payload, b"wrong-stripe-secret")
    else:
        headers = {}

    response = await async_client.post(
        "/webhooks/stripe",
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
    )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_invalid_clerk_signature_is_a_safe_bad_request(
    async_client,
    clerk_user_created_payload_bytes,
    signed_clerk_headers,
    caplog,
) -> None:
    sentinel = "SIGNATURE_SENTINEL_DO_NOT_LOG"
    headers = {**signed_clerk_headers, "svix-signature": f"v1,{sentinel}"}

    response = await async_client.post(
        "/webhooks/clerk",
        content=clerk_user_created_payload_bytes,
        headers=headers,
    )

    assert response.status_code == 400
    assert sentinel not in caplog.text
    assert clerk_user_created_payload_bytes.decode("utf-8") not in caplog.text
