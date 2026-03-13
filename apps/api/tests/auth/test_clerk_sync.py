import pytest


@pytest.mark.anyio
async def test_clerk_user_created_webhook_upserts_local_user(
    async_client,
    signed_clerk_headers,
    clerk_user_created_payload,
    clerk_user_created_payload_bytes,
) -> None:
    response = await async_client.post(
        "/webhooks/clerk",
        content=clerk_user_created_payload_bytes,
        headers=signed_clerk_headers,
    )

    assert response.status_code == 202
