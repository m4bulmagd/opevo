from datetime import datetime


class SubscriptionAccessPolicy:
    _ROUTING_STATUSES = frozenset({"active", "trialing"})
    _REPLACEABLE_STATUSES = frozenset({"canceled", "incomplete_expired"})

    @classmethod
    def can_route(cls, status: str, period_end: datetime | None) -> bool:
        del period_end
        return status in cls._ROUTING_STATUSES

    @staticmethod
    def should_grant_invoice(invoice_status: str) -> bool:
        return invoice_status == "paid"

    @classmethod
    def can_start_checkout(
        cls,
        *,
        account_status: str,
        subscription_status: str | None,
        has_incomplete_deactivation: bool,
        has_phone: bool,
    ) -> bool:
        subscription_is_replaceable = (
            subscription_status is None
            or cls.can_replace_subscription(subscription_status)
        )
        if account_status == "active":
            return subscription_is_replaceable
        if account_status != "inactive":
            return False
        return bool(
            subscription_is_replaceable
            and not has_incomplete_deactivation
            and not has_phone
        )

    @classmethod
    def can_replace_subscription(cls, status: str) -> bool:
        return status in cls._REPLACEABLE_STATUSES
