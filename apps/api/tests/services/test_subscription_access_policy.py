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
    ("invoice_status", "should_grant"),
    [
        ("paid", True),
        ("open", False),
        ("draft", False),
        ("void", False),
        ("uncollectible", False),
        ("unknown", False),
    ],
)
def test_should_grant_invoice_requires_paid_status(
    invoice_status: str,
    should_grant: bool,
) -> None:
    assert SubscriptionAccessPolicy.should_grant_invoice(invoice_status) is should_grant


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        (None, True),
        ("canceled", True),
        ("incomplete_expired", True),
        ("trialing", False),
        ("active", False),
        ("past_due", False),
        ("unpaid", False),
        ("incomplete", False),
        ("paused", False),
        ("unknown", False),
    ],
)
def test_can_start_checkout_only_without_subscription_or_after_terminal_status(
    status: str | None,
    allowed: bool,
) -> None:
    assert SubscriptionAccessPolicy.can_start_checkout(status) is allowed


@pytest.mark.parametrize(
    ("status", "allowed"),
    [
        ("canceled", True),
        ("incomplete_expired", True),
        ("active", False),
        ("trialing", False),
        ("unknown", False),
    ],
)
def test_can_replace_subscription_is_the_terminal_status_contract(
    status: str,
    allowed: bool,
) -> None:
    assert SubscriptionAccessPolicy.can_replace_subscription(status) is allowed
