import pytest

from app.repositories.call_repository import CallRepository
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


@pytest.mark.anyio
async def test_call_repository_excludes_soft_deleted_calls(db_session, active_user) -> None:
    repository = CallRepository(db_session)
    visible_call = await repository.create_pending(
        user_id=active_user.id,
        caller_number="+33111111111",
    )
    visible_call.status = "completed"
    await db_session.flush()
    deleted_call = await repository.create_pending(
        user_id=active_user.id,
        caller_number="+33222222222",
    )
    await db_session.commit()

    await repository.soft_delete(deleted_call)
    await db_session.commit()

    visible_calls = await repository.list_visible_by_user_id(active_user.id)

    assert [call.id for call in visible_calls] == [visible_call.id]
