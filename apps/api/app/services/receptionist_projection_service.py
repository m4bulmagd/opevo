from dataclasses import dataclass

from app.models.agent_config import AgentConfig
from app.models.business_profile import BusinessProfile
from app.schemas.agent_content import (
    KNOWLEDGE_BASE_MAX_LENGTH,
    OWNER_CONTEXT_MAX_LENGTH,
)
from app.schemas.business_profile import WEEKDAYS


NOT_PROVIDED = "Not provided"


class ReceptionistProjectionTooLargeError(Exception):
    def __init__(self, field_name: str) -> None:
        super().__init__(f"Projected {field_name} exceeds its runtime limit")
        self.field_name = field_name


@dataclass(frozen=True, slots=True)
class ReceptionistProjection:
    agent_name: str
    business_display_name: str | None
    owner_context: str | None
    system_prompt: str
    knowledge_base: str
    profile_projection_revision: int


def projection_value(value: object | None) -> str:
    return NOT_PROVIDED if value is None else str(value)


def _render_opening_hours(business_hours: dict | None) -> str:
    if business_hours is None:
        return NOT_PROVIDED

    lines: list[str] = []
    for day in WEEKDAYS:
        day_hours = business_hours.get(day)
        if not isinstance(day_hours, dict):
            lines.append(f"{day.title()}: {NOT_PROVIDED}")
            continue
        if day_hours.get("closed"):
            lines.append(f"{day.title()}: Closed")
            continue
        intervals = day_hours.get("intervals")
        if not isinstance(intervals, list) or not intervals:
            lines.append(f"{day.title()}: {NOT_PROVIDED}")
            continue
        rendered_intervals = []
        for interval in intervals:
            if not isinstance(interval, dict):
                rendered_intervals.append(NOT_PROVIDED)
                continue
            rendered_intervals.append(
                f"{projection_value(interval.get('start'))}–"
                f"{projection_value(interval.get('end'))}"
            )
        lines.append(f"{day.title()}: {', '.join(rendered_intervals)}")
    return "\n".join(lines)


def _render_faqs(faqs: list[dict[str, str]] | None) -> str:
    if not faqs:
        return NOT_PROVIDED
    return "\n\n".join(
        (
            f"Question: {projection_value(faq.get('question'))}\n"
            f"Answer: {projection_value(faq.get('answer'))}"
        )
        for faq in faqs
    )


def render_profile_knowledge(profile: BusinessProfile) -> str:
    return "\n\n".join(
        (
            f"Opening hours:\n{_render_opening_hours(profile.business_hours)}",
            f"Frequently asked questions:\n{_render_faqs(profile.faqs)}",
            f"Special instructions:\n{projection_value(profile.special_instructions)}",
            f"Escalation notes:\n{projection_value(profile.escalation_notes)}",
        )
    )


def build_receptionist_projection(
    profile: BusinessProfile,
    config: AgentConfig,
) -> ReceptionistProjection:
    generated_owner_context = "\n".join(
        (
            f"Owner name: {projection_value(profile.owner_name)}",
            f"Business name: {projection_value(profile.business_name)}",
            f"Business type: {projection_value(profile.business_type)}",
            f"Public description: {projection_value(profile.public_description)}",
            f"Timezone: {projection_value(profile.timezone)}",
        )
    )
    owner_context = (
        (profile.owner_context_override or None)
        if profile.owner_context_override is not None
        else generated_owner_context
    )
    knowledge_base = (
        profile.knowledge_base_override
        if profile.knowledge_base_override is not None
        else render_profile_knowledge(profile)
    )
    system_prompt = profile.system_prompt_override or ""
    if owner_context is not None and len(owner_context) > OWNER_CONTEXT_MAX_LENGTH:
        raise ReceptionistProjectionTooLargeError("owner_context")
    if len(knowledge_base) > KNOWLEDGE_BASE_MAX_LENGTH:
        raise ReceptionistProjectionTooLargeError("knowledge_base")
    return ReceptionistProjection(
        agent_name=profile.receptionist_name or config.agent_name,
        business_display_name=profile.business_name,
        owner_context=owner_context,
        system_prompt=system_prompt,
        knowledge_base=knowledge_base,
        profile_projection_revision=profile.content_revision,
    )


class ReceptionistProjectionService:
    def project(
        self,
        profile: BusinessProfile,
        config: AgentConfig,
    ) -> AgentConfig:
        projection = build_receptionist_projection(profile, config)
        config.agent_name = projection.agent_name
        config.business_display_name = projection.business_display_name
        config.owner_context = projection.owner_context
        config.system_prompt = projection.system_prompt
        config.knowledge_base = projection.knowledge_base
        config.profile_projection_revision = projection.profile_projection_revision
        return config
