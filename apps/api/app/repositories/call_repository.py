from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, cast
from uuid import UUID

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.call import Call


CALL_STATE_GRAPH: dict[str, frozenset[str]] = {
    "pending": frozenset({"connected", "failed"}),
    "connected": frozenset({"ending"}),
    "ending": frozenset({"finalizing"}),
    "finalizing": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
}

CALL_FAILURE_CODES = frozenset(
    {
        "dispatch_ineligible",
        "dispatch_conflict",
        "dispatch_configuration",
        "dispatch_provider_exhausted",
        "dispatch_timeout",
        "caller_left_before_connect",
        "finalization_exhausted",
        "legacy_failure",
    }
)

PHONE_QUERY_PATTERN = re.compile(r"^[0-9\s()+.\-]+$")


@dataclass(frozen=True)
class CallHistoryPage:
    calls: list[Call]
    total: int


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class CallObservabilitySnapshot:
    current: dict[str, int]
    stale: dict[str, int]


class CallTransitionError(ValueError):
    pass


class CallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, call_id: UUID) -> Call | None:
        return await self.session.get(Call, call_id)

    async def has_active_by_user_id(self, user_id: UUID) -> bool:
        active_call_id = await self.session.scalar(
            select(Call.id)
            .where(
                Call.user_id == user_id,
                Call.status.in_(("pending", "connected", "ending", "finalizing")),
                Call.deleted_at.is_(None),
            )
            .limit(1)
        )
        return active_call_id is not None

    async def detach_phone_number(self, phone_number_id: UUID) -> int:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Call)
                .where(Call.phone_number_id == phone_number_id)
                .values(phone_number_id=None)
            ),
        )
        return int(result.rowcount or 0)

    async def observability_snapshot(
        self,
        now: datetime,
        settings,
    ) -> CallObservabilitySnapshot:
        states = (
            "pending",
            "connected",
            "ending",
            "finalizing",
            "completed",
            "failed",
        )
        stale_expression = case(
            (
                (Call.status == "pending")
                & (
                    Call.state_changed_at
                    <= now
                    - timedelta(
                        seconds=settings.call_reconciliation_pending_stale_seconds
                    )
                ),
                1,
            ),
            (
                (Call.status == "connected")
                & (
                    Call.state_changed_at
                    <= now
                    - timedelta(
                        seconds=settings.call_reconciliation_connected_stale_seconds
                    )
                ),
                1,
            ),
            (
                (Call.status == "ending")
                & (
                    Call.state_changed_at
                    <= now
                    - timedelta(
                        seconds=settings.call_reconciliation_ending_grace_seconds
                    )
                ),
                1,
            ),
            (
                (Call.status == "finalizing")
                & (
                    Call.state_changed_at
                    <= now
                    - timedelta(
                        seconds=settings.call_reconciliation_finalizing_lease_seconds
                    )
                ),
                1,
            ),
            else_=0,
        )
        rows = await self.session.execute(
            select(
                Call.status,
                func.count(Call.id),
                func.sum(stale_expression),
            )
            .where(Call.status.in_(states), Call.deleted_at.is_(None))
            .group_by(Call.status)
        )
        current = {state: 0 for state in states}
        stale = {state: 0 for state in states}
        for state, count, stale_count in rows:
            current[state] = int(count)
            stale[state] = int(stale_count or 0)
        return CallObservabilitySnapshot(current=current, stale=stale)

    async def get_by_id_for_update(self, call_id: UUID) -> Call | None:
        result = await self.session.execute(
            select(Call)
            .where(Call.id == call_id, Call.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_id_including_deleted_for_update(
        self,
        call_id: UUID,
    ) -> Call | None:
        result = await self.session.execute(
            select(Call)
            .where(Call.id == call_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_stale_pending_ids(
        self,
        *,
        stale_before: datetime,
        limit: int,
    ) -> list[UUID]:
        result = await self.session.execute(
            select(Call.id)
            .where(
                Call.status == "pending",
                Call.state_changed_at <= stale_before,
                Call.deleted_at.is_(None),
            )
            .order_by(Call.state_changed_at, Call.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def claim_stale_reconciliation_rows(
        self,
        *,
        connected_before: datetime,
        ending_before: datetime,
        finalizing_before: datetime,
        limit: int,
    ) -> list[Call]:
        result = await self.session.execute(
            select(Call)
            .where(
                Call.deleted_at.is_(None),
                or_(
                    (Call.status == "connected")
                    & (Call.state_changed_at <= connected_before),
                    (Call.status == "ending")
                    & (Call.state_changed_at <= ending_before),
                    (Call.status == "finalizing")
                    & (Call.state_changed_at <= finalizing_before),
                )
            )
            .order_by(Call.state_changed_at, Call.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars())

    async def transition(
        self,
        call_id: UUID,
        *,
        from_states: set[str],
        to_state: str,
        failure_code: str | None = None,
    ) -> Call:
        call = await self.get_by_id_for_update(call_id)
        if call is None:
            raise CallTransitionError("Call not found")
        if call.status not in from_states:
            raise CallTransitionError("Call state transition precondition failed")
        if to_state not in CALL_STATE_GRAPH.get(call.status, frozenset()):
            raise CallTransitionError("Illegal call state transition")
        if to_state == "failed":
            if failure_code not in CALL_FAILURE_CODES:
                raise CallTransitionError("Invalid call failure code")
        elif failure_code is not None:
            raise CallTransitionError("Call failure code requires failed state")

        call.status = to_state
        call.failure_code = failure_code
        call.state_changed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return call

    @staticmethod
    def _visible_call_predicates(
        user_id: UUID,
        query: str | None,
    ) -> tuple[ColumnElement[bool], ...]:
        predicates: list[ColumnElement[bool]] = [
            Call.user_id == user_id,
            Call.deleted_at.is_(None),
        ]
        if query is None:
            return tuple(predicates)

        escaped_query = _escape_like(query)
        search_predicates: list[ColumnElement[bool]] = [
            Call.summary_text.ilike(f"%{escaped_query}%", escape="\\"),
            Call.summary_data["caller_intent"]
            .as_string()
            .ilike(f"%{escaped_query}%", escape="\\"),
        ]
        if PHONE_QUERY_PATTERN.fullmatch(query):
            digits = "".join(character for character in query if character.isdigit())
            if len(digits) >= 3:
                search_predicates.append(Call.caller_number.ilike(f"%{digits}%"))
            domestic_digits = digits[1:]
            if digits.startswith("0") and len(domestic_digits) >= 3:
                search_predicates.append(
                    Call.caller_number.ilike(f"%{domestic_digits}%")
                )

        predicates.append(or_(*search_predicates))
        return tuple(predicates)

    async def list_visible_page_by_user_id(
        self,
        user_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
    ) -> CallHistoryPage:
        predicates = self._visible_call_predicates(user_id, query)
        total = await self.session.scalar(
            select(func.count(Call.id)).where(*predicates)
        )
        result = await self.session.execute(
            select(Call)
            .where(*predicates)
            .order_by(
                Call.started_at.desc().nullslast(),
                Call.created_at.desc(),
                Call.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return CallHistoryPage(
            calls=list(result.scalars()),
            total=int(total or 0),
        )

    async def get_visible_by_id(self, call_id: UUID, *, user_id: UUID) -> Call | None:
        result = await self.session.execute(
            select(Call).where(
                Call.id == call_id,
                Call.user_id == user_id,
                Call.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_user_including_deleted(
        self,
        call_id: UUID,
        *,
        user_id: UUID,
    ) -> Call | None:
        result = await self.session.execute(
            select(Call).where(Call.id == call_id, Call.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_user_including_deleted_for_update(
        self,
        call_id: UUID,
        *,
        user_id: UUID,
    ) -> Call | None:
        result = await self.session.execute(
            select(Call)
            .where(Call.id == call_id, Call.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def create_pending(
        self,
        *,
        user_id: UUID,
        phone_number_id: UUID | None = None,
        agent_config_id: UUID | None = None,
        livekit_room_id: str | None = None,
        caller_number: str | None = None,
    ) -> Call:
        call = Call(
            user_id=user_id,
            phone_number_id=phone_number_id,
            agent_config_id=agent_config_id,
            livekit_room_id=livekit_room_id,
            caller_number=caller_number,
            status="pending",
        )
        self.session.add(call)
        await self.session.flush()
        return call

    async def get_by_room(self, *, room_name: str) -> Call | None:
        result = await self.session.execute(
            select(Call).where(Call.livekit_room_id == room_name)
        )
        return result.scalar_one_or_none()

    async def get_pending_by_room_without_recording(self, *, room_name: str) -> Call | None:
        result = await self.session.execute(
            select(Call).where(
                Call.livekit_room_id == room_name,
                Call.status == "pending",
                Call.recording_egress_id.is_(None),
                Call.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def connect_if_pending(self, *, call_id: UUID) -> Call | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            update(Call)
            .where(
                Call.id == call_id,
                Call.status == "pending",
                Call.deleted_at.is_(None),
            )
            .values(
                status="connected",
                started_at=now,
                state_changed_at=now,
                failure_code=None,
            )
            .returning(Call)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_id_without_recording_for_update(
        self,
        *,
        call_id: UUID,
    ) -> Call | None:
        result = await self.session.execute(
            select(Call)
            .where(
                Call.id == call_id,
                Call.recording_egress_id.is_(None),
                Call.status == "connected",
                Call.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def set_livekit_dispatch_id(
        self,
        call: Call,
        *,
        livekit_dispatch_id: str,
    ) -> Call:
        call.livekit_dispatch_id = livekit_dispatch_id
        await self.session.flush()
        return call

    async def mark_dispatch_failed(self, call: Call, *, failure_code: str) -> Call:
        transitioned = await self.transition(
            call.id,
            from_states={"pending"},
            to_state="failed",
            failure_code=failure_code,
        )
        transitioned.ended_at = transitioned.ended_at or datetime.now(timezone.utc)
        await self.session.flush()
        return transitioned

    async def get_active_by_room_with_recording(self, *, room_name: str) -> Call | None:
        result = await self.session.execute(
            select(Call).where(
                Call.livekit_room_id == room_name,
                Call.recording_egress_id.is_not(None),
                Call.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_room_for_update(self, *, room_name: str) -> Call | None:
        result = await self.session.execute(
            select(Call)
            .where(
                Call.livekit_room_id == room_name,
                Call.status.in_(("pending", "connected", "ending", "finalizing")),
                Call.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def set_recording_metadata(
        self,
        call: Call,
        *,
        recording_object_key: str,
        recording_egress_id: str,
        recording_url: str | None,
    ) -> Call:
        call.recording_object_key = recording_object_key
        call.recording_egress_id = recording_egress_id
        call.recording_url = recording_url
        await self.session.flush()
        return call

    async def soft_delete(self, call: Call) -> Call:
        call.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return call

    async def purge_customer_content(self, call: Call) -> Call:
        call.caller_number = None
        call.summary_text = None
        call.summary_data = None
        call.summary_transcript_max_sequence = None
        call.recording_object_key = None
        call.recording_url = None
        call.recording_egress_id = None
        call.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return call
