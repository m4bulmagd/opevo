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
    (
        "account_status",
        "subscription_status",
        "has_incomplete_deactivation",
        "has_phone",
        "allowed",
    ),
    [
        ("active", None, False, False, True),
        ("active", "canceled", False, False, True),
        ("active", "incomplete_expired", False, False, True),
        ("active", "active", False, False, False),
        ("deactivating", "canceled", False, False, False),
        ("deactivating", "canceled", True, True, False),
        ("inactive", "canceled", False, False, True),
        ("inactive", "incomplete_expired", False, False, True),
        ("inactive", None, False, False, True),
        ("inactive", "active", False, False, False),
        ("inactive", "canceled", True, False, False),
        ("inactive", "canceled", False, True, False),
        ("unknown", "canceled", False, False, False),
    ],
)
def test_checkout_requires_safe_account_and_subscription_state(
    account_status: str,
    subscription_status: str | None,
    has_incomplete_deactivation: bool,
    has_phone: bool,
    allowed: bool,
) -> None:
    assert (
        SubscriptionAccessPolicy.can_start_checkout(
            account_status=account_status,
            subscription_status=subscription_status,
            has_incomplete_deactivation=has_incomplete_deactivation,
            has_phone=has_phone,
        )
        is allowed
    )


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
