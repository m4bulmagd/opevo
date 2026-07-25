from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.business_profile_repository import BusinessProfileRepository
from app.repositories.call_repository import CallRepository
from app.repositories.customer_activation_repository import (
    CustomerActivationRepository,
)


DEFAULT_DASHBOARD_TIMEZONE = "Europe/Paris"


@dataclass(frozen=True)
class DashboardMetrics:
    timezone: str
    calls_today: int
    calls_last_7_days: int
    calls_previous_7_days: int
    calls_change_from_previous_7_days: int
    follow_up_flagged_last_7_days: int
    average_duration_seconds_last_7_days: int | None


class DashboardMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.call_repository = CallRepository(session)
        self.profile_repository = BusinessProfileRepository(session)
        self.activation_repository = CustomerActivationRepository(session)

    async def get_metrics(
        self,
        user_id: UUID,
        *,
        now: datetime | None = None,
    ) -> DashboardMetrics:
        profile = await self.profile_repository.get_by_user_id(user_id)
        activation = await self.activation_repository.get_by_user_id(user_id)
        timezone_name = DEFAULT_DASHBOARD_TIMEZONE
        if (
            profile is not None
            and profile.timezone
            and activation is not None
            and activation.profile_confirmed_at is not None
            and activation.profile_confirmed_revision == profile.content_revision
        ):
            timezone_name = profile.timezone

        current_time = now or datetime.now(UTC)
        now_utc = (
            current_time.replace(tzinfo=UTC)
            if current_time.tzinfo is None
            else current_time.astimezone(UTC)
        )
        zone = ZoneInfo(timezone_name)
        local_now = now_utc.astimezone(zone)
        today = local_now.date()
        today_start = datetime.combine(today, time.min, tzinfo=zone).astimezone(UTC)
        current_start = datetime.combine(
            today - timedelta(days=6),
            time.min,
            tzinfo=zone,
        ).astimezone(UTC)
        previous_start = datetime.combine(
            today - timedelta(days=13),
            time.min,
            tzinfo=zone,
        ).astimezone(UTC)

        aggregate = await self.call_repository.dashboard_metrics(
            user_id,
            today_start_utc=today_start,
            current_window_start_utc=current_start,
            previous_window_start_utc=previous_start,
            now_utc=now_utc,
        )
        return DashboardMetrics(
            timezone=timezone_name,
            calls_today=aggregate.calls_today,
            calls_last_7_days=aggregate.calls_last_7_days,
            calls_previous_7_days=aggregate.calls_previous_7_days,
            calls_change_from_previous_7_days=(
                aggregate.calls_last_7_days - aggregate.calls_previous_7_days
            ),
            follow_up_flagged_last_7_days=(
                aggregate.follow_up_flagged_last_7_days
            ),
            average_duration_seconds_last_7_days=(
                aggregate.average_duration_seconds_last_7_days
            ),
        )
