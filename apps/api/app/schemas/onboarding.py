from typing import Literal

from pydantic import BaseModel


class OnboardingStatusResponse(BaseModel):
    subscription_status: str | None
    plan_tier: str | None
    minutes_remaining: int
    phone_number: str | None
    phone_number_status: Literal["missing", "provisioning", "ready", "failed"]
    routing_enabled: bool
    agent_setup_complete: bool
    overall_status: Literal[
        "not_subscribed",
        "subscription_active",
        "provisioning_number",
        "setup_required",
        "ready_to_enable",
        "live",
        "provisioning_failed",
    ]
    can_retry_provisioning: bool


class RetryProvisioningResponse(BaseModel):
    status: Literal["accepted"]
    queued: bool
