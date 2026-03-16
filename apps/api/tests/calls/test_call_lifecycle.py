import pytest

from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.user_repository import UserRepository


@pytest.mark.anyio
async def test_create_user_and_default_agent_config(
    db_session,
) -> None:
    user_repository = UserRepository(db_session)
    agent_config_repository = AgentConfigRepository(db_session)

    user = await user_repository.create(
        clerk_user_id="user_123",
        email="test@example.com",
    )
    config = await agent_config_repository.create_default(user.id)

    assert config.user_id == user.id
    assert config.pipeline_mode == "stt_llm_tts"
