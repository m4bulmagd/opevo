from datetime import UTC, datetime, timedelta

import pytest

from app.services.subscription_access_policy import SubscriptionAccessPolicy


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        ("active", True),
        ("trialing", True),
        ("past_due", False),
        ("unpaid", False),
        ("canceled", False),
        ("incomplete", False),
        ("incomplete_expired", False),
        ("paused", False),
        ("unknown", False),
        ("ACTIVE", False),
        ("", False),
    ],
)
def test_can_route_matrix(status: str, allowed: bool) -> None:
    assert SubscriptionAccessPolicy.can_route(status, None) is allowed


def test_can_route_is_driven_by_provider_status_not_period_end() -> None:
    expired_period = datetime.now(UTC) - timedelta(days=1)

    assert SubscriptionAccessPolicy.can_route("active", expired_period) is True


@pytest.mark.parametrize(
    ("invoice_status", "paid", "should_grant"),
    [
        ("paid", True, True),
        ("paid", False, False),
        ("open", True, False),
        ("draft", True, False),
        ("void", True, False),
        ("uncollectible", True, False),
        ("unknown", True, False),
    ],
)
def test_should_grant_invoice_requires_paid_status_and_paid_flag(
    invoice_status: str,
    paid: bool,
    should_grant: bool,
) -> None:
    assert SubscriptionAccessPolicy.should_grant_invoice(invoice_status, paid) is should_grant
