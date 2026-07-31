from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from presvo_contracts import TranscriptSegment as TranscriptAppendRequest
from app.services.transcript_service import (
    TranscriptAuthorizationError,
    TranscriptService,
)


def _service(*, locked_call, locked_config):
    call_repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=locked_call),
    )
    config_repository = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=locked_config),
    )
    message_repository = SimpleNamespace(
        get_by_sequence=AsyncMock(return_value=None),
        insert_with_unique_backstop=AsyncMock(),
    )
    return (
        TranscriptService(
            SimpleNamespace(),
            call_repository=call_repository,
            agent_config_repository=config_repository,
            message_repository=message_repository,
        ),
        message_repository,
    )


@pytest.mark.anyio
async def test_locked_call_owner_change_rejects_stale_authenticated_claims() -> None:
    call_id = uuid4()
    signed_user_id = uuid4()
    signed_config_id = uuid4()
    service, messages = _service(
        locked_call=SimpleNamespace(
            id=call_id,
            user_id=uuid4(),
            agent_config_id=signed_config_id,
            status="connected",
        ),
        locked_config=SimpleNamespace(
            id=signed_config_id,
            user_id=signed_user_id,
        ),
    )

    with pytest.raises(TranscriptAuthorizationError):
        await service.append(
            call_id=call_id,
            item=TranscriptAppendRequest(
                sequence_number=1,
                speaker="CALLER",
                text="Must not persist",
            ),
            expected_user_id=signed_user_id,
            expected_agent_config_id=signed_config_id,
        )

    messages.insert_with_unique_backstop.assert_not_awaited()


@pytest.mark.anyio
async def test_locked_config_owner_change_rejects_stale_authenticated_claims() -> None:
    call_id = uuid4()
    signed_user_id = uuid4()
    signed_config_id = uuid4()
    service, messages = _service(
        locked_call=SimpleNamespace(
            id=call_id,
            user_id=signed_user_id,
            agent_config_id=signed_config_id,
            status="connected",
        ),
        locked_config=SimpleNamespace(
            id=signed_config_id,
            user_id=uuid4(),
        ),
    )

    with pytest.raises(TranscriptAuthorizationError):
        await service.merge_recovery(
            call_id=call_id,
            transcript=[],
            expected_user_id=signed_user_id,
            expected_agent_config_id=signed_config_id,
        )

    messages.insert_with_unique_backstop.assert_not_awaited()
