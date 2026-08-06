from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from functools import partial

from app.core.database import AsyncSessionFactory
from app.core.dispatch_token import DispatchTokenConfig
from app.core.observability import Observability
from app.providers.livekit_dispatch.base import LiveKitDispatchProvider
from app.providers.livekit_recording.base import RecordingProvider
from app.providers.storage.base import StorageProvider
from app.providers.subscriptions.base import SubscriptionProvider
from app.providers.summaries.base import SummaryProvider
from app.providers.telephony.base import TelephonyProvider
from app.models.outbox_event import OutboxEvent
from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch
from app.workers.outbox.phone import deliver_phone_provision, deliver_phone_routing
from app.workers.outbox.post_call import (
    deliver_recording_reconcile,
    deliver_summary_generate,
)
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch,
)


OutboxHandler = Callable[[OutboxEvent], Awaitable[None]]


def build_outbox_handlers(
    *,
    session_factory: AsyncSessionFactory,
    telephony_provider: TelephonyProvider,
    subscription_provider: SubscriptionProvider,
    livekit_dispatch_provider: LiveKitDispatchProvider,
    summary_provider: SummaryProvider,
    recording_provider: RecordingProvider,
    storage_provider: StorageProvider,
    observability: Observability,
    dispatch_token_config: DispatchTokenConfig,
    livekit_agent_name: str,
    activation_flow_enabled: bool,
    max_call_duration_seconds: int,
    now: Callable[[], datetime],
) -> Mapping[str, OutboxHandler]:
    return {
        "account.deactivate": partial(
            deliver_account_deactivation,
            session_factory=session_factory,
            telephony_provider=telephony_provider,
            subscription_provider=subscription_provider,
            observability=observability,
            now=now,
        ),
        "provider.cleanup": partial(
            deliver_provider_cleanup,
            session_factory=session_factory,
            telephony_provider=telephony_provider,
            subscription_provider=subscription_provider,
            now=now,
        ),
        "phone.provision": partial(
            deliver_phone_provision,
            session_factory=session_factory,
            telephony_provider=telephony_provider,
            activation_flow_enabled=activation_flow_enabled,
            now=now,
        ),
        "phone.enable": partial(
            deliver_phone_routing,
            session_factory=session_factory,
            telephony_provider=telephony_provider,
            activation_flow_enabled=activation_flow_enabled,
            now=now,
        ),
        "phone.disable": partial(
            deliver_phone_routing,
            session_factory=session_factory,
            telephony_provider=telephony_provider,
            activation_flow_enabled=activation_flow_enabled,
            now=now,
        ),
        "livekit.dispatch": partial(
            deliver_livekit_dispatch,
            session_factory=session_factory,
            provider=livekit_dispatch_provider,
            token_config=dispatch_token_config,
            livekit_agent_name=livekit_agent_name,
            activation_flow_enabled=activation_flow_enabled,
            max_call_duration_seconds=max_call_duration_seconds,
            now=now,
        ),
        "livekit.verification_dispatch": partial(
            deliver_livekit_verification_dispatch,
            session_factory=session_factory,
            provider=livekit_dispatch_provider,
            token_config=dispatch_token_config,
            livekit_agent_name=livekit_agent_name,
            now=now,
        ),
        "summary.generate": partial(
            deliver_summary_generate,
            session_factory=session_factory,
            summary_provider=summary_provider,
        ),
        "recording.reconcile": partial(
            deliver_recording_reconcile,
            session_factory=session_factory,
            recording_provider=recording_provider,
            storage_provider=storage_provider,
            observability=observability,
            now=now,
        ),
    }
