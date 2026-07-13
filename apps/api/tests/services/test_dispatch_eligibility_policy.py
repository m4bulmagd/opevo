from datetime import UTC, datetime, timedelta

import pytest

from app.services.dispatch_eligibility_policy import DispatchEligibilityPolicy


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    (
        "subscription_status",
        "period_start",
        "period_end",
        "balance",
        "phone_active",
        "agent_enabled",
        "setup_complete",
        "called_number_matches",
        "expected",
    ),
    [
        ("active", NOW, NOW + timedelta(seconds=1), 1, True, True, True, True, True),
        ("trialing", NOW - timedelta(days=1), NOW + timedelta(days=1), 1, True, True, True, True, True),
        ("past_due", NOW - timedelta(days=1), NOW + timedelta(days=1), 1, True, True, True, True, False),
        ("active", None, NOW + timedelta(days=1), 1, True, True, True, True, False),
        ("active", NOW - timedelta(days=1), None, 1, True, True, True, True, False),
        ("active", NOW + timedelta(seconds=1), NOW + timedelta(days=1), 1, True, True, True, True, False),
        ("active", NOW - timedelta(days=1), NOW, 1, True, True, True, True, False),
        ("active", NOW - timedelta(days=1), NOW + timedelta(days=1), 0, True, True, True, True, False),
        ("active", NOW - timedelta(days=1), NOW + timedelta(days=1), 1, False, True, True, True, False),
        ("active", NOW - timedelta(days=1), NOW + timedelta(days=1), 1, True, False, True, True, False),
        ("active", NOW - timedelta(days=1), NOW + timedelta(days=1), 1, True, True, False, True, False),
        ("active", NOW - timedelta(days=1), NOW + timedelta(days=1), 1, True, True, True, False, False),
    ],
)
def test_dispatch_requires_every_eligibility_gate(
    subscription_status: str,
    period_start: datetime | None,
    period_end: datetime | None,
    balance: int,
    phone_active: bool,
    agent_enabled: bool,
    setup_complete: bool,
    called_number_matches: bool,
    expected: bool,
) -> None:
    assert (
        DispatchEligibilityPolicy.can_dispatch(
            subscription_status=subscription_status,
            current_period_start=period_start,
            current_period_end=period_end,
            balance=balance,
            phone_active=phone_active,
            agent_enabled=agent_enabled,
            setup_complete=setup_complete,
            called_number_matches=called_number_matches,
            now=NOW,
        )
        is expected
    )


def test_dispatch_rejects_an_inverted_subscription_period() -> None:
    assert not DispatchEligibilityPolicy.can_dispatch(
        subscription_status="active",
        current_period_start=NOW + timedelta(hours=1),
        current_period_end=NOW - timedelta(hours=1),
        balance=1,
        phone_active=True,
        agent_enabled=True,
        setup_complete=True,
        called_number_matches=True,
        now=NOW,
    )
