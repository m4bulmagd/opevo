from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.business_profile import (
    BusinessProfileConstraints,
    BusinessProfileResponse,
)
from app.services.activation_policy import ActivationStage
from app.services.customer_readiness_policy import CustomerReadinessStage


class ActivationProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_confirmed_at: datetime | None
    provisioning_consented_at: datetime | None
    forwarding_verified_at: datetime | None
    go_live_approved_at: datetime | None
    activated_at: datetime | None
    last_failure_code: str | None


class ActivationBillingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: bool
    plan_tier: str | None
    subscription_status: str | None
    allocated_minutes: int
    minutes_remaining: int
    current_period_start: datetime | None
    current_period_end: datetime | None


class ActivationNumberResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_e164: str | None
    country_code: str | None
    provider_ready: bool
    provisioning_status: str | None
    can_retry: bool


class RuntimeReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: CustomerReadinessStage
    can_provision_number: bool
    can_activate: bool
    should_enable_phone: bool
    can_route: bool
    blockers: list[str]
    warnings: list[str]
    policy_version: str


class ActivationSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_version: int
    stage: ActivationStage
    completed_milestones: list[str]
    next_action: str | None
    blockers: list[str]
    warnings: list[str]
    profile: BusinessProfileResponse
    profile_constraints: BusinessProfileConstraints
    activation: ActivationProgressResponse
    billing: ActivationBillingResponse
    number: ActivationNumberResponse
    runtime_readiness: RuntimeReadinessResponse
    evaluated_at: datetime
