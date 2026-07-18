# Consent-First Provisioning and Local Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple payment from number purchase, add carrier lookup and explicit idempotent provisioning consent, and make the profile-to-number journey runnable locally with guarded identity, billing, carrier, and telephony substitutes.

**Architecture:** Stripe continues to own paid subscription truth but no longer initiates number purchase. Activation commands own consent and enqueue the existing durable `phone.provision` effect; provider factories select Telnyx or deterministic local adapters. A development-only identity and billing surface uses the same domain services as real users and is impossible to register in production.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, ARQ/Redis, transactional outbox, Stripe SDK 15, Telnyx SDK 2, pytest 9

## Dependencies

- Complete `2026-07-17-activation-domain-and-profile-api.md` first.
- Required interfaces: `BusinessProfileService`, `ActivationSnapshotService`, `CustomerActivationRepository`, `ActivationEventRepository`, and activation models from revision 0012.

## Global Constraints

- Profile confirmation and eligible starter payment must precede provisioning consent.
- Payment, subscription creation, and `invoice.paid` must never enqueue `phone.provision`.
- Only `POST /api/activation/confirm-provisioning` may record initial number-order consent.
- Repeated requests, jobs, and provider reconciliation must produce at most one Presvo number.
- Number country is always `FR`; there is no fallback to `US` or Ireland.
- Carrier lookup failure is non-blocking because the owner can select Orange, SFR, Bouygues Telecom, Free, or Other manually.
- Local provider modes perform no external I/O and are absent from production routing.
- Production startup rejects local identity, fake billing, fake carrier lookup, fake telephony, or disabled Telnyx ordering.
- Provider payloads and complete phone numbers do not enter logs, activation events, or error responses.
- No cloud deployment or provider credential use is part of this plan.
- Follow TDD and create one focused commit per task.

---

### Task 1: Remove automatic provisioning from Stripe lifecycle handling

**Files:**
- Modify: `apps/api/app/services/billing_service.py`
- Modify: `apps/api/tests/billing/test_stripe_webhooks.py`
- Modify: `apps/api/tests/billing/test_billing_lock_order.py`
- Modify: `apps/api/tests/integration/test_subscription_disable_intent.py`

**Interfaces:**
- Preserves: Stripe subscription state, minute grants, and safe routing reconciliation for an already activated account.
- Removes: `phone.provision` emission from every Stripe event path.

- [ ] **Step 1: Replace automatic-provisioning expectations with consent-first assertions**

```python
@pytest.mark.anyio
async def test_first_paid_invoice_grants_minutes_without_ordering_number(
    billing_service,
    stripe_invoice_paid_payload,
    outbox_repository,
) -> None:
    await billing_service.handle_event(stripe_invoice_paid_payload)

    topics = [event.topic for event in await outbox_repository.list_all()]
    assert "phone.provision" not in topics
    assert await current_balance() == 60


@pytest.mark.anyio
async def test_no_stripe_event_type_can_emit_phone_provision(all_stripe_events) -> None:
    for event in all_stripe_events:
        await handle_isolated(event)
    assert all(outbox.topic != "phone.provision" for outbox in await all_outbox_events())
```

Retain tests proving payment failure disables an already-live number and a paid
renewal may request routing reconciliation for an existing number.

- [ ] **Step 2: Run Stripe tests and observe the old automatic side effect**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/billing/test_stripe_webhooks.py \
  tests/billing/test_billing_lock_order.py \
  tests/integration/test_subscription_disable_intent.py -q
```

Expected: at least the first-activation test FAILS because `invoice.paid`
currently emits `phone.provision`.

- [ ] **Step 3: Remove only the number-purchase branch from `_handle_invoice_paid`**

Replace the first-activation branch with behavior that never provisions:

```python
phone_number = await self.phone_number_repository.get_by_user_id(subscription.user_id)
if phone_number is not None:
    await self._add_phone_intent(
        topic="phone.enable",
        user_id=subscription.user_id,
        idempotency_key=f"stripe:invoice:{invoice_id}:phone.enable",
    )
```

The outbox routing handler still re-evaluates central readiness, so an existing
number cannot enable before later forwarding verification and go-live approval.
Do not delete minute-grant or stale-event logic.

- [ ] **Step 4: Run billing and routing regression tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/billing tests/integration/test_subscription_disable_intent.py \
  tests/workers/test_phone_routing_readiness.py -q
```

Expected: PASS with no Stripe-generated `phone.provision` event.

- [ ] **Step 5: Commit the consent boundary**

```bash
git add apps/api/app/services/billing_service.py apps/api/tests/billing \
  apps/api/tests/integration/test_subscription_disable_intent.py \
  apps/api/tests/workers/test_phone_routing_readiness.py
git commit -m "fix: require consent before number provisioning"
```

---

### Task 2: Add provider-neutral carrier lookup with manual fallback

**Files:**
- Create: `apps/api/app/providers/carrier_lookup/__init__.py`
- Create: `apps/api/app/providers/carrier_lookup/base.py`
- Create: `apps/api/app/providers/carrier_lookup/telnyx.py`
- Create: `apps/api/app/providers/carrier_lookup/fake.py`
- Create: `apps/api/app/providers/carrier_lookup/factory.py`
- Create: `apps/api/app/services/carrier_lookup_service.py`
- Create: `apps/api/tests/providers/test_carrier_lookup_providers.py`
- Create: `apps/api/tests/activation/test_carrier_lookup_service.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/routers/activation.py`
- Modify: `apps/api/app/schemas/activation.py`
- Modify: `apps/api/app/services/business_profile_service.py`
- Modify: `apps/api/tests/activation/test_activation_api.py`

**Interfaces:**
- Produces: `CarrierLookupProvider.lookup(e164) -> CarrierLookupResult`.
- Produces: `CarrierLookupService.lookup_for_user(user_id) -> CarrierLookupResult`.
- Produces: `POST /api/activation/lookup-carrier`.

- [ ] **Step 1: Write failing provider-contract and API tests**

```python
@pytest.mark.anyio
async def test_lookup_normalizes_orange_brand() -> None:
    provider = FakeRawLookupProvider(carrier_name="Orange France")
    result = await CarrierLookupService(provider=provider).lookup_number("+33612345678")
    assert result.normalized_carrier == "orange"
    assert result.country_code == "FR"


@pytest.mark.anyio
async def test_lookup_failure_records_manual_fallback_without_raw_error(
    async_client, synced_user_token, saved_profile
) -> None:
    response = await async_client.post(
        "/api/activation/lookup-carrier",
        headers={"Authorization": f"Bearer {synced_user_token}"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "carrier_lookup_unavailable", "manual_selection_allowed": True}
    assert "provider" not in response.text.lower()
```

Add cases for SFR, Bouygues Telecom, Free Mobile, unknown carrier → `other`,
non-French country rejection, timeout, authentication error, rate limit, and
lookup of a number that changed before persistence.

- [ ] **Step 2: Run the tests and observe missing provider modules**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/providers/test_carrier_lookup_providers.py \
  tests/activation/test_carrier_lookup_service.py \
  tests/activation/test_activation_api.py -q
```

Expected: FAIL with missing carrier lookup modules and endpoint.

- [ ] **Step 3: Define the provider-neutral contract and safe errors**

```python
@dataclass(frozen=True, slots=True)
class CarrierLookupResult:
    normalized_number: str
    country_code: str
    carrier_name: str | None
    normalized_carrier: Literal["orange", "sfr", "bouygues", "free", "other"]
    number_type: str | None
    looked_up_at: datetime


class CarrierLookupError(RuntimeError):
    def __init__(self, code: Literal["retryable", "terminal"]) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = code == "retryable"


class CarrierLookupProvider(Protocol):
    async def lookup(self, e164: str) -> CarrierLookupResult:
        raise NotImplementedError
```

Implement brand normalization in one pure function using case-folded tokens.
Do not expose or persist the raw Telnyx object.

- [ ] **Step 4: Implement the Telnyx and deterministic fake adapters**

Presvo is locked to `telnyx==2.1.6`, which exposes resource classes rather than
the newer `Telnyx(...).number_lookup` client surface. Its `APIResource.retrieve`
signature is `retrieve(id, api_key=None, **params)`, so the adapter must call
`telnyx.NumberLookup.retrieve(e164, api_key=api_key)` in `asyncio.to_thread`,
preferably through an injected resource for deterministic tests. Map
`telnyx.error.APIConnectionError`, `TimeoutError`, `RateLimitError`,
and `ServiceUnavailableError` to retryable; map authentication, permission,
invalid-request, invalid-parameters, and resource-not-found errors to terminal.
Do not mutate the module-global Telnyx API key. Return only the six contract
fields. If the pinned SDK is upgraded later, update this adapter and plan
together rather than mixing both SDK generations.

```python
class FakeCarrierLookupProvider:
    def __init__(self, carrier: CarrierCode = "orange") -> None:
        self.carrier = carrier

    async def lookup(self, e164: str) -> CarrierLookupResult:
        normalized = normalize_french_number(e164)
        return CarrierLookupResult(
            normalized_number=normalized,
            country_code="FR",
            carrier_name=self.carrier.title(),
            normalized_carrier=self.carrier,
            number_type="mobile" if normalized.startswith(("+336", "+337")) else "fixed",
            looked_up_at=datetime.now(UTC),
        )
```

Add `carrier_lookup_mode: Literal["fake", "telnyx"] = "fake"` to `Settings` and
select the adapter only in `factory.py`.

- [ ] **Step 5: Persist detection without silently confirming it**

`CarrierLookupService.lookup_for_user` must lock the user, read the saved number,
release the transaction before provider I/O, then lock and re-read the profile.
Persist only if the number is unchanged. Set `detected_carrier` and
`detected_number_type`, `carrier_looked_up_at`, and
`carrier_lookup_status="succeeded"`; leave `confirmed_carrier` untouched.
On safe failure, set `carrier_lookup_status="failed"` and allow manual selection
through the normal profile `PUT`.

- [ ] **Step 6: Add the thin endpoint and safe error response**

```python
@router.post("/api/activation/lookup-carrier", response_model=CarrierLookupResponse)
async def lookup_carrier(
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CarrierLookupService = Depends(get_carrier_lookup_service),
) -> CarrierLookupResponse:
    try:
        return CarrierLookupResponse.model_validate(
            await service.lookup_for_user(identity.internal_user_id)
        )
    except CarrierLookupUnavailableError:
        raise HTTPException(
            status_code=503,
            detail={"code": "carrier_lookup_unavailable", "manual_selection_allowed": True},
        ) from None
```

- [ ] **Step 7: Run focused provider and API tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/providers/test_carrier_lookup_providers.py \
  tests/activation/test_carrier_lookup_service.py \
  tests/activation/test_activation_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit carrier discovery**

```bash
git add apps/api/app/providers/carrier_lookup apps/api/app/services/carrier_lookup_service.py \
  apps/api/app/core/config.py apps/api/app/routers/activation.py \
  apps/api/app/schemas/activation.py apps/api/app/services/business_profile_service.py \
  apps/api/tests/providers/test_carrier_lookup_providers.py \
  apps/api/tests/activation
git commit -m "feat: detect and confirm french carriers"
```

---

### Task 3: Implement explicit idempotent provisioning consent and retry

**Files:**
- Create: `apps/api/app/services/activation_provisioning_service.py`
- Create: `apps/api/tests/activation/test_activation_provisioning_service.py`
- Modify: `apps/api/app/repositories/phone_number_provisioning_repository.py`
- Modify: `apps/api/app/repositories/phone_number_repository.py`
- Modify: `apps/api/app/routers/activation.py`
- Modify: `apps/api/app/services/onboarding_service.py`
- Modify: `apps/api/tests/activation/test_activation_api.py`
- Modify: `apps/api/tests/onboarding/test_onboarding_api.py`
- Modify: `apps/api/tests/workers/test_individual_jobs.py`

**Interfaces:**
- Produces: `ActivationProvisioningService.confirm(user_id, arq_pool)` and `retry(user_id, arq_pool)`.
- Produces: `POST /api/activation/confirm-provisioning` and `POST /api/activation/retry-provisioning`.
- Consumes: existing `phone.provision` outbox topic and worker.

- [ ] **Step 1: Write failing consent, duplicate, and race tests**

```python
@pytest.mark.anyio
async def test_confirm_provisioning_records_one_consent_and_one_outbox(
    db_session, paid_confirmed_user
) -> None:
    service = ActivationProvisioningService(db_session)
    first = await service.confirm(paid_confirmed_user.id, arq_pool=None)
    second = await service.confirm(paid_confirmed_user.id, arq_pool=None)

    assert first.provisioning_id == second.provisioning_id
    assert await count_outbox(topic="phone.provision") == 1
    assert await count_activation_events("provisioning_consented") == 1
```

Add rejection tests for incomplete/unconfirmed profile, inactive subscription,
expired period, no minutes, unsupported plan, an existing number, a non-FR user,
and failed state without retry eligibility. Add a PostgreSQL concurrency test in
which two sessions confirm simultaneously and only one outbox identity exists.

- [ ] **Step 2: Run focused tests and observe missing command service**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_activation_provisioning_service.py \
  tests/activation/test_activation_api.py -q
```

Expected: FAIL because explicit provisioning commands do not exist.

- [ ] **Step 3: Add a queued state operation to the provisioning repository**

```python
async def queue_initial(
    self,
    *,
    user_id: UUID,
    operation_key: str,
) -> PhoneNumberProvisioning:
    current = await self.get_by_user_id_for_update(user_id)
    if current is None:
        current = PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status="queued",
            attempt_count=0,
            can_retry=False,
            provider_operation_key=operation_key,
        )
        self.session.add(current)
    elif current.provider_operation_key != operation_key:
        raise ProvisioningStateConflictError
    await self.session.flush()
    return current
```

Add `queue_retry` that requires `failed` plus `can_retry`, retains the provider
operation key, resets only safe error fields, and does not increment
`attempt_count`; `mark_running` owns attempt increments.

- [ ] **Step 4: Implement the consent command under the user lock**

The command order is user → activation → profile → subscription → provisioning
→ phone. Require current profile confirmation and eligible paid access. Use the
stable key `activation:phone.provision:{activation.id}` for the first attempt.

```python
operation_key = activation.provisioning_idempotency_key
if operation_key is None:
    operation_key = f"activation:phone.provision:{activation.id}"
    activation.provisioning_idempotency_key = operation_key
    activation.provisioning_consented_at = now
provisioning = await self.provisioning_repository.queue_initial(
    user_id=user_id,
    operation_key=operation_key,
)
await self.outbox_service.add(
    topic="phone.provision",
    aggregate_type="user",
    aggregate_id=user_id,
    idempotency_key=operation_key,
    payload={"user_id": str(user_id)},
)
await self.activation_events.append(
    user_id=user_id,
    activation_id=activation.id,
    event_type="provisioning_consented",
    idempotency_key=f"activation-event:{operation_key}",
    metadata={"country_code": "FR"},
)
```

Commit once, then best-effort wake `outbox_delivery_job`. Return the refreshed
activation snapshot even if Redis wakeup fails.

- [ ] **Step 5: Implement retry with a new outbox delivery identity**

Keep the provider operation key stable for provider reconciliation, but create a
new outbox key per attempt:

```python
next_attempt = provisioning.attempt_count + 1
outbox_key = f"activation:phone.provision:{activation.id}:attempt:{next_attempt}"
```

The worker receives the stable provider operation key from the provisioning
record, not the retry outbox key. Update `deliver_phone_provision` and
`phone_provisioning_job` signatures accordingly so a retry cannot purchase a
second number after an ambiguous timeout.

- [ ] **Step 6: Add endpoints and retire the old retry mutation**

Add the two activation endpoints and make `/api/onboarding/retry-provisioning`
delegate to `ActivationProvisioningService.retry` during compatibility. Return
`409` with a stable blocker code for invalid state and `202` plus the canonical
snapshot for accepted work.

- [ ] **Step 7: Run consent, worker, outbox, and billing tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/activation/test_activation_provisioning_service.py \
  tests/activation/test_activation_api.py tests/onboarding \
  tests/workers/test_individual_jobs.py \
  tests/workers/test_post_call_outbox_handlers.py \
  tests/billing/test_stripe_webhooks.py -q
```

Expected: PASS and no test observes more than one provider operation identity.

- [ ] **Step 8: Commit explicit provisioning commands**

```bash
git add apps/api/app/services/activation_provisioning_service.py \
  apps/api/app/repositories/phone_number_provisioning_repository.py \
  apps/api/app/repositories/phone_number_repository.py \
  apps/api/app/routers/activation.py apps/api/app/services/onboarding_service.py \
  apps/api/app/workers apps/api/tests/activation apps/api/tests/onboarding \
  apps/api/tests/workers apps/api/tests/billing/test_stripe_webhooks.py
git commit -m "feat: provision numbers after explicit consent"
```

---

### Task 4: Add deterministic local telephony and billing

**Files:**
- Create: `apps/api/app/providers/telephony/fake.py`
- Create: `apps/api/app/providers/telephony/factory.py`
- Create: `apps/api/app/services/local_billing_service.py`
- Create: `apps/api/app/routers/development.py`
- Create: `apps/api/tests/providers/test_fake_telephony_provider.py`
- Create: `apps/api/tests/activation/test_local_billing_service.py`
- Create: `apps/api/tests/activation/test_development_api.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/services/telephony_service.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/workers/jobs/phone_provisioning.py`
- Modify: `apps/api/tests/workers/test_phone_routing_readiness.py`

**Interfaces:**
- Produces: `create_telephony_provider(settings)` and `FakeTelephonyProvider`.
- Produces: `LocalBillingService.activate_starter(user_id, now)`.
- Produces only in development: `POST /api/development/activate-starter`.

- [ ] **Step 1: Write failing tests proving local adapters have no network dependency**

```python
@pytest.mark.anyio
async def test_fake_telephony_is_deterministic_per_operation_key() -> None:
    provider = FakeTelephonyProvider()
    first = await provider.provision_number(country_code="FR", operation_key="activation-1")
    second = await provider.provision_number(country_code="FR", operation_key="activation-1")
    assert first == second
    assert first["e164"].startswith("+339")
    assert first["provider_connection_name"] == "app-disabled"


@pytest.mark.anyio
async def test_local_billing_activates_starter_and_grants_once(db_session, active_user) -> None:
    service = LocalBillingService(db_session)
    await service.activate_starter(active_user.id, now=FIXED_NOW)
    await service.activate_starter(active_user.id, now=FIXED_NOW)
    assert await current_balance(db_session, active_user.id) == 60
```

- [ ] **Step 2: Run the tests and observe missing local adapters**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/providers/test_fake_telephony_provider.py \
  tests/activation/test_local_billing_service.py -q
```

Expected: FAIL with missing classes.

- [ ] **Step 3: Implement deterministic fake telephony**

```python
class FakeTelephonyProvider(TelephonyProvider):
    async def provision_number(self, *, country_code: str, operation_key: str | None = None) -> dict:
        if country_code != "FR" or not operation_key:
            raise TelephonyProviderError("provider_terminal", error_class="validation")
        digest = hashlib.sha256(operation_key.encode("utf-8")).hexdigest()
        digits = str(int(digest[:12], 16)).zfill(10)[-8:]
        return {
            "e164": f"+339{digits}",
            "provider_number_id": f"fake-{digest[:16]}",
            "provider_connection_name": "app-disabled",
        }

    async def enable_number(self, *, provider_number_id: str) -> str:
        self._require_fake_id(provider_number_id)
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        self._require_fake_id(provider_number_id)
        return "app-disabled"
```

Add `telephony_mode: Literal["fake", "telnyx"] = "fake"`. Make every default
provider construction in `TelephonyService`, provisioning worker, and routing
outbox use `create_telephony_provider(get_settings())`; injected test providers
still take precedence.

- [ ] **Step 4: Implement idempotent local starter activation**

Use a synthetic Stripe identity prefixed `local_`, a 30-day period starting at
the first activation time, and the existing subscription/usage services. Lock
the user as the serialization boundary. If the user's existing subscription is
already the local starter subscription, preserve its original period instead of
sliding it forward. Never overwrite a real Stripe-backed subscription through
the development endpoint. Use the stable usage source
`local-starter:{user_id}` so repeated activation on a later day still cannot
grant twice. Tests must call the command with two different `now` values and
assert one grant plus an unchanged period.

```python
subscription = await self.subscription_repository.upsert_by_stripe_subscription_id(
    user_id=user_id,
    stripe_customer_id=f"local_customer_{user_id}",
    stripe_subscription_id=f"local_subscription_{user_id}",
    plan_tier="starter",
    status="active",
    allocated_minutes=60,
    current_period_start=now,
    current_period_end=now + timedelta(days=30),
    stripe_subscription_created_at=now,
    last_stripe_event_created_at=now,
)
await self.usage_accounting_service.grant_invoice(
    user_id=user_id,
    invoice_id=f"local-starter:{user_id}",
    minutes=60,
)
```

Add `billing_mode: Literal["fake", "stripe"] = "fake"`.

- [ ] **Step 5: Register the local billing endpoint only in development**

`create_app` includes `development_router` only when
`settings.app_env == "development"`. The endpoint additionally requires
`billing_mode == "fake"`, authenticated ownership, and returns the refreshed
activation snapshot. It returns 404 when the router is absent, not a hidden 403.

- [ ] **Step 6: Run local adapter and provisioning worker tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/providers/test_fake_telephony_provider.py \
  tests/activation/test_local_billing_service.py \
  tests/activation/test_development_api.py \
  tests/workers/test_individual_jobs.py \
  tests/workers/test_phone_routing_readiness.py -q
```

Expected: PASS without Telnyx or Stripe credentials.

- [ ] **Step 7: Commit deterministic local providers**

```bash
git add apps/api/app/providers/telephony apps/api/app/services/local_billing_service.py \
  apps/api/app/routers/development.py apps/api/app/core/config.py apps/api/app/main.py \
  apps/api/app/services/telephony_service.py apps/api/app/workers \
  apps/api/tests/providers/test_fake_telephony_provider.py \
  apps/api/tests/activation/test_local_billing_service.py \
  apps/api/tests/activation/test_development_api.py apps/api/tests/workers
git commit -m "feat: add local billing and telephony adapters"
```

---

### Task 5: Add guarded local identity and production fail-closed validation

**Files:**
- Create: `apps/api/app/services/user_bootstrap_service.py`
- Create: `apps/api/tests/auth/test_local_auth.py`
- Modify: `apps/api/app/core/auth.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/core/runtime_validation.py`
- Modify: `apps/api/app/services/auth_service.py`
- Modify: `apps/api/tests/auth/test_clerk_sync.py`
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/api/.env.example`
- Modify: `compose.dev.yaml`

**Interfaces:**
- Produces: `UserBootstrapService.ensure_user(external_user_id, email) -> User`.
- Produces: `LocalAuthProvider.verify_token(token) -> UserIdentity`.
- Updates: `get_auth_provider` to select Clerk or local mode from server settings.

- [ ] **Step 1: Write failing local-auth and production-rejection tests**

```python
def test_local_auth_accepts_only_exact_configured_token() -> None:
    provider = LocalAuthProvider(token="presvo-local-development-token")
    assert provider.verify_token("presvo-local-development-token").clerk_user_id == "local_presvo_user"
    with pytest.raises(HTTPException) as error:
        provider.verify_token("wrong")
    assert error.value.status_code == 401


@pytest.mark.parametrize("field", ["auth_mode", "billing_mode", "carrier_lookup_mode", "telephony_mode"])
def test_production_rejects_local_or_fake_mode(base_settings, field) -> None:
    value = "local" if field == "auth_mode" else "fake"
    with pytest.raises(RuntimeError, match=field.upper()):
        validate_api_runtime(base_settings.model_copy(update={field: value}))
```

Add a request test that the local user is bootstrapped with `User`,
`AgentConfig`, `BusinessProfile`, and `CustomerActivation`, and a test that Clerk
mode rejects the local token.

- [ ] **Step 2: Run auth and startup tests and observe missing local mode**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/auth/test_local_auth.py tests/auth/test_clerk_sync.py \
  tests/test_deployment_readiness.py -q
```

Expected: FAIL because local auth fields/provider do not exist.

- [ ] **Step 3: Extract idempotent user bootstrap from Clerk sync**

```python
class UserBootstrapService:
    async def ensure_user(self, *, external_user_id: str, email: str) -> User:
        user = await self.user_repository.get_by_clerk_user_id(external_user_id)
        if user is None:
            user = await self.user_repository.create(external_user_id, email)
        await self.agent_config_repository.get_or_create_default(user.id)
        await self.profile_repository.get_or_create_for_update(user.id)
        await self.activation_repository.get_or_create_for_update(user.id)
        await self.session.flush()
        return user
```

Make `AuthService.sync_clerk_user` delegate to this service. Keep webhook-event
idempotency and its single final commit.

- [ ] **Step 4: Implement the server-selected local auth provider**

Add:

```python
auth_mode: Literal["clerk", "local"] = "clerk"
local_auth_token: str = "presvo-local-development-token"
```

`LocalAuthProvider` accepts only `hmac.compare_digest(token,
configured_token)`, always yields external ID `local_presvo_user`, and never
accepts a caller-selected user. In `require_user_identity`, local mode may call
`UserBootstrapService.ensure_user` with `local@presvo.invalid`; Clerk mode keeps
the existing synced-user requirement.

- [ ] **Step 5: Fail closed outside development and document local defaults**

`validate_api_runtime` must reject `auth_mode=local` in every non-development
environment and reject all fake modes in production. Production requires the
exact mapping `auth_mode=clerk`, `billing_mode=stripe`,
`carrier_lookup_mode=telnyx`, and `telephony_mode=telnyx`.

Add these safe defaults to the API service in `compose.dev.yaml`:

```yaml
AUTH_MODE: local
BILLING_MODE: fake
CARRIER_LOOKUP_MODE: fake
TELEPHONY_MODE: fake
LOCAL_AUTH_TOKEN: presvo-local-development-token
```

The worker receives only `TELEPHONY_MODE=fake`, which it needs for durable
provisioning/routing jobs. Do not give the worker the local identity token,
billing mode, or carrier lookup mode.

Document the same variables in `.env.example` with comments that production
rejects them. Do not add the local token to browser/public environment.

- [ ] **Step 6: Run auth, runtime, and cross-tenant regression tests**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/auth tests/test_deployment_readiness.py tests/auth/test_cross_tenant.py -q
```

Expected: PASS; production rejects all fake/local modes; Clerk cross-tenant
tests remain unchanged.

- [ ] **Step 7: Commit local identity and guards**

```bash
git add apps/api/app/services/user_bootstrap_service.py apps/api/app/core/auth.py \
  apps/api/app/core/config.py apps/api/app/core/runtime_validation.py \
  apps/api/app/services/auth_service.py apps/api/tests/auth \
  apps/api/tests/test_deployment_readiness.py apps/api/.env.example compose.dev.yaml
git commit -m "feat: guard provider-free local activation"
```

---

### Task 6: Verify Plan 2 end to end at API level

**Files:**
- Create: `apps/api/tests/integration/test_local_activation_to_number.py`
- Modify only if verification finds a defect in Plan 2-owned files.

**Interfaces:**
- Verifies: local identity → profile → fake payment → explicit consent → fake French number.

- [ ] **Step 1: Write the local integration journey**

```python
@pytest.mark.anyio
async def test_provider_free_journey_reaches_forwarding_required(local_client) -> None:
    headers = {"Authorization": "Bearer presvo-local-development-token"}
    assert (await local_client.put("/api/business-profile", headers=headers, json=complete_profile_payload())).status_code == 200
    assert (await local_client.post("/api/activation/lookup-carrier", headers=headers)).status_code == 200
    assert (await local_client.post("/api/activation/confirm-profile", headers=headers)).json()["stage"] == "payment_required"
    assert (await local_client.post("/api/development/activate-starter", headers=headers)).json()["stage"] == "provisioning_consent_required"
    accepted = await local_client.post("/api/activation/confirm-provisioning", headers=headers)
    assert accepted.status_code == 202
    await drain_outbox()
    snapshot = (await local_client.get("/api/activation", headers=headers)).json()
    assert snapshot["stage"] == "forwarding_required"
    assert snapshot["number"]["e164"].startswith("+339")
```

- [ ] **Step 2: Run the integration journey and the full deterministic API suite**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync ruff check app tests
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync mypy app
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q
```

Expected: all deterministic tests PASS without Clerk, Stripe, or Telnyx
credentials. Provider-backed tests may SKIP only where already documented.

- [ ] **Step 3: Scan for forbidden automatic provisioning and unsafe local modes**

```bash
rg -n 'phone\.provision' apps/api/app/services/billing_service.py apps/api/app/webhooks/stripe.py
rg -n 'AUTH_MODE|BILLING_MODE|CARRIER_LOOKUP_MODE|TELEPHONY_MODE' \
  apps/api/app/core/runtime_validation.py apps/api/tests/test_deployment_readiness.py
```

Expected: first command returns no matches; second shows explicit production
rejection coverage for all four mode variables.

- [ ] **Step 4: Commit the API-level local journey**

```bash
git add apps/api/tests/integration/test_local_activation_to_number.py apps/api
git commit -m "test: prove consent-first local provisioning"
```
