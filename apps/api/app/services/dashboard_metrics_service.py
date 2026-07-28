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
WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class DashboardActivityPoint:
    date: str
    label: str
    calls: int


@dataclass(frozen=True)
class DashboardMetrics:
    timezone: str
    calls_today: int
    calls_last_7_days: int
    calls_previous_7_days: int
    calls_change_from_previous_7_days: int
    follow_up_flagged_last_7_days: int
    average_duration_seconds_last_7_days: int | None
    daily_activity: tuple[DashboardActivityPoint, ...]


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
        activity_dates = tuple(
            today - timedelta(days=day_offset)
            for day_offset in range(6, -1, -1)
        )
        activity_boundaries = tuple(
            datetime.combine(activity_date, time.min, tzinfo=zone).astimezone(
                UTC
            )
            for activity_date in (
                *activity_dates,
                today + timedelta(days=1),
            )
        )
        activity_windows = tuple(
            zip(
                activity_boundaries[:-1],
                activity_boundaries[1:],
                strict=True,
            )
        )

        aggregate = await self.call_repository.dashboard_metrics(
            user_id,
            today_start_utc=today_start,
            current_window_start_utc=current_start,
            previous_window_start_utc=previous_start,
            now_utc=now_utc,
            activity_windows_utc=activity_windows,
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
            daily_activity=tuple(
                DashboardActivityPoint(
                    date=activity_date.isoformat(),
                    label=WEEKDAY_LABELS[activity_date.weekday()],
                    calls=call_count,
                )
                for activity_date, call_count in zip(
                    activity_dates,
                    aggregate.daily_call_counts,
                    strict=True,
                )
            ),
        )
