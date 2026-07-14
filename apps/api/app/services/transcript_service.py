from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from app.repositories.agent_config_repository import AgentConfigRepository
from app.repositories.call_repository import CallRepository
from app.repositories.message_repository import MessageRepository
from app.schemas.agent_runtime import TranscriptAppendRequest


TRANSCRIPT_OPEN_CALL_STATUSES = frozenset(
    {"pending", "connected", "ending", "finalizing"}
)


class TranscriptError(Exception):
    code = "transcript_error"


class TranscriptCallNotFoundError(TranscriptError):
    code = "call_not_found"


class TranscriptCallNotAcceptingError(TranscriptError):
    code = "call_not_accepting_transcript"


class TranscriptAuthorizationError(TranscriptError):
    code = "invalid_agent_token"


class TranscriptSequenceConflictError(TranscriptError):
    code = "sequence_conflict"


@dataclass(frozen=True)
class TranscriptAppendResult:
    status: str
    sequence_number: int


class TranscriptService:
    def __init__(
        self,
        session,
        *,
        call_repository: CallRepository | None = None,
        agent_config_repository: AgentConfigRepository | None = None,
        message_repository: MessageRepository | None = None,
    ) -> None:
        self.session = session
        self.call_repository = call_repository or CallRepository(session)
        self.agent_config_repository = agent_config_repository or AgentConfigRepository(
            session
        )
        self.message_repository = message_repository or MessageRepository(session)

    async def append(
        self,
        *,
        call_id: UUID,
        item: TranscriptAppendRequest,
        expected_user_id: UUID | None = None,
        expected_agent_config_id: UUID | None = None,
    ) -> TranscriptAppendResult:
        call = await self._lock_and_authorize(
            call_id=call_id,
            expected_user_id=expected_user_id,
            expected_agent_config_id=expected_agent_config_id,
        )
        return await self._merge_item(
            call_id=call_id,
            call_status=call.status,
            item=item,
            allow_terminal_new=False,
        )

    async def merge_recovery(
        self,
        *,
        call_id: UUID,
        transcript: Sequence[TranscriptAppendRequest | dict],
        expected_user_id: UUID | None = None,
        expected_agent_config_id: UUID | None = None,
    ) -> list[TranscriptAppendResult]:
        call = await self._lock_and_authorize(
            call_id=call_id,
            expected_user_id=expected_user_id,
            expected_agent_config_id=expected_agent_config_id,
        )

        normalized = self.normalize_recovery(transcript)
        results: list[TranscriptAppendResult] = []
        for item in normalized:
            results.append(
                await self._merge_item(
                    call_id=call_id,
                    call_status=call.status,
                    item=item,
                    allow_terminal_new=True,
                )
            )
        return results

    async def _lock_and_authorize(
        self,
        *,
        call_id: UUID,
        expected_user_id: UUID | None,
        expected_agent_config_id: UUID | None,
    ):
        call = await self.call_repository.get_by_id_for_update(call_id)
        if call is None:
            raise TranscriptCallNotFoundError

        if expected_user_id is None and expected_agent_config_id is None:
            return call
        if expected_user_id is None or expected_agent_config_id is None:
            raise TranscriptAuthorizationError
        if (
            call.user_id != expected_user_id
            or call.agent_config_id != expected_agent_config_id
        ):
            raise TranscriptAuthorizationError

        agent_config = await self.agent_config_repository.get_by_id_for_update(
            expected_agent_config_id
        )
        if agent_config is None or agent_config.user_id != expected_user_id:
            raise TranscriptAuthorizationError
        return call

    @staticmethod
    def normalize_recovery(
        transcript: Sequence[TranscriptAppendRequest | dict],
    ) -> list[TranscriptAppendRequest]:
        normalized: list[TranscriptAppendRequest] = []
        for index, raw_item in enumerate(transcript, start=1):
            if isinstance(raw_item, TranscriptAppendRequest):
                normalized.append(raw_item)
                continue
            item = dict(raw_item)
            text = item.get("text")
            if isinstance(text, str) and not text.strip():
                continue
            item.setdefault("sequence_number", index)
            try:
                normalized.append(TranscriptAppendRequest.model_validate(item))
            except ValidationError:
                raise
        return normalized

    async def _merge_item(
        self,
        *,
        call_id: UUID,
        call_status: str,
        item: TranscriptAppendRequest,
        allow_terminal_new: bool,
    ) -> TranscriptAppendResult:
        existing = await self.message_repository.get_by_sequence(
            call_id=call_id,
            sequence_number=item.sequence_number,
        )
        if existing is not None:
            return self._classify_existing(existing, item)

        if not allow_terminal_new and call_status not in TRANSCRIPT_OPEN_CALL_STATUSES:
            raise TranscriptCallNotAcceptingError

        stored, inserted = await self.message_repository.insert_with_unique_backstop(
            call_id=call_id,
            sequence_number=item.sequence_number,
            speaker=item.speaker.value,
            text=item.text,
        )
        if not inserted:
            return self._classify_existing(stored, item)
        return TranscriptAppendResult(
            status="stored",
            sequence_number=item.sequence_number,
        )

    @staticmethod
    def _classify_existing(existing, item: TranscriptAppendRequest) -> TranscriptAppendResult:
        if existing.speaker != item.speaker.value or existing.text != item.text:
            raise TranscriptSequenceConflictError
        return TranscriptAppendResult(
            status="duplicate",
            sequence_number=item.sequence_number,
        )
