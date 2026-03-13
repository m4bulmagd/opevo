import pytest


@pytest.mark.anyio
async def test_protected_route_rejects_token_without_local_user(
    async_client,
    valid_clerk_but_missing_local_user_token,
) -> None:
    response = await async_client.get(
        "/api/agent/config",
        headers={"Authorization": f"Bearer {valid_clerk_but_missing_local_user_token}"},
    )

    assert response.status_code == 401
