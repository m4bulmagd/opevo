from datetime import datetime


class SubscriptionAccessPolicy:
    _ROUTING_STATUSES = frozenset({"active", "trialing"})
    _CHECKOUT_STATUSES = frozenset({None, "canceled", "incomplete_expired"})

    @classmethod
    def can_route(cls, status: str, period_end: datetime | None) -> bool:
        del period_end
        return status in cls._ROUTING_STATUSES

    @staticmethod
    def should_grant_invoice(invoice_status: str, paid: bool) -> bool:
        return paid is True and invoice_status == "paid"

    @classmethod
    def can_start_checkout(cls, status: str | None) -> bool:
        return status in cls._CHECKOUT_STATUSES
