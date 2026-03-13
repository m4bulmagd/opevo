def test_clerk_user_created_webhook_upserts_local_user(
    client,
    signed_clerk_headers,
    clerk_user_created_payload,
    clerk_user_created_payload_bytes,
) -> None:
    response = client.post(
        "/webhooks/clerk",
        content=clerk_user_created_payload_bytes,
        headers=signed_clerk_headers,
    )

    assert response.status_code == 202
