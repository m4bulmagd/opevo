from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class OnboardingStatusResponse(BaseModel):
    subscription_status: str | None
    plan_tier: str | None
    minutes_remaining: int
    phone_number: str | None
    phone_number_status: Literal["missing", "provisioning", "ready", "failed"]
    agent_setup_complete: bool
    can_retry_provisioning: bool
    stage: Literal[
        "subscription_required",
        "number_provisioning",
        "number_provisioning_failed",
        "receptionist_setup_required",
        "ready",
        "routing_pending",
        "live",
        "suspended",
    ]
    can_activate: bool
    can_route: bool
    blockers: list[str]
    warnings: list[str]
    evaluated_at: datetime
    policy_version: str


class RetryProvisioningResponse(BaseModel):
    status: Literal["accepted"]
    queued: bool
