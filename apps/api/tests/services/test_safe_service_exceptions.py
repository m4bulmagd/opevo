"""Provider failures at post-call boundaries expose only safe error codes."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.agent_config import AgentConfig
from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.outbox_event import OutboxEvent
from app.models.phone_number import PhoneNumber
from app.models.subscription import Subscription
from app.models.usage_ledger import UsageLedger
from app.core.provider_failures import ProviderFailure
from app.workers.outbox.failures import (
    OutboxDeliveryError,
    _outbox_error_class,
)
from app.workers.outbox.phone import deliver_phone_provision, deliver_phone_routing
from app.workers.outbox.post_call import deliver_summary_generate


@pytest.fixture(autouse=True)
def _legacy_routing_flow(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ACTIVATION_FLOW_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _event(call_id, *, topic: str, aggregate_type: str) -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(),
        idempotency_key=f"{topic}:{call_id}",
        topic=topic,
        aggregate_type=aggregate_type,
        aggregate_id=call_id,
        payload={"call_id": str(call_id)},
        status="processing",
        attempt_count=1,
        next_attempt_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("error_code", "error_class"),
    [
        ("recording_unresolved", "unknown"),
        ("recording_provider_unavailable", "unavailable"),
        ("recording_storage_unavailable", "unavailable"),
        ("recording_identity_mismatch", "validation"),
        ("recording_identity_conflict", "conflict"),
        ("recording_legacy_incomplete", "validation"),
    ],
)
def test_recording_reconciliation_errors_are_bounded_and_safely_classified(
    error_code: str,
    error_class: str,
) -> None:
    error = OutboxDeliveryError(
        error_code,
        retryable=True,
        exhaustible=False,
    )

    assert str(error) == error_code
    assert _outbox_error_class(error_code) == error_class


@pytest.mark.anyio
async def test_typed_summary_provider_failure_is_translated_to_safe_retry(
    db_session,
    active_user,
) -> None:
    call = Call(user_id=active_user.id, status="completed")
    db_session.add(call)
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=call.id,
            sequence_number=1,
            speaker="CALLER",
            text="Durable transcript",
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class SecretBearingProvider:
        async def generate_summary(self, _transcript):
            raise ProviderFailure(
                provider="gemini",
                operation="generate_summary",
                disposition="retryable",
                error_class="unavailable",
            )

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_summary_generate(
            {
                "session_factory": factory,
                "summary_provider": SecretBearingProvider(),
            },
            _event(
                call.id,
                topic="summary.generate",
                aggregate_type="call-summary",
            ),
        )

    assert exc_info.value.error_code == "provider_retryable"
    assert exc_info.value.retryable is True
    assert "provider operation failed" not in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("provider_retryable", True),
        ("provider_terminal", False),
    ],
)
async def test_phone_routing_preserves_safe_provider_category(
    db_session,
    active_user,
    category: str,
    retryable: bool,
) -> None:
    now = datetime.now(UTC)
    phone = PhoneNumber(
        user_id=active_user.id,
        e164="+33123456789",
        country_code="FR",
        provider="telnyx",
        provider_number_id="pn-safe-category",
        provider_connection_name="app-disabled",
        is_active=False,
    )
    db_session.add_all(
        [
            phone,
            AgentConfig(
                user_id=active_user.id,
                agent_name="Ava",
                owner_context="Sam at Bakery",
                system_prompt="Be helpful",
                knowledge_base="Hours 9-5",
                pipeline_mode="stt_llm_tts",
                is_enabled=True,
            ),
            Subscription(
                user_id=active_user.id,
                stripe_customer_id="cus-safe-category",
                stripe_subscription_id="sub-safe-category",
                plan_tier="starter",
                status="active",
                allocated_minutes=60,
                current_period_start=now,
                current_period_end=now.replace(year=now.year + 1),
            ),
            UsageLedger(
                user_id=active_user.id,
                event_type="invoice_paid_reset",
                source_id="invoice-safe-category",
                minutes_delta=10,
                balance_after=10,
            ),
        ]
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    class CategorizedProvider:
        async def enable_number(self, *, provider_number_id: str) -> str:
            raise ProviderFailure(
                provider="telnyx",
                operation="enable_number",
                disposition=("retryable" if category == "provider_retryable" else "terminal"),
                error_class="unavailable",
            )

        async def disable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError

    event = _event(active_user.id, topic="phone.enable", aggregate_type="user")
    event.payload = {
        "user_id": str(active_user.id),
        "lifecycle_generation": 1,
    }
    db_session.add(event)
    await db_session.commit()

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_phone_routing(
            {
                "session_factory": factory,
                "telephony_provider": CategorizedProvider(),
            },
            event,
        )

    assert exc_info.value.error_code == category
    assert exc_info.value.retryable is retryable


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("provider_retryable", True),
        ("provider_terminal", False),
    ],
)
async def test_phone_provisioning_outbox_preserves_safe_provider_category(
    db_session,
    active_user,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    retryable: bool,
) -> None:
    from app.workers.outbox import phone as phone_outbox

    async def fail_provisioning(*_args, **_kwargs) -> None:
        raise ProviderFailure(
            provider="telnyx",
            operation="provision_number",
            disposition=("retryable" if category == "provider_retryable" else "terminal"),
            error_class="unavailable",
        )

    monkeypatch.setattr(
        phone_outbox,
        "provision_phone_number",
        fail_provisioning,
    )
    from app.models.phone_number_provisioning import PhoneNumberProvisioning

    user_id = active_user.id
    event = _event(user_id, topic="phone.provision", aggregate_type="user")
    event.payload = {
        "user_id": str(user_id),
        "lifecycle_generation": 1,
    }
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status="queued",
            attempt_count=0,
            can_retry=False,
            provider_operation_key=event.idempotency_key,
        )
    )
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_phone_provision({"session_factory": factory}, event)

    assert exc_info.value.error_code == category
    assert exc_info.value.retryable is retryable


@pytest.mark.anyio
async def test_malformed_provisioning_result_is_safe_terminal_outbox_failure(
    db_session,
    active_user,
) -> None:
    from sqlalchemy import select

    from app.models.phone_number_provisioning import PhoneNumberProvisioning

    malformed_number = "+442079460958"

    class MalformedProvisioningProvider:
        async def provision_number(
            self,
            *,
            country_code: str,
            operation_key: str | None = None,
        ) -> dict:
            return {
                "e164": malformed_number,
                "provider_number_id": "provider-secret-number-id",
                "provider_connection_name": "app-disabled",
            }

        async def enable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError("enable_number should not be called")

        async def disable_number(self, *, provider_number_id: str) -> str:
            raise AssertionError("disable_number should not be called")

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    event = _event(
        active_user.id,
        topic="phone.provision",
        aggregate_type="user",
    )
    event.payload = {
        "user_id": str(active_user.id),
        "lifecycle_generation": 1,
    }
    db_session.add(
        PhoneNumberProvisioning(
            user_id=active_user.id,
            target_country_code="FR",
            status="queued",
            attempt_count=0,
            can_retry=False,
            provider_operation_key=event.idempotency_key,
        )
    )
    await db_session.commit()

    with pytest.raises(OutboxDeliveryError) as exc_info:
        await deliver_phone_provision(
            {
                "session_factory": factory,
                "telephony_provider": MalformedProvisioningProvider(),
            },
            event,
        )

    assert exc_info.value.error_code == "provider_terminal"
    assert exc_info.value.retryable is False
    assert malformed_number not in str(exc_info.value)
    provisioning = await db_session.scalar(
        select(PhoneNumberProvisioning).where(
            PhoneNumberProvisioning.user_id == active_user.id
        )
    )
    assert provisioning is not None
    assert provisioning.last_error_reason == "provider_terminal"
    assert provisioning.can_retry is False
    assert provisioning.last_error_payload == {"error_type": "validation"}
