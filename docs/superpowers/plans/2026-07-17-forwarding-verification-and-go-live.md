# Forwarding Verification and Go-Live Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give customers safe carrier-aware conditional-forwarding guidance, verify forwarding through a single-use fixed-message system call, and require an explicit readiness-checked go-live action before normal calls can dispatch.

**Architecture:** A versioned Presvo instruction catalog supplies safe carrier content. `ForwardingVerificationService` owns durable windows and sessions; the LiveKit webhook checks it before normal call admission and emits a distinct outbox dispatch with a distinct JWT audience. The agent parses a typed job union and runs a TTS-only fixed-message path that cannot create normal call data. `ActivationGoLiveService` records approval and reconciles provider routing through the existing central readiness/outbox boundary.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, transactional outbox, LiveKit API, LiveKit Agents, Pydantic 2, pytest 9

## Dependencies

- Complete `2026-07-17-activation-domain-and-profile-api.md`.
- Complete `2026-07-17-consent-provisioning-and-local-providers.md`.
- Required records include verification claim/dispatch timestamps and `activated_at` in `CustomerActivation`.

## Global Constraints

- Forward only unanswered, busy, and unreachable calls; unconditional forwarding is never the default.
- The verification window lasts exactly ten minutes according to server UTC time.
- The fixed English message is exactly: `Forwarding test successful. Return to Presvo to go live.`
- Verification invokes no LLM, accepts no customer prompt, and enables no caller conversation.
- Verification creates no normal `Call`, recording, transcript, summary, notification, or usage charge.
- Forwarding is marked verified only after successful message playback acknowledgement.
- Redirect/diversion metadata is validated when available; the flow is an operational check, not cryptographic ownership proof.
- A changed existing number, confirmed carrier, assigned Presvo number, or routing revision invalidates verification and go-live.
- No normal call can dispatch without current verification and explicit go-live approval.
- All LiveKit Agents changes require deterministic tests; credentialed LiveKit evaluation remains optional pre-release work.
- No cloud deployment or external call is authorized by this plan.

---

### Task 1: Publish versioned carrier-aware forwarding guidance

**Files:**
- Create: `apps/api/app/schemas/forwarding.py`
- Create: `apps/api/app/services/forwarding_instruction_catalog.py`
- Create: `apps/api/tests/activation/test_forwarding_instruction_catalog.py`
- Modify: `apps/api/app/schemas/activation.py`
- Modify: `apps/api/app/services/activation_snapshot_service.py`
- Modify: `apps/api/tests/activation/test_activation_snapshot_service.py`

**Interfaces:**
- Produces: `ForwardingInstructionCatalog.for_profile(carrier, number_type, presvo_number) -> ForwardingGuide`.
- Extends: activation snapshot `forwarding` field with a versioned customer guide.

- [ ] **Step 1: Write failing catalog tests for conditional-only guidance**

```python
@pytest.mark.parametrize("carrier", ["orange", "sfr", "bouygues", "free", "other"])
def test_every_carrier_has_three_conditional_sections(carrier: CarrierCode) -> None:
    guide = ForwardingInstructionCatalog().for_profile(
        carrier=carrier,
        number_type="fixed",
        presvo_number="+33912345678",
    )
    assert [step.condition for step in guide.steps] == ["unanswered", "busy", "unreachable"]
    assert all("unconditional" not in step.title.lower() for step in guide.steps)


def test_sfr_fixed_uses_only_verified_copyable_codes() -> None:
    guide = build_guide("sfr", "fixed")
    assert guide.step("busy").dial_code == "*69*0912345678#"
    assert guide.step("unanswered").dial_code == "*61*0912345678#"
    assert guide.step("unreachable").dial_code is None
```

Add equivalent Free fixed tests, plus tests that Orange, Bouygues, mobile, and
Other variants do not guess unsupported codes and always present a safe account
settings path.

- [ ] **Step 2: Run the catalog test and observe the missing service**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_forwarding_instruction_catalog.py -q
```

Expected: FAIL with missing forwarding catalog.

- [ ] **Step 3: Define the versioned guide contract**

```python
class ForwardingStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    condition: Literal["unanswered", "busy", "unreachable"]
    title: str
    instructions: list[str]
    dial_code: str | None = None
    disable_code: str | None = None
    source_url: HttpUrl | None = None


class ForwardingGuide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    carrier: CarrierCode
    number_type: str | None
    presvo_number: str
    warning: str
    steps: list[ForwardingStep]
```

Set catalog version `fr-forwarding-2026-07-17`. The warning must state that the
carrier may charge forwarded-call minutes and that exact availability depends
on the customer's plan.

- [ ] **Step 4: Implement verified fixed-line content and safe fallbacks**

Use these current official sources in the catalog:

- Orange account/voice-service guidance:
  `https://assistance.orange.fr/nid/38921`
- Orange conditional forwarding UI:
  `https://assistance.orange.fr/oid/20084`
- SFR fixed busy/non-answer codes:
  `https://assistance.sfr.fr/internet-tel-fixe/tel-fixe/activer-desactiver-options.html`
- Free fixed busy code:
  `https://assistance.free.fr/articles/551`
- Free fixed non-answer code:
  `https://assistance.free.fr/articles/552`
- Free mobile forwarding overview:
  `https://assistance.free.fr/articles/1755`
- Bouygues current tariff guide confirming forwarding availability and billing:
  `https://www.bouyguestelecom.fr/static/cms/tarifs/20260330_RCBT_Guide-des-tarifs_BD.pdf`

Exact copyable fixed-line codes are limited to:

```python
VERIFIED_FIXED_CODES = {
    "sfr": {
        "busy": ("*69*{national_number}#", "#69#"),
        "unanswered": ("*61*{national_number}#", "#61#"),
    },
    "free": {
        "busy": ("*69*{national_number}#", "#69#"),
        "unanswered": ("*61*{national_number}*20#", "#61#"),
    },
}
```

Format the assigned E.164 number to the French national dialing form before
interpolating a fixed-line code. For example, `+33912345678` becomes
`0912345678`. Implement this with the existing `phonenumbers` dependency and
reject any non-French input rather than inserting a `+33` number into a code
whose official examples use national format.

Orange uses its customer area or voice portal for conditional settings because
its exact codes differ by offer. Bouygues and Other use device/account guidance
without a guessed code; Other has no invented provider source URL. Unreachable uses carrier/device settings unless a
line-type-specific official source is later versioned and tested.

- [ ] **Step 5: Include the guide only after number assignment**

`ActivationSnapshotService` calls the catalog only when `confirmed_carrier` and
an assigned Presvo number exist. It passes lookup `number_type` when available.
If lookup did not return a type, classify the existing French business number
locally with `phonenumbers.number_type` as `fixed`, `mobile`, or `unknown`; do
not infer the source line type from the assigned `+339` Presvo destination. The
returned source URLs are public provider help pages, not user-specific links.

- [ ] **Step 6: Run catalog and snapshot tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_forwarding_instruction_catalog.py \
  tests/activation/test_activation_snapshot_service.py -q
```

Expected: PASS and no guide contains an unconditional-forwarding action.

- [ ] **Step 7: Commit forwarding guidance**

```bash
git add apps/api/app/schemas/forwarding.py \
  apps/api/app/services/forwarding_instruction_catalog.py \
  apps/api/app/schemas/activation.py \
  apps/api/app/services/activation_snapshot_service.py \
  apps/api/tests/activation
git commit -m "feat: add carrier-aware forwarding guidance"
```

---

### Task 2: Own ten-minute verification windows and local simulation

**Files:**
- Create: `apps/api/app/services/forwarding_verification_service.py`
- Create: `apps/api/app/workers/jobs/verification_expiry.py`
- Create: `apps/api/tests/activation/test_forwarding_verification_service.py`
- Create: `apps/api/tests/workers/test_verification_expiry_job.py`
- Modify: `apps/api/app/routers/activation.py`
- Modify: `apps/api/app/routers/development.py`
- Modify: `apps/api/app/schemas/activation.py`
- Modify: `apps/api/app/services/activation_snapshot_service.py`
- Modify: `apps/api/app/workers/arq_worker.py`
- Modify: `apps/api/tests/activation/test_activation_api.py`
- Modify: `apps/api/tests/activation/test_development_api.py`

**Interfaces:**
- Produces: `ForwardingVerificationService.open_window`, `claim`, `complete`, and `expire`.
- Produces: `POST /api/activation/open-verification-window`.
- Produces only in development: `POST /api/development/simulate-forwarded-call`.

- [ ] **Step 1: Write failing state-machine tests**

```python
@pytest.mark.anyio
async def test_window_is_exactly_ten_minutes(db_session, provisioned_user) -> None:
    service = ForwardingVerificationService(db_session, now_provider=lambda: FIXED_NOW)
    activation = await service.open_window(provisioned_user.id)
    assert activation.verification_window_started_at == FIXED_NOW
    assert activation.verification_window_expires_at == FIXED_NOW + timedelta(minutes=10)
    assert activation.verification_status == "open"


@pytest.mark.anyio
async def test_complete_requires_claimed_current_routing_fingerprint(db_session, open_window) -> None:
    claimed = await service.claim(
        called_number=open_window.presvo_number,
        room_name="verification-room-1",
    )
    await service.complete(session_id=claimed.session_id)
    activation = await activation_repository.get_by_user_id(open_window.user_id)
    assert activation.verification_status == "succeeded"
    assert activation.forwarding_verified_at == FIXED_NOW
```

Add tests for opening before number readiness, duplicate open, expiry, claim at
the boundary, claim by wrong called number, duplicate claim, completion before
claim, completion with stale fingerprint, content-only profile change, routing
change, and duplicate completion. Add worker tests for expiring an unclaimed
window at ten minutes, preserving a claimed session through its two-minute
completion grace, expiring it after that grace, and duplicate cron execution.

- [ ] **Step 2: Run the tests and observe missing verification service**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_forwarding_verification_service.py -q
```

Expected: FAIL with missing service.

- [ ] **Step 3: Implement window opening under the user lock**

Require a current confirmed profile, provider-ready assigned number, succeeded
provisioning, and no current verification. Reset prior failed/expired session
fields, set server timestamps, append `verification_window_opened`, and return
the refreshed activation.

```python
started_at = self.now_provider()
activation.verification_window_started_at = started_at
activation.verification_window_expires_at = started_at + timedelta(minutes=10)
activation.verification_session_id = None
activation.verification_claimed_at = None
activation.verification_dispatch_id = None
activation.verification_routing_fingerprint = None
activation.verification_status = "open"
activation.last_failure_code = None
```

- [ ] **Step 4: Implement single-use claim and completion**

`claim` resolves the assigned phone by normalized called number, locks user then
activation, verifies `started_at <= now < expires_at`, creates a UUID session ID,
stores `verification_claimed_at`, `verification_status="claimed"`, and the
current routing fingerprint in `verification_routing_fingerprint`, then appends
a unique event.

`complete` accepts only the claimed session identity, locks the same rows, and
requires the stored claim fingerprint to equal the newly recomputed current
fingerprint. A call claimed before expiry may complete for up to two minutes
after expiry. It sets `verification_status="succeeded"`, copies the stored
claim fingerprint to `verified_routing_fingerprint`, and sets
`forwarding_verified_at`; it does not set go-live approval.

Implement `expire` as an idempotent locked transition that appends exactly one
`verification_window_expired` event. Add `verification_expiry_job` to the ARQ
worker's once-per-minute cron list. The job claims bounded expired rows with
`FOR UPDATE SKIP LOCKED`: open windows expire at their ten-minute deadline;
claimed sessions expire only after the two-minute completion grace. The pure
snapshot policy still treats a past deadline as closed immediately, so UI
correctness never depends on cron timing.

- [ ] **Step 5: Add the customer endpoint and deterministic simulator**

The customer endpoint opens a window and returns the canonical snapshot.
The development simulator calls the same `claim` method with a deterministic
local room, then calls `complete`; it never writes directly to model fields.

```python
@development_router.post("/api/development/simulate-forwarded-call")
async def simulate_forwarded_call(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: ForwardingVerificationService = Depends(get_verification_service),
) -> ActivationSnapshotResponse:
    claimed = await service.claim_for_user(identity.internal_user_id, room_name=f"local-verification-{uuid4()}")
    await service.complete(session_id=claimed.session_id)
    return await snapshot_service.get(identity.internal_user_id)
```

- [ ] **Step 6: Run service and API tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_forwarding_verification_service.py \
  tests/activation/test_activation_api.py \
  tests/activation/test_development_api.py \
  tests/workers/test_verification_expiry_job.py -q
```

Expected: PASS; local simulation ends at `ready_to_activate` and creates no
normal call data.

- [ ] **Step 7: Commit verification windows**

```bash
git add apps/api/app/services/forwarding_verification_service.py \
  apps/api/app/workers/jobs/verification_expiry.py apps/api/app/workers/arq_worker.py \
  apps/api/app/routers/activation.py apps/api/app/routers/development.py \
  apps/api/app/schemas/activation.py \
  apps/api/app/services/activation_snapshot_service.py \
  apps/api/tests/activation apps/api/tests/workers/test_verification_expiry_job.py
git commit -m "feat: add forwarding verification windows"
```

---

### Task 3: Route real verification calls through a separate durable dispatch

**Files:**
- Create: `apps/api/app/core/verification_token.py`
- Create: `apps/api/app/services/inbound_verification_service.py`
- Create: `apps/api/tests/livekit/test_forwarding_verification_dispatch.py`
- Modify: `apps/api/app/schemas/livekit.py`
- Modify: `apps/api/app/webhooks/livekit.py`
- Modify: `apps/api/app/services/outbox_service.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/workers/jobs/outbox_delivery.py`
- Modify: `apps/api/tests/livekit/test_dispatch_webhook.py`
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py`
- Modify: `apps/api/tests/services/test_outbox_service.py`

**Interfaces:**
- Produces: `VerificationDispatchMetadata` with distinct `job_type="forwarding_verification"`.
- Produces: outbox topic `livekit.verification_dispatch`.
- Produces: `POST /api/activation/verification/{session_id}/complete` authenticated by a verification-scoped JWT.

- [ ] **Step 1: Write failing webhook isolation tests**

```python
@pytest.mark.anyio
async def test_open_window_intercepts_sip_before_normal_call_creation(
    livekit_service, open_window_event
) -> None:
    result = await livekit_service.handle_participant_joined(open_window_event)
    assert result.status == "verification_claimed"
    assert await count_rows(Call) == 0
    assert await count_outbox("livekit.verification_dispatch") == 1
    assert await count_outbox("livekit.dispatch") == 0


@pytest.mark.anyio
async def test_no_window_continues_to_normal_readiness_path(livekit_service, normal_sip_event) -> None:
    result = await livekit_service.handle_participant_joined(normal_sip_event)
    assert result.status in {"denied", "pending", "idempotent"}
```

Add duplicate webhook, wrong called number, expired window, diversion mismatch
when present, no diversion attribute, and no-normal-data assertions.

- [ ] **Step 2: Run webhook/outbox tests and observe normal dispatch behavior**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/livekit/test_forwarding_verification_dispatch.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/workers/test_livekit_dispatch_outbox.py -q
```

Expected: FAIL because verification calls are not intercepted or dispatched.

- [ ] **Step 3: Add a distinct verification JWT audience**

Use the existing strong dispatch secret but never reuse call claims:

```python
def create_verification_token(*, session_id: str, user_id: str, ttl_seconds: int = 900) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "aud": "presvo-forwarding-verification",
            "sub": session_id,
            "user_id": user_id,
            "iat": now,
            "exp": now + timedelta(seconds=ttl_seconds),
        },
        require_dispatch_secret(),
        algorithm="HS256",
    )
```

`verify_verification_token` requires the exact audience, session ID, and user
ownership. Call-scoped tokens must fail this verifier and vice versa.

- [ ] **Step 4: Intercept verification before normal call admission**

In the SIP participant branch, after validating room and called number but
before creating a `Call`, ask `InboundVerificationService.claim_if_open`.
Pass optional redirect attributes such as `sip.diversion` if present. If it
returns no claim, continue the existing readiness and call path unchanged.

For a claim, append this outbox event in the same transaction:

```python
await outbox.add(
    topic="livekit.verification_dispatch",
    aggregate_type="forwarding-verification",
    aggregate_id=activation.id,
    idempotency_key=f"livekit.verification_dispatch:{session_id}",
    payload={
        "activation_id": str(activation.id),
        "session_id": session_id,
        "room_name": room_name,
    },
)
```

Do not create a `Call` row to carry the room identity.

- [ ] **Step 5: Add a dedicated outbox handler and metadata contract**

```python
class VerificationDispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_type: Literal["forwarding_verification"] = "forwarding_verification"
    verification_session_id: str
    user_id: str
    agent_identity: str
    completion_token: str
    message: Literal["Forwarding test successful. Return to Presvo to go live."]
    tts_provider: Literal["speechmatics", "elevenlabs"] = "speechmatics"
```

The handler locks the activation, validates the claimed session, creates or
reconciles exactly one LiveKit dispatch for the room and session ID, and persists
`verification_dispatch_id`. Register the topic in outbox validation, delivery
classification, and `DEFAULT_OUTBOX_HANDLERS`.

- [ ] **Step 6: Add the completion endpoint with scoped auth**

The agent endpoint requires `x-verification-token`, validates audience/session,
then calls `ForwardingVerificationService.complete`. Return
`{"status": "verified", "session_id": session_id}` for first or duplicate
success. It accepts no transcript, duration, caller number, or recording field.

- [ ] **Step 7: Run webhook, token, and outbox tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/livekit/test_forwarding_verification_dispatch.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/services/test_outbox_service.py -q
```

Expected: PASS; verification and normal dispatch topics remain disjoint.

- [ ] **Step 8: Commit isolated verification dispatch**

```bash
git add apps/api/app/core/verification_token.py \
  apps/api/app/services/inbound_verification_service.py \
  apps/api/app/schemas/livekit.py apps/api/app/webhooks/livekit.py \
  apps/api/app/services/outbox_service.py apps/api/app/workers/jobs \
  apps/api/tests/livekit apps/api/tests/workers/test_livekit_dispatch_outbox.py \
  apps/api/tests/services/test_outbox_service.py
git commit -m "feat: dispatch forwarding verification separately"
```

---

### Task 4: Add the TTS-only verification job to the LiveKit agent

**Files:**
- Create: `apps/agent/agent/verification_runtime.py`
- Create: `apps/agent/tests/test_verification_runtime.py`
- Modify: `apps/agent/agent/schemas.py`
- Modify: `apps/agent/agent/main.py`
- Modify: `apps/agent/agent/pipeline_factory.py`
- Modify: `apps/agent/agent/api_client.py`
- Modify: `apps/agent/tests/test_main.py`
- Modify: `apps/agent/tests/test_pipeline_factory.py`
- Modify: `apps/agent/tests/test_api_client.py`

**Interfaces:**
- Produces: discriminated union `JobMetadata = CustomerCallDispatchMetadata | ForwardingVerificationDispatchMetadata`.
- Produces: `run_forwarding_verification(context, metadata)`.
- Produces: `AgentApiClient.complete_verification(session_id, token)`.

- [ ] **Step 1: Write failing job-discrimination and no-LLM tests**

```python
def test_verification_metadata_rejects_customer_fields() -> None:
    payload = valid_verification_metadata() | {"system_prompt": "customer text"}
    with pytest.raises(ValidationError):
        parse_job_metadata(payload)


@pytest.mark.anyio
async def test_verification_runtime_plays_exact_message_without_stt_or_llm() -> None:
    session = FakeSession()
    await run_forwarding_verification(
        context=fake_context(session),
        metadata=verification_metadata(),
        session_factory=lambda: session,
        api_client=FakeApiClient(),
    )
    assert session.say_calls == [("Forwarding test successful. Return to Presvo to go live.", False)]
    assert session.stt is None
    assert session.llm is None
    assert session.record == {"audio": False, "transcript": False, "traces": False, "logs": False}
```

Add tests for exact agent identity, malformed metadata rejection without logging
tokens, completion retry, completion permanent rejection, session shutdown, and
no `SessionRuntime.finalize` call.

- [ ] **Step 2: Run agent tests and observe missing verification path**

```bash
cd apps/agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_verification_runtime.py tests/test_main.py tests/test_api_client.py -q
```

Expected: FAIL because all jobs currently require normal call metadata.

- [ ] **Step 3: Introduce a discriminated metadata union**

Rename the current model to `CustomerCallDispatchMetadata`, add
`job_type: Literal["customer_call"] = "customer_call"`, and add:

```python
class ForwardingVerificationDispatchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_type: Literal["forwarding_verification"]
    verification_session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    agent_identity: str = Field(min_length=1)
    completion_token: str = Field(min_length=1)
    message: Literal["Forwarding test successful. Return to Presvo to go live."]
    tts_provider: Literal["speechmatics", "elevenlabs"]


JobMetadata = Annotated[
    CustomerCallDispatchMetadata | ForwardingVerificationDispatchMetadata,
    Field(discriminator="job_type"),
]
JOB_METADATA_ADAPTER = TypeAdapter(JobMetadata)
```

Update existing call tests to include or accept the default customer job type.

- [ ] **Step 4: Build a TTS-only session factory**

Add `build_verification_session(tts_provider, plugin_modules=None,
session_cls=AgentSession)`. It loads only the chosen TTS plugin and returns
`AgentSession(tts=tts)`. It must never call `_build_stt`, `_build_llm`,
`build_system_prompt`, or STS model construction.

- [ ] **Step 5: Implement the fixed-message entrypoint branch**

`handle_job_request` validates the union and requires
`agent-verification-{session_id}` for verification jobs. `entrypoint` branches
immediately after parsing:

```python
metadata = parse_job_metadata(metadata_dict)
if isinstance(metadata, ForwardingVerificationDispatchMetadata):
    await run_forwarding_verification(context, metadata)
    return
await run_customer_call(context, metadata)
```

The verification runtime connects, waits for SIP, starts a no-record session,
disables caller audio, awaits `session.say(message, allow_interruptions=False)`,
calls the completion API, and drains/shuts down. It never constructs
`SessionRuntime` or registers transcript handlers.

- [ ] **Step 6: Add safe completion delivery**

```python
async def complete_verification(self, session_id: str, token: str) -> dict:
    response = await self._post_with_retries(
        f"{self.base_url}/api/activation/verification/{session_id}/complete",
        headers={"x-verification-token": token},
        json={},
    )
    payload = response.json()
    if payload != {"status": "verified", "session_id": session_id}:
        raise VerificationCompletionAcknowledgementError
    return payload
```

Use the existing bounded retry delays and safe status classification. Never log
the completion token.

- [ ] **Step 7: Run all deterministic agent checks**

```bash
cd apps/agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q -k 'not test_receptionist_behavior'
```

Expected: Ruff and mypy exit 0; deterministic agent tests PASS; credentialed
behavior evaluations remain deselected.

- [ ] **Step 8: Commit the fixed-message agent job**

```bash
git add apps/agent/agent/verification_runtime.py apps/agent/agent/schemas.py \
  apps/agent/agent/main.py apps/agent/agent/pipeline_factory.py \
  apps/agent/agent/api_client.py apps/agent/tests
git commit -m "feat: run fixed forwarding verification calls"
```

---

### Task 5: Require explicit go-live and record real routing activation

**Files:**
- Create: `apps/api/app/services/activation_go_live_service.py`
- Create: `apps/api/tests/activation/test_activation_go_live_service.py`
- Modify: `apps/api/app/routers/activation.py`
- Modify: `apps/api/app/services/agent_config_service.py`
- Modify: `apps/api/app/services/activation_policy.py`
- Modify: `apps/api/app/services/activation_snapshot_service.py`
- Modify: `apps/api/app/services/customer_readiness_policy.py`
- Modify: `apps/api/app/services/customer_readiness_service.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/tests/agent/test_agent_config_api.py`
- Modify: `apps/api/tests/workers/test_phone_routing_readiness.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`

**Interfaces:**
- Produces: `ActivationGoLiveService.go_live(user_id, arq_pool)`.
- Produces: `POST /api/activation/go-live`.
- Updates: successful `phone.enable` projection sets `CustomerActivation.activated_at`.

- [ ] **Step 1: Write failing go-live prerequisite and activation tests**

```python
@pytest.mark.anyio
async def test_go_live_requires_current_forwarding_verification(db_session, ready_except_verification) -> None:
    with pytest.raises(ActivationGoLiveBlockedError) as error:
        await ActivationGoLiveService(db_session).go_live(ready_except_verification.id, arq_pool=None)
    assert error.value.blockers == ("forwarding_not_verified",)


@pytest.mark.anyio
async def test_go_live_is_activating_until_provider_projection_succeeds(db_session, ready_user) -> None:
    snapshot = await ActivationGoLiveService(db_session).go_live(ready_user.id, arq_pool=None)
    assert snapshot.stage == "activating"
    assert await count_outbox("phone.enable") == 1
    await deliver_phone_enable()
    snapshot = await ActivationSnapshotService(db_session).get(ready_user.id)
    assert snapshot.stage == "active"
```

Add blocker cases for profile/projection, subscription/period/balance, number,
provider ID, verification fingerprint, and already active idempotency. Add a
test that direct `PATCH /api/agent/config {"is_enabled": true}` is rejected
while activation flow is enabled.

- [ ] **Step 2: Run focused tests and observe the absent go-live command**

```bash
cd apps/api
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_activation_go_live_service.py \
  tests/agent/test_agent_config_api.py \
  tests/workers/test_phone_routing_readiness.py -q
```

Expected: FAIL because activation approval is not yet wired.

- [ ] **Step 3: Implement go-live under the dispatch ordering lock**

Lock user first, then activation, phone, subscription, and agent config in the
same order used by dispatch. Recompute the current routing fingerprint and
central readiness with an enabled config override. Ignore only projection
blockers that the pending `phone.enable` effect will resolve.

```python
activation.go_live_requested_at = now
activation.go_live_approved_at = now
activation.last_failure_code = None
agent_config.is_enabled = True
await outbox.add(
    topic="phone.enable",
    aggregate_type="user",
    aggregate_id=user_id,
    idempotency_key=f"activation:go-live:{activation.id}:{profile.routing_revision}",
    payload={"user_id": str(user_id)},
)
```

Append `go_live_requested` with no customer free text. Commit, wake outbox, and
return an `activating` snapshot.

- [ ] **Step 4: Record successful provider activation and safe terminal failure**

After `deliver_phone_routing` persists `app-active`, lock the activation and
re-evaluate readiness. If `can_route`, set `activated_at` once and append
`go_live_succeeded`. For terminal provider failure, keep the phone disabled, set
the agent config disabled, clear `go_live_requested_at`,
`go_live_approved_at`, and `activated_at`, set
`last_failure_code="routing_provider_terminal"`, and append `go_live_failed`.
The snapshot returns to `ready_to_activate` with a safe retry action, so another
explicit **Go live** command is required. Retryable delivery remains
`activating` until outbox retry is exhausted; exhaustion performs the same safe
failure transition.

- [ ] **Step 5: Enforce the central policy at every normal dispatch seam**

With `ACTIVATION_FLOW_ENABLED=true`, ensure webhook admission,
`livekit.dispatch` outbox delivery, `phone.enable`, and agent config enablement
all load profile/activation prerequisites through the same readiness snapshot.
No caller-specific seam may reconstruct activation rules independently.

- [ ] **Step 6: Add the customer endpoint and direct-toggle restriction**

`POST /api/activation/go-live` returns `202` with the snapshot. Map blockers to
`409 {"code": "go_live_blocked", "blockers": [...]}`. When activation flow is
enabled, customer PATCH may update non-projected legacy fields only while
routing is disabled; it may not set `is_enabled=true`.

- [ ] **Step 7: Run go-live, routing, and normal dispatch regression tests**

```bash
cd apps/api
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_activation_go_live_service.py \
  tests/agent/test_agent_config_api.py \
  tests/workers/test_phone_routing_readiness.py \
  tests/livekit/test_dispatch_service.py \
  tests/livekit/test_durable_dispatch_service.py -q
```

Expected: PASS; every normal dispatch is denied before verification/go-live and
accepted only after provider projection succeeds.

- [ ] **Step 8: Commit explicit go-live enforcement**

```bash
git add apps/api/app/services/activation_go_live_service.py \
  apps/api/app/routers/activation.py apps/api/app/services/agent_config_service.py \
  apps/api/app/services/activation_policy.py \
  apps/api/app/services/activation_snapshot_service.py \
  apps/api/app/services/customer_readiness_policy.py \
  apps/api/app/services/customer_readiness_service.py \
  apps/api/app/workers/jobs/outbox_topics.py apps/api/tests
git commit -m "feat: require verified explicit go-live"
```

---

### Task 6: Prove verification privacy and runtime isolation

**Files:**
- Create: `apps/api/tests/integration/test_forwarding_verification_privacy.py`
- Modify only if verification finds a defect in Plan 3-owned files.

**Interfaces:**
- Verifies: no normal call data, no usage, no recording, token separation, and complete readiness enforcement.

- [ ] **Step 1: Add an integration proof for the entire verification lifecycle**

```python
@pytest.mark.anyio
async def test_verification_lifecycle_creates_no_customer_call_data(verification_harness) -> None:
    before = await privacy_counts()
    session = await verification_harness.claim_realistic_sip_event()
    await verification_harness.deliver_dispatch(session)
    await verification_harness.complete_from_agent(session)
    after = await privacy_counts()

    assert after.calls == before.calls
    assert after.messages == before.messages
    assert after.usage_ledgers == before.usage_ledgers
    assert after.notifications == before.notifications
    assert after.summary_events == before.summary_events
    assert after.recording_events == before.recording_events
    assert after.activation_events == before.activation_events + 3
```

Also assert that no log contains the verification token, caller number, Presvo
number, or customer profile content.

- [ ] **Step 2: Run complete API and agent deterministic verification**

```bash
cd apps/api
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync ruff check app tests
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync mypy app
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q

cd ../agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q -k 'not test_receptionist_behavior'
```

Expected: all deterministic checks PASS; no activation/verification test skips.

- [ ] **Step 3: Verify forbidden data paths by source scan**

```bash
rg -n 'summary\.generate|recording\.stop|UsageLedger|Call\(' \
  apps/api/app/services/forwarding_verification_service.py \
  apps/api/app/services/inbound_verification_service.py
rg -n 'build_system_prompt|_build_llm|_build_stt|SessionRuntime' \
  apps/agent/agent/verification_runtime.py
```

Expected: both commands return no matches.

- [ ] **Step 4: Commit the privacy proof**

```bash
git add apps/api/tests/integration/test_forwarding_verification_privacy.py apps/api apps/agent
git commit -m "test: prove forwarding verification isolation"
```
