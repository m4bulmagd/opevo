from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AccountDeactivateRequest(BaseModel):
    confirmation: Literal["DEACTIVATE"]


class DeactivationProgressResponse(BaseModel):
    state: Literal[
        "requested",
        "disabling_routing",
        "canceling_subscription",
        "draining_call",
        "releasing_number",
        "finalizing",
        "attention_required",
    ]
    requested_at: datetime


class AccountStatusResponse(BaseModel):
    status: Literal["active", "deactivating", "inactive"]
    serving: bool
    deactivation: DeactivationProgressResponse | None
    reactivation_allowed: bool
    blocker: Literal[
        "account_deactivating",
        "account_inactive",
        "deactivation_attention_required",
        "reactivation_not_ready",
        "customer_not_ready",
    ] | None
