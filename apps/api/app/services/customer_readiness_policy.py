from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.schemas.agent_content import (
    AGENT_NAME_MAX_LENGTH,
    KNOWLEDGE_BASE_MAX_LENGTH,
    OWNER_CONTEXT_MAX_LENGTH,
    SYSTEM_PROMPT_MAX_LENGTH,
)


class ReadinessBlocker(StrEnum):
    USER_INACTIVE = "user_inactive"
    SUBSCRIPTION_MISSING = "subscription_missing"
    PLAN_UNSUPPORTED = "plan_unsupported"
    SUBSCRIPTION_STATUS_INELIGIBLE = "subscription_status_ineligible"
    SUBSCRIPTION_PERIOD_MISSING = "subscription_period_missing"
    SUBSCRIPTION_PERIOD_INACTIVE = "subscription_period_inactive"
    MINUTES_EXHAUSTED = "minutes_exhausted"
    PHONE_MISSING = "phone_missing"
    PHONE_PROVIDER_ID_MISSING = "phone_provider_id_missing"
    AGENT_CONFIG_MISSING = "agent_config_missing"
    AGENT_SETUP_INCOMPLETE = "agent_setup_incomplete"
    AGENT_CONTENT_INVALID = "agent_content_invalid"
    AGENT_DISABLED = "agent_disabled"
    PHONE_INACTIVE = "phone_inactive"
    PHONE_PROJECTION_INACTIVE = "phone_projection_inactive"
    BUSINESS_PROFILE_INCOMPLETE = "business_profile_incomplete"
    PROFILE_PROJECTION_STALE = "profile_projection_stale"
    FORWARDING_NOT_VERIFIED = "forwarding_not_verified"
    GO_LIVE_NOT_APPROVED = "go_live_not_approved"
    GO_LIVE_NOT_ACTIVATED = "go_live_not_activated"


class CustomerReadinessStage(StrEnum):
    SUBSCRIPTION_REQUIRED = "subscription_required"
    NUMBER_PROVISIONING = "number_provisioning"
    NUMBER_PROVISIONING_FAILED = "number_provisioning_failed"
    RECEPTIONIST_SETUP_REQUIRED = "receptionist_setup_required"
    READY = "ready"
    ROUTING_PENDING = "routing_pending"
    LIVE = "live"
    SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class CustomerReadinessSnapshot:
    user_status: str | None
    plan_tier: str | None
    subscription_status: str | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    balance: int
    provisioning_status: str | None
    phone_present: bool
    phone_provider_id_present: bool
    phone_active: bool
    phone_connection_name: str | None
    agent_present: bool
    agent_enabled: bool
    agent_name: str | None
    owner_context: str | None
    system_prompt: str | None
    knowledge_base: str | None
    activation_required: bool
    business_profile_complete: bool
    profile_projection_current: bool
    forwarding_verified: bool
    go_live_approved: bool
    go_live_activated: bool = False


@dataclass(frozen=True, slots=True)
class CustomerReadinessResult:
    stage: CustomerReadinessStage
    subscription_eligible: bool
    can_provision_number: bool
    can_activate: bool
    should_enable_phone: bool
    can_route: bool
    blockers: tuple[ReadinessBlocker, ...]
    warnings: tuple[str, ...]
    evaluated_at: datetime
    policy_version: str

    def can_dispatch(self, *, called_number_matches: bool) -> bool:
        return self.can_route and called_number_matches


class CustomerReadinessPolicy:
    POLICY_VERSION = "runtime-v3"
    ELIGIBLE_SUBSCRIPTION_STATUSES = frozenset({"active", "trialing"})
    SUPPORTED_PLAN = "starter"

    _SUBSCRIPTION_BLOCKERS = frozenset(
        {
            ReadinessBlocker.SUBSCRIPTION_MISSING,
            ReadinessBlocker.PLAN_UNSUPPORTED,
            ReadinessBlocker.SUBSCRIPTION_STATUS_INELIGIBLE,
            ReadinessBlocker.SUBSCRIPTION_PERIOD_MISSING,
            ReadinessBlocker.SUBSCRIPTION_PERIOD_INACTIVE,
        }
    )
    _ACCESS_BLOCKERS = _SUBSCRIPTION_BLOCKERS | frozenset(
        {
            ReadinessBlocker.USER_INACTIVE,
            ReadinessBlocker.MINUTES_EXHAUSTED,
        }
    )
    _ACTIVATION_BLOCKERS = _ACCESS_BLOCKERS | frozenset(
        {
            ReadinessBlocker.PHONE_MISSING,
            ReadinessBlocker.PHONE_PROVIDER_ID_MISSING,
            ReadinessBlocker.AGENT_CONFIG_MISSING,
            ReadinessBlocker.AGENT_SETUP_INCOMPLETE,
            ReadinessBlocker.AGENT_CONTENT_INVALID,
            ReadinessBlocker.BUSINESS_PROFILE_INCOMPLETE,
            ReadinessBlocker.PROFILE_PROJECTION_STALE,
            ReadinessBlocker.FORWARDING_NOT_VERIFIED,
            ReadinessBlocker.GO_LIVE_NOT_APPROVED,
        }
    )
    _RECEPTIONIST_BLOCKERS = frozenset(
        {
            ReadinessBlocker.AGENT_CONFIG_MISSING,
            ReadinessBlocker.AGENT_SETUP_INCOMPLETE,
            ReadinessBlocker.AGENT_CONTENT_INVALID,
        }
    )

    @classmethod
    def evaluate(
        cls,
        snapshot: CustomerReadinessSnapshot,
        *,
        now: datetime | None = None,
    ) -> CustomerReadinessResult:
        evaluated_at = cls._as_utc(now or datetime.now(UTC))
        found: set[ReadinessBlocker] = set()

        if snapshot.user_status != "active":
            found.add(ReadinessBlocker.USER_INACTIVE)

        cls._evaluate_subscription(snapshot, evaluated_at, found)
        if snapshot.balance <= 0:
            found.add(ReadinessBlocker.MINUTES_EXHAUSTED)

        if not snapshot.phone_present:
            found.add(ReadinessBlocker.PHONE_MISSING)
        else:
            if not snapshot.phone_provider_id_present:
                found.add(ReadinessBlocker.PHONE_PROVIDER_ID_MISSING)
            if not snapshot.phone_active:
                found.add(ReadinessBlocker.PHONE_INACTIVE)
            if snapshot.phone_connection_name != "app-active":
                found.add(ReadinessBlocker.PHONE_PROJECTION_INACTIVE)

        cls._evaluate_agent(snapshot, found)
        cls._evaluate_activation(snapshot, found)

        blockers = tuple(blocker for blocker in ReadinessBlocker if blocker in found)
        subscription_eligible = not bool(found & cls._SUBSCRIPTION_BLOCKERS)
        can_provision_number = not bool(found & cls._ACCESS_BLOCKERS)
        can_activate = not bool(found & cls._ACTIVATION_BLOCKERS)
        should_enable_phone = can_activate and snapshot.agent_enabled
        can_route = bool(
            should_enable_phone
            and snapshot.phone_active
            and snapshot.phone_connection_name == "app-active"
            and (not snapshot.activation_required or snapshot.go_live_activated)
        )
        stage = cls._derive_stage(
            snapshot=snapshot,
            found=found,
            can_route=can_route,
            should_enable_phone=should_enable_phone,
        )
        return CustomerReadinessResult(
            stage=stage,
            subscription_eligible=subscription_eligible,
            can_provision_number=can_provision_number,
            can_activate=can_activate,
            should_enable_phone=should_enable_phone,
            can_route=can_route,
            blockers=blockers,
            warnings=(),
            evaluated_at=evaluated_at,
            policy_version=cls.POLICY_VERSION,
        )

    @classmethod
    def _evaluate_subscription(
        cls,
        snapshot: CustomerReadinessSnapshot,
        evaluated_at: datetime,
        found: set[ReadinessBlocker],
    ) -> None:
        if snapshot.subscription_status is None:
            found.add(ReadinessBlocker.SUBSCRIPTION_MISSING)
            return

        if snapshot.plan_tier != cls.SUPPORTED_PLAN:
            found.add(ReadinessBlocker.PLAN_UNSUPPORTED)
        if snapshot.subscription_status not in cls.ELIGIBLE_SUBSCRIPTION_STATUSES:
            found.add(ReadinessBlocker.SUBSCRIPTION_STATUS_INELIGIBLE)

        if snapshot.current_period_start is None or snapshot.current_period_end is None:
            found.add(ReadinessBlocker.SUBSCRIPTION_PERIOD_MISSING)
            return

        period_start = cls._as_utc(snapshot.current_period_start)
        period_end = cls._as_utc(snapshot.current_period_end)
        if not period_start <= evaluated_at < period_end:
            found.add(ReadinessBlocker.SUBSCRIPTION_PERIOD_INACTIVE)

    @classmethod
    def _evaluate_agent(
        cls,
        snapshot: CustomerReadinessSnapshot,
        found: set[ReadinessBlocker],
    ) -> None:
        if not snapshot.agent_present:
            found.add(ReadinessBlocker.AGENT_CONFIG_MISSING)
            return

        agent_name = cls._normalized(snapshot.agent_name)
        owner_context = cls._normalized(snapshot.owner_context)
        system_prompt = cls._normalized(snapshot.system_prompt)
        knowledge_base = cls._normalized(snapshot.knowledge_base)

        if (
            not agent_name
            or agent_name.casefold() == "assistant"
            or not owner_context
            or not (system_prompt or knowledge_base)
        ):
            found.add(ReadinessBlocker.AGENT_SETUP_INCOMPLETE)
        if (
            len(agent_name) > AGENT_NAME_MAX_LENGTH
            or len(owner_context) > OWNER_CONTEXT_MAX_LENGTH
            or len(system_prompt) > SYSTEM_PROMPT_MAX_LENGTH
            or len(knowledge_base) > KNOWLEDGE_BASE_MAX_LENGTH
        ):
            found.add(ReadinessBlocker.AGENT_CONTENT_INVALID)
        if not snapshot.agent_enabled:
            found.add(ReadinessBlocker.AGENT_DISABLED)

    @staticmethod
    def _evaluate_activation(
        snapshot: CustomerReadinessSnapshot,
        found: set[ReadinessBlocker],
    ) -> None:
        if not snapshot.activation_required:
            return
        if not snapshot.business_profile_complete:
            found.add(ReadinessBlocker.BUSINESS_PROFILE_INCOMPLETE)
        if not snapshot.profile_projection_current:
            found.add(ReadinessBlocker.PROFILE_PROJECTION_STALE)
        if not snapshot.forwarding_verified:
            found.add(ReadinessBlocker.FORWARDING_NOT_VERIFIED)
        if not snapshot.go_live_approved:
            found.add(ReadinessBlocker.GO_LIVE_NOT_APPROVED)
        elif not snapshot.go_live_activated:
            found.add(ReadinessBlocker.GO_LIVE_NOT_ACTIVATED)

    @classmethod
    def _derive_stage(
        cls,
        *,
        snapshot: CustomerReadinessSnapshot,
        found: set[ReadinessBlocker],
        can_route: bool,
        should_enable_phone: bool,
    ) -> CustomerReadinessStage:
        if can_route:
            return CustomerReadinessStage.LIVE
        if ReadinessBlocker.SUBSCRIPTION_MISSING in found:
            return CustomerReadinessStage.SUBSCRIPTION_REQUIRED
        if found & cls._ACCESS_BLOCKERS:
            return CustomerReadinessStage.SUSPENDED

        usable_phone = snapshot.phone_present and snapshot.phone_provider_id_present
        inconsistent_phone = bool(
            snapshot.phone_present and not snapshot.phone_provider_id_present
        )
        if not usable_phone:
            if (
                snapshot.provisioning_status == "failed"
                or snapshot.provisioning_status == "succeeded"
                or inconsistent_phone
            ):
                return CustomerReadinessStage.NUMBER_PROVISIONING_FAILED
            return CustomerReadinessStage.NUMBER_PROVISIONING

        if found & cls._RECEPTIONIST_BLOCKERS:
            return CustomerReadinessStage.RECEPTIONIST_SETUP_REQUIRED
        if should_enable_phone:
            return CustomerReadinessStage.ROUTING_PENDING
        return CustomerReadinessStage.READY

    @staticmethod
    def _normalized(value: str | None) -> str:
        return (value or "").strip()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
