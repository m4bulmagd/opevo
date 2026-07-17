# Activation Domain and Profile API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the durable business-profile and activation domain, a canonical activation snapshot, guided receptionist projection, and authenticated profile APIs without changing the live call path until the activation feature flag is enabled.

**Architecture:** `BusinessProfile` owns customer-entered facts, `CustomerActivation` owns explicit milestones, and `AgentConfig` remains a versioned runtime projection. A pure activation policy derives the customer stage from authoritative facts; `ActivationSnapshotService` loads those facts once and the existing readiness policy consumes the same profile/activation prerequisites behind a rollout flag.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 17, pytest 9, uv 0.11.19

## Global Constraints

- Product market is France; product and receptionist language are English.
- One user owns one business profile, one activation journey, one receptionist configuration, and one Presvo number.
- Required profile fields are owner name, business name, business type, public description, IANA timezone, structured hours, existing French number, confirmed carrier, and receptionist name.
- A day is closed or contains one or two non-overlapping local-time intervals.
- Carrier values are exactly `orange`, `sfr`, `bouygues`, `free`, or `other`.
- Customer-entered business content never becomes a raw system prompt; mandatory receptionist policy remains separate.
- Names are at most 100 characters; public description is at most 1,000; FAQs are at most 20 with 200-character questions and 800-character answers; special instructions and escalation notes are at most 2,000 each.
- Existing business numbers are normalized to French E.164.
- New readiness requirements remain behind `ACTIVATION_FLOW_ENABLED=false` until Plan 4 enables the complete local journey.
- No cloud deployment, external provider operation, or real phone-number purchase is authorized by this plan.
- Follow TDD: failing focused test, observed failure, minimal implementation, passing focused test, then commit.

---

### Task 1: Persist the activation aggregate and cardinality constraints

**Files:**
- Create: `apps/api/app/models/business_profile.py`
- Create: `apps/api/app/models/customer_activation.py`
- Create: `apps/api/app/models/activation_event.py`
- Create: `apps/api/app/repositories/business_profile_repository.py`
- Create: `apps/api/app/repositories/customer_activation_repository.py`
- Create: `apps/api/app/repositories/activation_event_repository.py`
- Create: `apps/api/alembic/versions/0012_add_customer_activation_domain.py`
- Create: `apps/api/tests/test_activation_domain_migration.py`
- Modify: `apps/api/app/models/agent_config.py`
- Modify: `apps/api/app/models/phone_number.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/alembic/env.py`
- Modify: `apps/api/tests/integration/test_integrity_constraints.py`
- Modify: `apps/api/tests/test_migration_revision_ids.py`

**Interfaces:**
- Produces: `BusinessProfile`, `CustomerActivation`, and `ActivationEvent` SQLAlchemy models.
- Produces: `BusinessProfileRepository.get_or_create_for_update(user_id)`, `CustomerActivationRepository.get_or_create_for_update(user_id)`, and `ActivationEventRepository.append(...)`.
- Produces: `AgentConfig.business_display_name`, `AgentConfig.profile_projection_revision: int`, and database uniqueness for `PhoneNumber.user_id`.

- [ ] **Step 1: Write model-metadata and migration tests that describe the new aggregate**

```python
def test_activation_models_enforce_one_row_per_user() -> None:
    from app.models.business_profile import BusinessProfile
    from app.models.customer_activation import CustomerActivation
    from app.models.phone_number import PhoneNumber

    assert BusinessProfile.__table__.c.user_id.unique is True
    assert CustomerActivation.__table__.c.user_id.unique is True
    assert PhoneNumber.__table__.c.user_id.unique is True


def test_activation_revision_follows_call_state_machine() -> None:
    migration = _load_migration()
    assert migration.revision == "0012_customer_activation"
    assert migration.down_revision == "0011_call_state_machine"
```

Add a PostgreSQL integration case that concurrently inserts two `PhoneNumber`
rows for one user and asserts exactly one commit succeeds. Add equivalent cases
for `BusinessProfile` and `CustomerActivation`.

- [ ] **Step 2: Run the new tests and observe the missing-model failure**

Run:

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/test_activation_domain_migration.py \
  tests/integration/test_integrity_constraints.py -q
```

Expected: FAIL because the three activation model modules and revision `0012_customer_activation` do not exist.

- [ ] **Step 3: Add the three focused SQLAlchemy models**

Implement the following exact responsibilities; keep each model in its own file:

```python
# app/models/business_profile.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BusinessProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "business_profiles"
    __table_args__ = (
        CheckConstraint("content_revision >= 1", name=conv("ck_business_profiles_content_revision_positive")),
        CheckConstraint("routing_revision >= 1", name=conv("ck_business_profiles_routing_revision_positive")),
        CheckConstraint(
            "confirmed_carrier IS NULL OR confirmed_carrier IN ('orange', 'sfr', 'bouygues', 'free', 'other')",
            name=conv("ck_business_profiles_confirmed_carrier_allowed"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    owner_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    public_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    existing_phone_e164: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detected_carrier: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detected_number_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    carrier_lookup_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    carrier_looked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_carrier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receptionist_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    faqs: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    special_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    routing_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
```

```python
# app/models/customer_activation.py
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CustomerActivation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customer_activations"
    __table_args__ = (
        CheckConstraint("workflow_version >= 1", name=conv("ck_customer_activations_workflow_version_positive")),
        CheckConstraint(
            "verification_status IN ('not_started', 'open', 'claimed', 'succeeded', 'failed', 'expired', 'invalidated')",
            name=conv("ck_customer_activations_verification_status_allowed"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    profile_confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provisioning_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provisioning_idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    verification_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_window_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    verification_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_dispatch_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    verification_routing_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    verified_routing_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forwarding_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    go_live_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    go_live_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

```python
# app/models/activation_event.py
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ActivationEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "activation_events"

    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    activation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("customer_activations.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
```

Add `business_display_name: Mapped[str | None] = mapped_column(String(100),
nullable=True)` and `profile_projection_revision: Mapped[int] =
mapped_column(Integer, nullable=False, default=0)` to `AgentConfig`, and make
`PhoneNumber.user_id` unique at model level.

- [ ] **Step 4: Add repositories with locked get-or-create semantics**

```python
class BusinessProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> BusinessProfile | None:
        result = await self.session.execute(select(BusinessProfile).where(BusinessProfile.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_or_create_for_update(self, user_id: UUID) -> BusinessProfile:
        result = await self.session.execute(
            select(BusinessProfile).where(BusinessProfile.user_id == user_id).with_for_update()
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = BusinessProfile(user_id=user_id)
            self.session.add(profile)
            await self.session.flush()
        return profile
```

Use the same pattern for `CustomerActivationRepository`. Implement
`ActivationEventRepository.append(...)` by first looking up the unique
`idempotency_key`; return the existing event for a duplicate and otherwise add
and flush a new `ActivationEvent`.

- [ ] **Step 5: Write revision 0012 with safe preflight and backfill**

The migration must:

1. preflight duplicate `phone_numbers.user_id` values before DDL and raise a
   count-only `RuntimeError`;
2. create `business_profiles`, `customer_activations`, and `activation_events`;
3. add nullable `agent_configs.business_display_name` and
   `agent_configs.profile_projection_revision` with server default `0`;
4. create `uq_phone_numbers_user_id`;
5. backfill one blank profile and activation row per existing user using
   `INSERT ... SELECT`;
6. remove the temporary server default from the agent column;
7. reverse those operations in dependency-safe order in `downgrade()`.

Use this preflight shape so logs never contain a phone number:

```python
duplicates = connection.execute(
    sa.text(
        "SELECT COUNT(*) AS duplicate_groups FROM ("
        "SELECT user_id FROM phone_numbers GROUP BY user_id HAVING COUNT(*) > 1"
        ") duplicate_users"
    )
).scalar_one()
if duplicates:
    raise RuntimeError(
        "Cannot add uq_phone_numbers_user_id: "
        f"duplicate_user_groups={duplicates}"
    )
```

- [ ] **Step 6: Register model metadata and verify migration behavior**

Import all three models from `app/models/__init__.py` and `alembic/env.py`.
Update the revision-chain test to expect `0012_customer_activation`. Run:

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/test_activation_domain_migration.py \
  tests/test_migration_revision_ids.py \
  tests/integration/test_integrity_constraints.py -q
```

Expected: unit migration tests PASS; PostgreSQL-only concurrency tests SKIP
without `TEST_DATABASE_URL` and PASS in CI.

- [ ] **Step 7: Commit the activation persistence boundary**

```bash
git add apps/api/app/models apps/api/app/repositories \
  apps/api/alembic/versions/0012_add_customer_activation_domain.py \
  apps/api/alembic/env.py apps/api/tests
git commit -m "feat: persist customer activation domain"
```

---

### Task 2: Validate profile drafts, hours, and routing revisions

**Files:**
- Create: `apps/api/app/schemas/business_profile.py`
- Create: `apps/api/app/services/business_profile_service.py`
- Create: `apps/api/app/services/routing_fingerprint.py`
- Create: `apps/api/tests/activation/test_business_profile_service.py`
- Create: `apps/api/tests/activation/test_business_profile_schemas.py`
- Modify: `apps/api/app/repositories/business_profile_repository.py`
- Modify: `apps/api/app/repositories/customer_activation_repository.py`

**Interfaces:**
- Produces: `BusinessProfileDraft`, `BusinessProfileResponse`, `BusinessProfileConstraints`, `CarrierCode`, and `BusinessHours`.
- Produces: `BusinessProfileService.save_draft(user_id, draft)` and `confirm_profile(user_id)`.
- Produces: `routing_fingerprint(profile, phone_number) -> str`.

- [ ] **Step 1: Write failing schema tests for split hours and French numbers**

```python
def test_business_hours_accept_split_day() -> None:
    payload = BusinessHours.model_validate(
        {
            day: {"closed": day in {"saturday", "sunday"}, "intervals": []}
            for day in WEEKDAYS
        }
        | {
            "monday": {
                "closed": False,
                "intervals": [
                    {"start": "09:00", "end": "12:00"},
                    {"start": "14:00", "end": "18:00"},
                ],
            }
        }
    )
    assert len(payload.root["monday"].intervals) == 2


@pytest.mark.parametrize("number", ["0612345678", "+33 6 12 34 56 78"])
def test_profile_normalizes_french_number(number: str) -> None:
    draft = complete_profile_draft(existing_phone_e164=number)
    assert draft.existing_phone_e164 == "+33612345678"
```

Also test missing weekdays, overlapping intervals, more than two intervals,
closed days with intervals, unknown timezones, non-French numbers, 21 FAQs, and
every documented length bound.

- [ ] **Step 2: Run the schema tests and observe missing contracts**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_business_profile_schemas.py -q
```

Expected: FAIL with `ModuleNotFoundError: app.schemas.business_profile`.

- [ ] **Step 3: Implement the bounded Pydantic profile contract**

Define `WEEKDAYS`, `CarrierCode`, `FaqItem`, `OpeningInterval`, `DayHours`,
`BusinessHours`, `BusinessProfileDraft`, `BusinessProfileResponse`, and
`BusinessProfileConstraints` from the same module-level bound constants.
Normalize the number with the existing `normalize_french_number` helper and
validate timezones with `zoneinfo.ZoneInfo`.

```python
CarrierCode = Literal["orange", "sfr", "bouygues", "free", "other"]
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
NAME_MAX_LENGTH = 100
BUSINESS_TYPE_MAX_LENGTH = 100
PUBLIC_DESCRIPTION_MAX_LENGTH = 1_000
FAQ_MAX_ITEMS = 20
FAQ_QUESTION_MAX_LENGTH = 200
FAQ_ANSWER_MAX_LENGTH = 800
INSTRUCTIONS_MAX_LENGTH = 2_000
ESCALATION_NOTES_MAX_LENGTH = 2_000


class OpeningInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: time
    end: time

    @model_validator(mode="after")
    def require_forward_interval(self) -> Self:
        if self.end <= self.start:
            raise ValueError("Opening interval must end after it starts")
        return self


class DayHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    closed: bool
    intervals: list[OpeningInterval] = Field(max_length=2)

    @model_validator(mode="after")
    def validate_day(self) -> Self:
        if self.closed and self.intervals:
            raise ValueError("Closed days cannot contain intervals")
        ordered = sorted(self.intervals, key=lambda interval: interval.start)
        if any(left.end > right.start for left, right in pairwise(ordered)):
            raise ValueError("Opening intervals cannot overlap")
        if not self.closed and not ordered:
            raise ValueError("Open days require at least one interval")
        self.intervals = ordered
        return self
```

Use `RootModel[dict[str, DayHours]]` for `BusinessHours` and require exactly the
seven weekday keys. Serialize `time` values to `HH:MM` when storing JSON.
Expose the same values through `BusinessProfileConstraints` so the web app can
source labels, counters, and `maxLength` attributes from the API instead of
copying product limits into TypeScript.

```python
class BusinessProfileConstraints(BaseModel):
    model_config = ConfigDict(frozen=True)
    name_max_length: int = NAME_MAX_LENGTH
    business_type_max_length: int = BUSINESS_TYPE_MAX_LENGTH
    public_description_max_length: int = PUBLIC_DESCRIPTION_MAX_LENGTH
    faq_max_items: int = FAQ_MAX_ITEMS
    faq_question_max_length: int = FAQ_QUESTION_MAX_LENGTH
    faq_answer_max_length: int = FAQ_ANSWER_MAX_LENGTH
    special_instructions_max_length: int = INSTRUCTIONS_MAX_LENGTH
    escalation_notes_max_length: int = ESCALATION_NOTES_MAX_LENGTH
    max_intervals_per_day: int = 2
    phone_country: Literal["FR"] = "FR"
```

- [ ] **Step 4: Write failing service tests for revision and invalidation rules**

```python
@pytest.mark.anyio
async def test_routing_change_clears_verification_and_go_live(db_session, active_user) -> None:
    service = build_profile_service(db_session)
    await service.save_draft(active_user.id, complete_profile_draft())
    await service.confirm_profile(active_user.id)
    activation = await CustomerActivationRepository(db_session).get_or_create_for_update(active_user.id)
    activation.verified_routing_fingerprint = "old"
    activation.forwarding_verified_at = datetime.now(UTC)
    activation.go_live_approved_at = datetime.now(UTC)
    await db_session.commit()

    updated = complete_profile_draft(existing_phone_e164="+33144556677")
    await service.save_draft(active_user.id, updated)

    assert activation.verified_routing_fingerprint is None
    assert activation.forwarding_verified_at is None
    assert activation.go_live_approved_at is None
    assert activation.activated_at is None
    assert activation.verification_status == "invalidated"
```

Add the inverse test: changing only hours or FAQs increments
`content_revision`, preserves `routing_revision`, and does not clear forwarding
verification, final go-live approval, `activated_at`, or the already completed
initial profile milestone.

- [ ] **Step 5: Implement `BusinessProfileService` under the user lock**

`save_draft` must lock the user first, then the profile and activation. Compare
the normalized routing tuple `(existing_phone_e164, confirmed_carrier)` before
and after the update. Increment `content_revision` for any real change; increment
`routing_revision` and clear verification/go-live only for a routing change.

```python
ROUTING_FIELDS = ("existing_phone_e164", "confirmed_carrier")


async def save_draft(self, user_id: UUID, draft: BusinessProfileDraft) -> BusinessProfile:
    user = await self.user_repository.get_by_id_for_update(user_id)
    if user is None:
        raise BusinessProfileNotFoundError
    profile = await self.profile_repository.get_or_create_for_update(user_id)
    activation = await self.activation_repository.get_or_create_for_update(user_id)
    updates = draft.to_storage_dict()
    phone_changed = profile.existing_phone_e164 != updates["existing_phone_e164"]
    if phone_changed:
        updates |= {
            "detected_carrier": None,
            "detected_number_type": None,
            "carrier_lookup_status": None,
            "carrier_looked_up_at": None,
            "confirmed_carrier": None,
        }
    changed = {name for name, value in updates.items() if getattr(profile, name) != value}
    routing_changed = bool(changed & set(ROUTING_FIELDS))
    for name, value in updates.items():
        setattr(profile, name, value)
    if changed:
        profile.content_revision += 1
    if routing_changed:
        profile.routing_revision += 1
        activation.verification_window_started_at = None
        activation.verification_window_expires_at = None
        activation.verification_session_id = None
        activation.verification_claimed_at = None
        activation.verification_dispatch_id = None
        activation.verification_routing_fingerprint = None
        activation.verification_status = "invalidated"
        activation.verified_routing_fingerprint = None
        activation.forwarding_verified_at = None
        activation.go_live_requested_at = None
        activation.go_live_approved_at = None
        activation.activated_at = None
    await self.session.commit()
    await self.session.refresh(profile)
    return profile
```

`confirm_profile` requires every required field and a confirmed carrier, then
sets `profile_confirmed_revision` and `profile_confirmed_at`. Saving a draft
must never establish this initial milestone. After it has been explicitly
completed once, structurally valid content-only edits preserve it; readiness is
protected by the atomically updated runtime projection. A changed existing
number clears lookup and carrier confirmation, making the profile incomplete
until the customer confirms a carrier for the new number. Because this product
slice is France-only and the number has already passed French validation,
initial confirmation also sets the locked `User.country_code="FR"`. Add a test
covering an existing Clerk or local user whose country was previously null;
Plan 2 provisioning must not depend on hidden database edits.

- [ ] **Step 6: Implement a deterministic routing fingerprint**

```python
def routing_fingerprint(profile: BusinessProfile, phone_number: PhoneNumber | None) -> str:
    payload = {
        "existing_phone_e164": profile.existing_phone_e164,
        "confirmed_carrier": profile.confirmed_carrier,
        "presvo_phone_e164": phone_number.e164 if phone_number is not None else None,
        "routing_revision": profile.routing_revision,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Test that the output is stable, contains no raw phone number, and changes when
any routing-sensitive value changes.

- [ ] **Step 7: Run focused service and schema tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest tests/activation/test_business_profile_schemas.py \
  tests/activation/test_business_profile_service.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit validated profile behavior**

```bash
git add apps/api/app/schemas/business_profile.py \
  apps/api/app/services/business_profile_service.py \
  apps/api/app/services/routing_fingerprint.py \
  apps/api/app/repositories/business_profile_repository.py \
  apps/api/app/repositories/customer_activation_repository.py \
  apps/api/tests/activation
git commit -m "feat: validate resumable business profiles"
```

---

### Task 3: Project guided profile content into the receptionist runtime

**Files:**
- Create: `apps/api/app/services/receptionist_projection_service.py`
- Create: `apps/api/tests/activation/test_receptionist_projection_service.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/services/business_profile_service.py`
- Modify: `apps/api/app/services/agent_config_service.py`
- Modify: `apps/api/app/repositories/agent_config_repository.py`
- Modify: `apps/api/app/schemas/agent_content.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/tests/agent/test_agent_config_api.py`
- Modify: `apps/api/tests/workers/test_post_call_outbox_handlers.py`
- Modify: `apps/agent/agent/schemas.py`
- Modify: `apps/agent/tests/test_call_limits.py`

**Interfaces:**
- Produces: `ReceptionistProjectionService.project(profile, config) -> AgentConfig`.
- Consumes: `BusinessProfile.content_revision` and writes `AgentConfig.profile_projection_revision`.
- Updates: normal dispatch metadata uses the projected business display name for its greeting.

- [ ] **Step 1: Write a failing deterministic-projection test**

```python
def test_projection_uses_guided_labels_and_no_system_prompt() -> None:
    projection = build_projection(complete_profile())

    assert projection.agent_name == "Claire"
    assert projection.business_display_name == "Atelier Nord"
    assert "Business name: Atelier Nord" in projection.owner_context
    assert "Opening hours" in projection.knowledge_base
    assert "Frequently asked questions" in projection.knowledge_base
    assert projection.system_prompt == ""
    assert projection.profile_projection_revision == 2
```

Add tests that output remains within `OWNER_CONTEXT_MAX_LENGTH` and
`KNOWLEDGE_BASE_MAX_LENGTH`, that customer text is preserved as data, and that
the projector raises `ReceptionistProjectionTooLargeError` instead of truncating
silently. Add an incomplete-draft case proving missing values render as stable
`Not provided` data, a missing receptionist name preserves the existing/default
agent name, and no projection field contains the literal string `None`.

- [ ] **Step 2: Run the focused test and observe the missing service**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_receptionist_projection_service.py -q
```

Expected: FAIL with a missing projection service.

- [ ] **Step 3: Implement one deterministic projection builder**

```python
@dataclass(frozen=True, slots=True)
class ReceptionistProjection:
    agent_name: str
    business_display_name: str | None
    owner_context: str
    system_prompt: str
    knowledge_base: str
    profile_projection_revision: int


def projection_value(value: str | None) -> str:
    return value if value is not None else "Not provided"


def build_receptionist_projection(
    profile: BusinessProfile,
    config: AgentConfig,
) -> ReceptionistProjection:
    owner_context = "\n".join(
        (
            f"Owner name: {projection_value(profile.owner_name)}",
            f"Business name: {projection_value(profile.business_name)}",
            f"Business type: {projection_value(profile.business_type)}",
            f"Public description: {projection_value(profile.public_description)}",
            f"Timezone: {projection_value(profile.timezone)}",
        )
    )
    knowledge = render_profile_knowledge(profile)
    if len(owner_context) > OWNER_CONTEXT_MAX_LENGTH or len(knowledge) > KNOWLEDGE_BASE_MAX_LENGTH:
        raise ReceptionistProjectionTooLargeError
    return ReceptionistProjection(
        agent_name=profile.receptionist_name or config.agent_name,
        business_display_name=profile.business_name,
        owner_context=owner_context,
        system_prompt="",
        knowledge_base=knowledge,
        profile_projection_revision=profile.content_revision,
    )
```

`render_profile_knowledge` must emit stable labeled sections for opening hours,
FAQs, special instructions, and escalation notes. It must not interpolate any
mandatory policy language. Replace missing draft values with the stable data
label `Not provided`; never stringify Python `None`. Implement the public
`ReceptionistProjectionService.project(profile, config) -> AgentConfig`
interface from the task contract, using the deterministic builder internally.

Align the existing API and agent `AGENT_NAME_MAX_LENGTH` constants from 80 to
the approved 100-character receptionist-name bound. Update the agent boundary
test to accept 100 and reject 101; the API and agent must never disagree about
an otherwise valid profile.

- [ ] **Step 4: Apply projection in the profile transaction**

After updating the profile and before committing, lock or create `AgentConfig`,
build the projection, and write exactly the six projection fields. A projection
failure rolls back the profile save. Keep the existing agent PATCH endpoint for
backward compatibility while the rollout flag is false.

Move `activation_flow_enabled: bool = False` into `Settings` in this task so the
guard exists before it is consumed. When enabled, PATCH requests containing any
of `agent_name`, `owner_context`, `system_prompt`, or `knowledge_base` must fail
with HTTP 409 and detail code `agent_content_managed_by_profile`; PATCHes that
only contain non-projected fields retain their existing behavior. Add a default-
false settings assertion plus endpoint tests for both flag states. This moves a
default-off prerequisite forward from Task 4 and does not enable activation.

When building normal LiveKit dispatch metadata, use
`agent_config.business_display_name` as the existing `owner_name`/greeting
value. Use the current `user.full_name` fallback only for legacy rows while the
rollout flag is false; when the flag is true, a missing projected business name
must fail dispatch configuration closed. Add dispatch payload tests proving the
greeting uses the business name, the owner's name remains bounded inside owner
context, the legacy fallback remains default-off compatible, and enabled mode
fails closed without a business name.

- [ ] **Step 5: Run profile, agent-config, and readiness regression tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_receptionist_projection_service.py \
  tests/activation/test_business_profile_service.py \
  tests/agent/test_agent_config_api.py \
  tests/workers/test_post_call_outbox_handlers.py -q
cd ../agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_call_limits.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the runtime projection**

```bash
git add apps/api/app/core/config.py apps/api/app/routers/agent.py \
  apps/api/app/services/receptionist_projection_service.py \
  apps/api/app/services/business_profile_service.py \
  apps/api/app/services/agent_config_service.py \
  apps/api/app/repositories/agent_config_repository.py \
  apps/api/app/schemas/agent_content.py \
  apps/api/app/workers/jobs/outbox_topics.py \
  apps/api/tests/activation/test_receptionist_projection_service.py \
  apps/api/tests/agent/test_agent_config_api.py \
  apps/api/tests/workers/test_post_call_outbox_handlers.py \
  apps/agent/agent/schemas.py apps/agent/tests/test_call_limits.py
git commit -m "feat: project profiles into receptionist runtime"
```

---

### Task 4: Derive the canonical activation stage and extend readiness

**Files:**
- Create: `apps/api/app/services/activation_policy.py`
- Create: `apps/api/app/services/activation_snapshot_service.py`
- Create: `apps/api/app/schemas/activation.py`
- Create: `apps/api/tests/activation/test_activation_policy.py`
- Create: `apps/api/tests/activation/test_activation_snapshot_service.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/services/customer_readiness_policy.py`
- Modify: `apps/api/app/services/customer_readiness_service.py`
- Modify: `apps/api/tests/services/test_customer_readiness_policy.py`
- Modify: `apps/api/tests/services/test_customer_readiness_service.py`

**Interfaces:**
- Produces: `ActivationStage`, `ActivationFacts`, `ActivationDecision`, and `ActivationPolicy.evaluate(facts)`.
- Produces: `ActivationSnapshotService.get(user_id, now=None) -> ActivationSnapshotResponse`.
- Extends: `CustomerReadinessSnapshot` with activation prerequisites and `ReadinessBlocker` with stable codes.

- [ ] **Step 1: Write a table-driven failing activation-policy test**

```python
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"profile_confirmed": False}, ActivationStage.PROFILE_REQUIRED),
        ({"subscription_eligible": False}, ActivationStage.PAYMENT_REQUIRED),
        ({"provisioning_consented": False}, ActivationStage.PROVISIONING_CONSENT_REQUIRED),
        ({"provisioning_status": "running"}, ActivationStage.PROVISIONING),
        ({"provisioning_status": "failed"}, ActivationStage.PROVISIONING_FAILED),
        ({"phone_ready": True, "forwarding_verified": False}, ActivationStage.FORWARDING_REQUIRED),
        ({"verification_window_open": True}, ActivationStage.VERIFICATION_WINDOW_OPEN),
        ({"forwarding_verified": True, "go_live_approved": False}, ActivationStage.READY_TO_ACTIVATE),
        ({"go_live_pending": True}, ActivationStage.ACTIVATING),
        ({"go_live_approved": True, "runtime_ready": False}, ActivationStage.RUNTIME_PAUSED),
        ({"go_live_approved": True, "runtime_ready": True}, ActivationStage.ACTIVE),
    ],
)
def test_activation_stage_precedence(overrides, expected) -> None:
    facts = replace(ready_facts(), **overrides)
    assert ActivationPolicy.evaluate(facts).stage is expected
```

- [ ] **Step 2: Run the policy test and observe the missing module**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest tests/activation/test_activation_policy.py -q
```

Expected: FAIL with missing `activation_policy`.

- [ ] **Step 3: Implement the pure activation policy and response schema**

```python
class ActivationStage(StrEnum):
    PROFILE_REQUIRED = "profile_required"
    PAYMENT_REQUIRED = "payment_required"
    PROVISIONING_CONSENT_REQUIRED = "provisioning_consent_required"
    PROVISIONING = "provisioning"
    PROVISIONING_FAILED = "provisioning_failed"
    FORWARDING_REQUIRED = "forwarding_required"
    VERIFICATION_WINDOW_OPEN = "verification_window_open"
    READY_TO_ACTIVATE = "ready_to_activate"
    ACTIVATING = "activating"
    RUNTIME_PAUSED = "runtime_paused"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    stage: ActivationStage
    completed_milestones: tuple[str, ...]
    next_action: str | None
    blockers: tuple[str, ...]
```

Implement `evaluate` as explicit ordered conditions matching the test table.
Do not infer stages from nullable frontend fields.

- [ ] **Step 4: Extend central readiness behind the rollout flag**

Use `activation_flow_enabled: bool = False`, introduced in Task 3, to gate the
readiness extension. Add these blocker codes:

```python
BUSINESS_PROFILE_INCOMPLETE = "business_profile_incomplete"
PROFILE_PROJECTION_STALE = "profile_projection_stale"
FORWARDING_NOT_VERIFIED = "forwarding_not_verified"
GO_LIVE_NOT_APPROVED = "go_live_not_approved"
```

Extend `CustomerReadinessSnapshot` with:

```python
activation_required: bool
business_profile_complete: bool
profile_projection_current: bool
forwarding_verified: bool
go_live_approved: bool
```

When `activation_required` is false, behavior and existing tests remain
unchanged. When true, the four new blockers participate in
`_ACTIVATION_BLOCKERS`, `should_enable_phone`, and `can_route`. Bump
`POLICY_VERSION` to `runtime-v2` and update exact assertions.

- [ ] **Step 5: Implement `ActivationSnapshotService` as the single loader**

Load each authoritative row once, compute profile completeness, current
projection, current routing fingerprint, forwarding verification, billing
eligibility, provisioning/number state, and runtime readiness. Return a
Pydantic `ActivationSnapshotResponse` containing the fields from the approved
spec, including `profile_constraints=BusinessProfileConstraints()`. Use safe
blocker strings; never return `last_error_payload`.

Keep durable milestone timestamps in an explicit nested projection so the web
does not infer whether this account was previously active:

```python
class ActivationProgressResponse(BaseModel):
    profile_confirmed_at: datetime | None
    provisioning_consented_at: datetime | None
    forwarding_verified_at: datetime | None
    go_live_approved_at: datetime | None
    activated_at: datetime | None
    last_failure_code: str | None


class ActivationSnapshotResponse(BaseModel):
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
```

Plans 2 and 3 extend this same response with carrier/provisioning detail,
forwarding guidance, and verification state; they do not create alternate
snapshot envelopes.

```python
facts = ActivationFacts(
    profile_confirmed=bool(
        profile is not None
        and profile_is_complete(profile)
        and activation.profile_confirmed_at is not None
    ),
    subscription_eligible=subscription_access.eligible,
    provisioning_consented=activation.provisioning_consented_at is not None,
    provisioning_status=provisioning.status if provisioning is not None else None,
    phone_ready=bool(phone and phone.provider_number_id),
    verification_window_open=window_is_open(activation, now),
    forwarding_verified=verified_fingerprint == current_fingerprint,
    go_live_pending=bool(activation.go_live_approved_at and activation.activated_at is None),
    go_live_approved=activation.go_live_approved_at is not None,
    runtime_ready=readiness.result.can_route,
)
decision = ActivationPolicy.evaluate(facts)
```

- [ ] **Step 6: Run activation and readiness tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_activation_policy.py \
  tests/activation/test_activation_snapshot_service.py \
  tests/services/test_customer_readiness_policy.py \
  tests/services/test_customer_readiness_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit canonical activation state**

```bash
git add apps/api/app/core/config.py apps/api/app/schemas/activation.py \
  apps/api/app/services/activation_policy.py \
  apps/api/app/services/activation_snapshot_service.py \
  apps/api/app/services/customer_readiness_policy.py \
  apps/api/app/services/customer_readiness_service.py \
  apps/api/tests/activation apps/api/tests/services
git commit -m "feat: derive canonical activation readiness"
```

---

### Task 5: Bootstrap and expose the profile-first activation API

**Files:**
- Create: `apps/api/app/routers/activation.py`
- Create: `apps/api/tests/activation/test_activation_api.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/services/auth_service.py`
- Modify: `apps/api/tests/auth/test_clerk_sync.py`
- Modify: `apps/api/app/services/onboarding_service.py`
- Modify: `apps/api/tests/onboarding/test_onboarding_api.py`

**Interfaces:**
- Produces: `GET /api/activation`, `PUT /api/business-profile`, and `POST /api/activation/confirm-profile`.
- Preserves: `/api/onboarding` as a compatibility projection until Plan 4 removes old web usage.

- [ ] **Step 1: Write failing authenticated API tests**

```python
@pytest.mark.anyio
async def test_profile_first_api_round_trip(async_client, synced_user_token, complete_profile_payload) -> None:
    saved = await async_client.put(
        "/api/business-profile",
        json=complete_profile_payload,
        headers={"Authorization": f"Bearer {synced_user_token}"},
    )
    assert saved.status_code == 200
    assert saved.json()["existing_phone_e164"] == "+33612345678"

    confirmed = await async_client.post(
        "/api/activation/confirm-profile",
        headers={"Authorization": f"Bearer {synced_user_token}"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["stage"] == "payment_required"
```

Add unauthenticated, unsynced, invalid profile, stale confirmation, cross-user,
and idempotent Clerk webhook/bootstrap cases.

- [ ] **Step 2: Run API tests and observe 404 failures**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_activation_api.py tests/auth/test_clerk_sync.py -q
```

Expected: FAIL because the activation router is not registered and Clerk sync
does not create the new aggregate.

- [ ] **Step 3: Bootstrap profile and activation with every synced user**

In `AuthService.sync_clerk_user`, after creating the user and default agent
config, call `get_or_create_for_update` for both new aggregate rows before the
single commit. Existing users are already covered by migration backfill. Keep
duplicate Clerk events idempotent.

- [ ] **Step 4: Add thin authenticated routes**

```python
router = APIRouter(tags=["activation"])


@router.get("/api/activation", response_model=ActivationSnapshotResponse)
async def get_activation(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: ActivationSnapshotService = Depends(get_activation_snapshot_service),
) -> ActivationSnapshotResponse:
    return await service.get(identity.internal_user_id)


@router.put("/api/business-profile", response_model=BusinessProfileResponse)
async def put_business_profile(
    payload: BusinessProfileDraft,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: BusinessProfileService = Depends(get_business_profile_service),
) -> BusinessProfileResponse:
    profile = await service.save_draft(identity.internal_user_id, payload)
    return BusinessProfileResponse.model_validate(profile, from_attributes=True)


@router.post("/api/activation/confirm-profile", response_model=ActivationSnapshotResponse)
async def confirm_profile(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    command_service: BusinessProfileService = Depends(get_business_profile_service),
    snapshot_service: ActivationSnapshotService = Depends(get_activation_snapshot_service),
) -> ActivationSnapshotResponse:
    await command_service.confirm_profile(identity.internal_user_id)
    return await snapshot_service.get(identity.internal_user_id)
```

Translate domain errors to stable `422` or `409` detail objects such as
`{"code": "profile_incomplete", "fields": [...]}`. Never return exception text.

- [ ] **Step 5: Register the router and preserve onboarding compatibility**

Include `activation_router` from `create_app`. Update `OnboardingService` to
continue returning its current contract while sourcing readiness from the
extended central policy. Do not delete the old endpoint in this plan.

- [ ] **Step 6: Run the complete focused slice**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync ruff check app tests
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync mypy app
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation tests/auth/test_clerk_sync.py tests/onboarding \
  tests/services/test_customer_readiness_policy.py \
  tests/services/test_customer_readiness_service.py -q
```

Expected: Ruff and mypy exit 0; focused tests PASS.

- [ ] **Step 7: Commit the profile-first API**

```bash
git add apps/api/app/routers/activation.py apps/api/app/main.py \
  apps/api/app/services/auth_service.py apps/api/app/services/onboarding_service.py \
  apps/api/tests/activation/test_activation_api.py \
  apps/api/tests/auth/test_clerk_sync.py apps/api/tests/onboarding
git commit -m "feat: expose profile-first activation api"
```

---

### Task 6: Verify Plan 1 as an independently safe slice

**Files:**
- Modify only if verification reveals a defect in files already owned by Tasks 1-5.

**Interfaces:**
- Verifies: migration chain, API/agent contracts, readiness compatibility, lint, typing, and deterministic tests.

- [ ] **Step 1: Run the full API verification suite**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync ruff check app tests
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync mypy app
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q
cd ../agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest tests/test_call_limits.py -q
```

Expected: all deterministic checks PASS. The known credentialed/provider tests
may SKIP; no new activation test may skip.

- [ ] **Step 2: Verify blank-database migration SQL and head**

```bash
cd apps/api
env DATABASE_URL=postgresql+asyncpg://migration:password@database.example/presvo \
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync alembic -c alembic.ini \
  upgrade head --sql
```

Expected: exit 0 and final revision `0012_customer_activation` in generated SQL.

- [ ] **Step 3: Confirm the rollout flag preserves the current runtime by default**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/livekit/test_dispatch_service.py \
  tests/workers/test_phone_routing_readiness.py -q
```

Expected: PASS with `ACTIVATION_FLOW_ENABLED` absent/false.

- [ ] **Step 4: Commit only verification-driven corrections**

```bash
git status --short
git add apps/api apps/agent
git commit -m "fix: close activation domain verification gaps"
```

If `git status --short` is empty, do not create an empty commit.
