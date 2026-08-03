from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any, cast
from uuid import UUID

from sqlalchemy import JSON, Text, and_, case, cast as sql_cast, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY
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
        "dispatch_internal_defect",
        "dispatch_provider_exhausted",
        "dispatch_timeout",
        "caller_left_before_connect",
        "finalization_exhausted",
        "legacy_failure",
    }
)

PHONE_QUERY_PATTERN = re.compile(r"^[0-9\s()+.\-]+$")

SUMMARY_WHITESPACE_CHARACTERS = (
    " \t\n\r\v\f"
    "\x1c\x1d\x1e\x1f\x85"
    "\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


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


@dataclass(frozen=True)
class DashboardMetricsAggregate:
    calls_today: int
    calls_last_7_days: int
    calls_previous_7_days: int
    follow_up_flagged_last_7_days: int
    average_duration_seconds_last_7_days: int | None
    daily_call_counts: tuple[int, ...]


class CallTransitionError(ValueError):
    pass


class CallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, call_id: UUID) -> Call | None:
        return await self.session.get(Call, call_id)

    @staticmethod
    def _sqlite_valid_follow_up_expression() -> ColumnElement[bool]:
        action_items = (
            func.json_each(Call.summary_data, "$.action_items")
            .table_valued("key", "value", "type")
            .alias("dashboard_action_item")
        )
        invalid_action_item = exists(
            select(1)
            .select_from(action_items)
            .where(
                or_(
                    action_items.c.type != "text",
                    func.length(action_items.c.value) == 0,
                    func.length(action_items.c.value) > 300,
                    func.length(
                        func.trim(
                            action_items.c.value,
                            SUMMARY_WHITESPACE_CHARACTERS,
                        )
                    )
                    == 0,
                )
            )
            .correlate(Call)
        )
        return and_(
            func.json_type(Call.summary_data, "$.caller_intent") == "text",
            func.length(
                func.json_extract(Call.summary_data, "$.caller_intent")
            ).between(1, 200),
            func.length(
                func.trim(
                    func.json_extract(Call.summary_data, "$.caller_intent"),
                    SUMMARY_WHITESPACE_CHARACTERS,
                )
            )
            > 0,
            func.json_type(Call.summary_data, "$.action_items") == "array",
            func.json_array_length(
                func.json_extract(Call.summary_data, "$.action_items")
            )
            <= 10,
            ~invalid_action_item,
            func.json_type(Call.summary_data, "$.sentiment") == "text",
            func.length(
                func.json_extract(Call.summary_data, "$.sentiment")
            ).between(1, 32),
            func.length(
                func.trim(
                    func.json_extract(Call.summary_data, "$.sentiment"),
                    SUMMARY_WHITESPACE_CHARACTERS,
                )
            )
            > 0,
            func.json_type(Call.summary_data, "$.follow_up_required") == "true",
        )

    @staticmethod
    def _postgresql_valid_follow_up_expression() -> ColumnElement[bool]:
        caller_intent_json = Call.summary_data.op("->")("caller_intent")
        action_items_json = Call.summary_data.op("->")("action_items")
        sentiment_json = Call.summary_data.op("->")("sentiment")
        follow_up_json = Call.summary_data.op("->")("follow_up_required")
        safe_action_items = case(
            (
                func.json_typeof(action_items_json) == "array",
                action_items_json,
            ),
            else_=sql_cast(literal("[]"), JSON),
        )
        action_items = (
            func.json_array_elements(safe_action_items)
            .table_valued("value")
            .alias("dashboard_action_item")
        )
        action_item_text = action_items.c.value.op("#>>")(
            sql_cast(literal("{}"), ARRAY(Text))
        )
        invalid_action_item = exists(
            select(1)
            .select_from(action_items)
            .where(
                or_(
                    func.json_typeof(action_items.c.value) != "string",
                    func.length(action_item_text) == 0,
                    func.length(action_item_text) > 300,
                    func.length(
                        func.btrim(
                            action_item_text,
                            SUMMARY_WHITESPACE_CHARACTERS,
                        )
                    )
                    == 0,
                )
            )
            .correlate(Call)
        )
        return and_(
            func.json_typeof(caller_intent_json) == "string",
            func.length(
                Call.summary_data.op("->>")("caller_intent")
            ).between(1, 200),
            func.length(
                func.btrim(
                    Call.summary_data.op("->>")("caller_intent"),
                    SUMMARY_WHITESPACE_CHARACTERS,
                )
            )
            > 0,
            func.json_typeof(action_items_json) == "array",
            case(
                (
                    func.json_typeof(action_items_json) == "array",
                    func.json_array_length(action_items_json),
                ),
                else_=None,
            )
            <= 10,
            ~invalid_action_item,
            func.json_typeof(sentiment_json) == "string",
            func.length(
                Call.summary_data.op("->>")("sentiment")
            ).between(1, 32),
            func.length(
                func.btrim(
                    Call.summary_data.op("->>")("sentiment"),
                    SUMMARY_WHITESPACE_CHARACTERS,
                )
            )
            > 0,
            func.json_typeof(follow_up_json) == "boolean",
            Call.summary_data.op("->>")("follow_up_required") == "true",
        )

    def _valid_follow_up_expression(self) -> ColumnElement[bool]:
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "sqlite":
            return self._sqlite_valid_follow_up_expression()
        if dialect_name == "postgresql":
            return self._postgresql_valid_follow_up_expression()
        raise RuntimeError(
            f"Dashboard metrics do not support the {dialect_name!r} SQL dialect"
        )

    async def dashboard_metrics(
        self,
        user_id: UUID,
        *,
        today_start_utc: datetime,
        current_window_start_utc: datetime,
        previous_window_start_utc: datetime,
        now_utc: datetime,
        activity_windows_utc: tuple[tuple[datetime, datetime], ...],
    ) -> DashboardMetricsAggregate:
        current_window = and_(
            Call.started_at >= current_window_start_utc,
            Call.started_at <= now_utc,
        )
        previous_window = and_(
            Call.started_at >= previous_window_start_utc,
            Call.started_at < current_window_start_utc,
        )
        today_window = and_(
            Call.started_at >= today_start_utc,
            Call.started_at <= now_utc,
        )
        average_duration = and_(
            current_window,
            Call.status.in_(("completed", "failed")),
            Call.duration_seconds.is_not(None),
        )
        activity_expressions = [
            func.sum(
                case(
                    (
                        and_(
                            Call.started_at >= window_start,
                            Call.started_at < window_end,
                            Call.started_at <= now_utc,
                        ),
                        1,
                    ),
                    else_=0,
                )
            )
            for window_start, window_end in activity_windows_utc
        ]
        row = (
            await self.session.execute(
                select(
                    func.sum(case((today_window, 1), else_=0)),
                    func.sum(case((current_window, 1), else_=0)),
                    func.sum(case((previous_window, 1), else_=0)),
                    func.sum(
                        case(
                            (
                                and_(
                                    current_window,
                                    self._valid_follow_up_expression(),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.avg(
                        case(
                            (average_duration, Call.duration_seconds),
                            else_=None,
                        )
                    ),
                    *activity_expressions,
                ).where(
                    Call.user_id == user_id,
                    Call.deleted_at.is_(None),
                )
            )
        ).one()
        average_value = row[4]
        rounded_average = (
            None
            if average_value is None
            else int(
                Decimal(str(average_value)).quantize(
                    Decimal("1"),
                    rounding=ROUND_HALF_UP,
                )
            )
        )
        return DashboardMetricsAggregate(
            calls_today=int(row[0] or 0),
            calls_last_7_days=int(row[1] or 0),
            calls_previous_7_days=int(row[2] or 0),
            follow_up_flagged_last_7_days=int(row[3] or 0),
            average_duration_seconds_last_7_days=rounded_average,
            daily_call_counts=tuple(int(value or 0) for value in row[5:]),
        )

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
        *,
        statuses: frozenset[str] | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> tuple[ColumnElement[bool], ...]:
        predicates: list[ColumnElement[bool]] = [
            Call.user_id == user_id,
            Call.deleted_at.is_(None),
        ]
        if statuses is not None:
            predicates.append(Call.status.in_(statuses))
        if started_after is not None:
            predicates.append(Call.started_at >= started_after)
        if started_before is not None:
            predicates.append(Call.started_at <= started_before)
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
        statuses: frozenset[str] | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> CallHistoryPage:
        predicates = self._visible_call_predicates(
            user_id,
            query,
            statuses=statuses,
            started_after=started_after,
            started_before=started_before,
        )
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
