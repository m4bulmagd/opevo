from datetime import UTC, datetime


class DispatchEligibilityPolicy:
    ELIGIBLE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})

    @classmethod
    def can_dispatch(
        cls,
        *,
        subscription_status: str,
        current_period_start: datetime | None,
        current_period_end: datetime | None,
        balance: int,
        phone_active: bool,
        agent_enabled: bool,
        setup_complete: bool,
        called_number_matches: bool,
        now: datetime | None = None,
    ) -> bool:
        if (
            subscription_status not in cls.ELIGIBLE_SUBSCRIPTION_STATUSES
            or current_period_start is None
            or current_period_end is None
        ):
            return False

        effective_now = cls._as_utc(now or datetime.now(UTC))
        period_start = cls._as_utc(current_period_start)
        period_end = cls._as_utc(current_period_end)
        return bool(
            period_start <= effective_now < period_end
            and balance > 0
            and phone_active
            and agent_enabled
            and setup_complete
            and called_number_matches
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
