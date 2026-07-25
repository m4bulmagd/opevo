from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.billing_service import BillingService


@pytest.mark.anyio
async def test_invoice_paid_locks_user_before_subscription() -> None:
    events: list[str] = []
    user_id = uuid4()
    user = SimpleNamespace(id=user_id, lifecycle_generation=1, status="active")
    subscription = SimpleNamespace(
        user_id=user_id,
        plan_tier="starter",
        allocated_minutes=60,
        status="active",
        lifecycle_generation=1,
    )

    class _Usage:
        async def acquire_invoice_grant_lock(self, *, invoice_id: str) -> None:
            events.append("invoice_lock")

        async def grant_invoice(self, **_kwargs):
            events.append("grant")
            return SimpleNamespace(already_granted=True, first_activation=False)

    class _Users:
        async def get_by_clerk_user_id(self, _clerk_user_id: str):
            events.append("user_lookup")
            return user

        async def get_by_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("user_lock")
            return user

    class _Subscriptions:
        async def get_by_user_id_for_update(self, requested_user_id):
            assert requested_user_id == user_id
            events.append("subscription_lock")
            return subscription

        async def resolve_invoice_target_for_update(self, **_kwargs):
            events.append("invoice_target")
            return subscription, True

        def advance_known_event_watermark(self, *_args) -> None:
            return None

    service = BillingService.__new__(BillingService)
    service.usage_accounting_service = _Usage()
    service.user_repository = _Users()
    service.subscription_repository = _Subscriptions()
    service.phone_number_repository = SimpleNamespace()
    service.outbox_service = SimpleNamespace()

    await service._handle_invoice_paid(
        {
            "id": "in_lock_order",
            "status": "paid",
            "paid": True,
            "customer": "cus_lock_order",
            "parent": {
                "subscription_details": {
                    "subscription": "sub_lock_order",
                    "metadata": {"clerk_user_id": "user_lock_order"},
                }
            },
            "lines": {
                "data": [{"price": {"lookup_key": "starter"}}]
            },
        },
        "evt_lock_order",
        "invoice.paid",
        datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert events == [
        "invoice_lock",
        "user_lookup",
        "user_lock",
        "subscription_lock",
        "invoice_target",
        "grant",
    ]
