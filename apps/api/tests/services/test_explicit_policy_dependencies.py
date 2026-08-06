import inspect

import pytest

from app.services.account_lifecycle_service import AccountLifecycleService
from app.services.agent_config_service import AgentConfigService
from app.services.call_reconciliation_service import CallReconciliationService
from app.services.carrier_lookup_service import CarrierLookupService
from app.services.customer_readiness_service import CustomerReadinessService
from app.services.livekit_dispatch_service import LiveKitDispatchService
from app.services.onboarding_service import OnboardingService
from app.services.telephony_service import TelephonyService


@pytest.mark.parametrize(
    ("constructor", "dependency"),
    [
        (AgentConfigService, "activation_flow_enabled"),
        (CustomerReadinessService, "activation_flow_enabled"),
        (CallReconciliationService, "settings"),
        (CarrierLookupService, "provider"),
        (TelephonyService, "provider"),
        (LiveKitDispatchService, "activation_flow_enabled"),
        (OnboardingService, "activation_flow_enabled"),
        (AccountLifecycleService, "activation_flow_enabled"),
    ],
)
def test_domain_policy_dependency_is_required(constructor, dependency: str) -> None:
    parameter = inspect.signature(constructor).parameters[dependency]

    assert parameter.default is inspect.Parameter.empty
