import json


def test_subscription_activation_provisions_usage_ledger(
    client,
    signed_stripe_headers_factory,
    stripe_subscription_created_payload,
) -> None:
    response = client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_subscription_created_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_subscription_created_payload),
    )

    assert response.status_code == 202


def test_invoice_paid_resets_minutes(
    client,
    signed_stripe_headers_factory,
    stripe_invoice_paid_payload,
) -> None:
    response = client.post(
        "/webhooks/stripe",
        content=json.dumps(stripe_invoice_paid_payload, separators=(",", ":")).encode("utf-8"),
        headers=signed_stripe_headers_factory(stripe_invoice_paid_payload),
    )

    assert response.status_code == 202
