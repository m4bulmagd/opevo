from datetime import datetime

from pydantic import BaseModel


class SubscriptionResponse(BaseModel):
    plan_tier: str
    status: str
    allocated_minutes: int
    current_period_start: datetime | None
    current_period_end: datetime | None
    stripe_customer_id: str | None
    stripe_subscription_id: str | None


class UsageSnapshotResponse(BaseModel):
    minutes_remaining: int
    allocated_minutes: int
    plan_tier: str | None
    subscription_status: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None


class UsageLedgerEntryResponse(BaseModel):
    id: str
    event_type: str
    minutes_delta: int
    balance_after: int | None
    call_id: str | None
    created_at: datetime


class UsageLedgerListResponse(BaseModel):
    entries: list[UsageLedgerEntryResponse]


class CheckoutSessionRequest(BaseModel):
    plan_tier: str


class HostedSessionResponse(BaseModel):
    url: str


class PortalSessionRequest(BaseModel):
    return_url: str
