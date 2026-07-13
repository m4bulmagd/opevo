import time
from collections.abc import Mapping

import stripe
from fastapi import HTTPException, status
from svix.webhooks import Webhook, WebhookVerificationError


INVALID_WEBHOOK_DETAIL = "Invalid webhook signature"


def verify_stripe_signature(
    *,
    secret: str,
    payload: bytes,
    signature_header: str | None,
    max_age_seconds: int = 300,
) -> None:
    if not signature_header:
        raise _invalid_signature()

    try:
        stripe.WebhookSignature.verify_header(
            payload=payload.decode("utf-8"),
            header=signature_header,
            secret=secret,
            tolerance=max_age_seconds,
        )
        timestamp = _stripe_timestamp(signature_header)
    except (stripe.SignatureVerificationError, UnicodeDecodeError, ValueError):
        raise _invalid_signature() from None

    # Stripe's SDK rejects old signatures, but intentionally accepts signatures
    # dated in the future. Bound both sides of the replay window explicitly.
    if timestamp > time.time() + max_age_seconds:
        raise _invalid_signature()


def verify_svix_signature(
    *,
    secret: str,
    payload: bytes,
    headers: Mapping[str, str],
) -> str:
    try:
        Webhook(secret).verify(payload, headers)
    except (WebhookVerificationError, ValueError):
        raise _invalid_signature() from None

    return headers.get("svix-id", "")


def _stripe_timestamp(signature_header: str) -> int:
    for component in signature_header.split(","):
        key, separator, value = component.partition("=")
        if key == "t" and separator and value:
            return int(value)
    raise ValueError("missing timestamp")


def _invalid_signature() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=INVALID_WEBHOOK_DETAIL,
    )
