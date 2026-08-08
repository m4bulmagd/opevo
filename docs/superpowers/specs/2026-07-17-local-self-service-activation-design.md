# Local-First Self-Service Activation Design

## Status

Approved product and architecture design based on repository exploration and the
product decisions made through 2026-07-17. Implementation has not started.

Implementation-plan review clarified one existing-state mismatch: the local
MinIO stack currently installs a 30-day expiration rule. Because automatic
retention was explicitly deferred, this slice removes that rule and preserves
recordings until user deletion or a later approved retention policy.

This document specializes the broader
`2026-07-16-self-service-production-launch-design.md` for the next local-first
product slice. Where the two designs differ, this document establishes the
current activation behavior:

- payment does not automatically order a phone number;
- the customer must explicitly confirm number provisioning;
- forwarding verification uses a short, fixed-message system call rather than
  a normal receptionist call;
- the customer must explicitly go live after successful verification;
- development and deterministic verification happen locally, without cloud
  deployment;
- France remains the phone-number market; Ireland remains only a possible
  future hosting region.

The older `2026-04-11-self-serve-france-mvp-design.md` remains useful historical
context, but its automatic post-payment provisioning behavior is superseded by
this design.

## Goal

Allow an SME owner or independent professional in France to configure Opevo,
pay, explicitly order one French Opevo number, set conditional call forwarding,
verify that forwarding works, and deliberately activate the receptionist without
Opevo staff or database intervention.

The complete journey must be buildable and testable locally. Real Stripe,
Telnyx, and LiveKit operations are opt-in. No cloud deployment or infrastructure
mutation is part of this slice.

## Product Context

### Target customer

- An SME owner or independent professional operating in France.
- One owner managing one business, one receptionist, and one Opevo number.
- A customer who wants missed calls answered and expects normal setup and
  recovery to require little or no Opevo support.

### Launch job

Opevo answers calls that the owner cannot take. After a normal call, the owner
can quickly understand what happened through the original recording, a concise
summary, and an obvious outcome.

Appointment booking, outbound calls, and a configurable conversation-flow
builder remain later product work.

### Language and brand

- Product interface: English.
- Receptionist language: English.
- Phone-number market: France.
- French localization: later.
- Brand personality: calm, refined, dependable, and quietly warm, like an
  excellent human receptionist.
- The visual design must avoid playful startup styling, technical AI imagery,
  generic card grids, and provider jargon.

## Scope

### Included

- A persisted, resumable activation domain.
- One business profile per user.
- Structured opening hours, including up to two intervals per day.
- An existing French business number in E.164 format.
- Automatic carrier lookup with user confirmation and manual fallback.
- Launch carrier guidance for Orange, SFR, Bouygues Telecom, Free, and Other.
- Profile-first, payment-second ordering eligibility.
- Explicit, idempotent provisioning consent.
- One French Opevo number per user.
- Durable asynchronous provisioning with safe retry.
- Conditional-forwarding guidance for unanswered, busy, and unreachable calls.
- A user-opened ten-minute forwarding-verification window.
- A fixed-message verification call that creates no normal customer call data.
- A final readiness review and explicit go-live command.
- A dedicated five-milestone activation interface.
- A minimal post-activation dashboard handoff that makes call summaries and
  outcomes clear.
- A development-only local identity adapter for provider-free manual and
  browser testing.
- Deterministic local billing, telephony, and verification substitutes.
- Automated coverage of state transitions, failures, concurrency, and the
  browser-level happy path.

### Excluded

- Cloud deployment or infrastructure provisioning.
- Appointment booking or calendar integration.
- Customer-initiated outbound calls, campaigns, or live transfer.
- French UI or French receptionist behavior.
- Multiple owners, staff roles, businesses, receptionists, plans, or numbers.
- A Retell- or Recall-style conversation-flow builder.
- Automatic 30-day data retention enforcement.
- A complete call-history redesign beyond the minimum post-activation handoff.
- Legal approval of the recording disclosure.

## Product Principles

1. The server owns activation truth. The web app renders a canonical snapshot.
2. Payment establishes eligibility; it does not imply consent to purchase a
   phone number.
3. Provider state is never reconstructed from optimistic frontend state.
4. Every externally visible mutation is idempotent and safely retryable.
5. Verification and activation are different decisions.
6. The verification call does not enter the customer-call data lifecycle.
7. Real call dispatch remains governed by the central readiness policy.
8. Expected failures give the owner a specific self-service next action.
9. Local substitutes exercise the same application services and transition
   rules as real providers.

## Golden Activation Journey

1. The owner creates or signs into a Opevo account.
2. Opevo creates the local user, default receptionist configuration, business
   profile draft, and activation record idempotently.
3. The owner completes the required business and receptionist fields.
4. The owner enters an existing French business number.
5. Opevo attempts carrier lookup. The owner confirms the result or selects a
   supported carrier or Other manually.
6. The owner reviews and confirms the profile.
7. The owner activates the starter subscription.
8. Opevo shows a number-order review. No order has occurred yet.
9. The owner selects **Confirm and provision my number**.
10. A durable job orders and configures one French Opevo number.
11. Opevo presents conditional-forwarding instructions for the confirmed
    carrier.
12. After applying the instructions, the owner opens a ten-minute verification
    window.
13. The owner calls the existing business number from another phone and allows
    the call to forward.
14. Opevo plays a versioned fixed success message and hangs up. It does not run
    the receptionist or persist a normal call, recording, transcript, summary,
    or usage charge.
15. Opevo marks forwarding verified only after successful verification-session
    completion.
16. The owner reviews the final readiness status and selects **Go live**.
17. The central readiness policy and real provider projection both succeed
    before Opevo reports the receptionist as active.
18. The owner enters the normal dashboard, where real calls expose a concise
    summary, outcome, follow-up state, and original recording.

The owner can leave, refresh, sign out, or restart local services at any point
and resume from the authoritative next step.

## Domain Model

### `BusinessProfile`

One row per user, enforced by a unique database constraint.

Required fields:

- `owner_name`
- `business_name`
- `business_type`
- `public_description`
- `timezone`
- `business_hours`
- `existing_phone_e164`
- `confirmed_carrier`
- `receptionist_name`

Optional fields:

- FAQs as bounded question-and-answer items
- special call instructions
- escalation notes

Supporting fields include detected carrier, lookup time and status, confirmation
time, content revision, routing revision, and normal timestamps. Provider lookup
payloads are not stored wholesale.

Recommended launch bounds are:

- names: 1-100 characters;
- business type: 1-100 characters;
- public description: 1-1,000 characters;
- at most 20 FAQ items, each with a bounded question and answer;
- special instructions and escalation notes: at most 2,000 characters each.

These limits must be represented once in the API contract and shared with the
frontend rather than drifting between layers.

`business_hours` uses a versioned structure keyed by weekday. Each day is either
closed or contains one or two non-overlapping local-time intervals. The initial
UI supports split days such as 09:00-12:00 and 14:00-18:00. The timezone is an
IANA identifier and defaults to `Europe/Paris` for the France launch, while still
requiring user confirmation.

### `CustomerActivation`

One row per user, enforced by a unique database constraint. It stores stable
workflow facts rather than copies of Stripe or Telnyx truth:

- workflow version;
- confirmed profile revision and time;
- provisioning consent time and deterministic idempotency key;
- current verification-window start and expiry;
- current verification-session identity and status;
- routing fingerprint verified and verification time;
- final go-live approval time;
- last safe recoverable failure code;
- normal timestamps.

The routing fingerprint is derived from routing-sensitive facts, including the
existing business number, confirmed carrier, and assigned Opevo number. A
change to any of these facts invalidates forwarding verification and final
go-live approval. Content-only changes such as hours, FAQs, or receptionist
wording do not require forwarding verification.

### `PhoneNumber`

The existing model remains the source for the assigned Opevo number and its
provider projection. Add a database uniqueness constraint on `user_id` so an
account cannot hold multiple Opevo numbers through races or retries.

### `PhoneNumberProvisioning`

The existing one-per-user provisioning model remains the durable source for
queued, running, succeeded, and failed order state. It must retain bounded
attempt metadata, a safe failure code, retry eligibility, the provider order
reference, and the assigned phone-number link when successful.

### Receptionist runtime projection

`BusinessProfile` is authoritative for customer-entered business and
receptionist content. The existing `AgentConfig` remains the bounded runtime
projection used by call dispatch and the voice agent.

Saving guided profile content updates that projection through one application
service: receptionist name maps to the runtime agent name, and approved business
facts map to owner context and knowledge content. Resumable incomplete drafts are
projected safely: missing values use stable `Not provided` labels, a missing
receptionist name preserves the existing/default agent name, and generated
content never contains the literal string `None`. The projection revision still
advances with the authoritative profile revision so later readiness checks can
detect stale runtime content.

Introduce `activation_flow_enabled=false` with this projection slice rather
than waiting for the readiness slice. While the flag is false, the legacy agent
configuration PATCH endpoint remains backward compatible. When it is true,
customer PATCH attempts that include `agent_name`, `owner_context`,
`system_prompt`, or `knowledge_base` fail with HTTP 409 and the stable code
`agent_content_managed_by_profile`; non-projected fields keep their existing
behavior. This only moves the default-off guard earlier and does not enable the
activation journey.

Normal dispatch uses `AgentConfig.business_display_name` for the spoken business
identity. The existing user-name fallback is permitted only for legacy rows
while the flag is false; an enabled activation flow with no projected business
name fails dispatch configuration closed. Activation does not expose a raw
system-prompt editor. Mandatory system behavior remains separate from the
customer projection and cannot be overridden by profile content.

The projection version participates in readiness so a stale or failed projection
cannot be activated silently.

### `ActivationEvent`

Add a small append-only audit trail for important state changes. Events include:

- profile confirmed;
- carrier detected, manually selected, or confirmed;
- provisioning consented, queued, succeeded, failed, or retried;
- verification window opened or expired;
- verification session started, succeeded, or failed;
- go-live requested, succeeded, failed, or invalidated;
- runtime paused or restored.

Events contain safe identifiers and bounded machine-readable metadata. They do
not contain credentials, raw provider payloads, recordings, transcripts, or
customer-entered free text.

## Canonical Activation State

`ActivationSnapshotService` loads the business profile, activation milestones,
subscription, provisioning record, assigned number, receptionist configuration,
usage state, and central readiness result. It returns one canonical snapshot.

Stripe remains authoritative for payment status. The telephony adapter remains
authoritative for provider number state. The activation record remains
authoritative for explicit customer consent, verification, and go-live approval.

Customer-facing stages are evaluated in this order:

1. `profile_required`
2. `payment_required`
3. `provisioning_consent_required`
4. `provisioning`
5. `provisioning_failed`
6. `forwarding_required`
7. `verification_window_open`
8. `ready_to_activate`
9. `activating`
10. `runtime_paused`
11. `active`

`runtime_paused` applies when a previously activated customer no longer passes
runtime readiness. A routing-sensitive profile change also clears verification
and go-live approval, requiring another verification and explicit go-live.
Temporary subscription or balance failures preserve the earlier go-live
approval and may resume automatically only after authoritative billing state and
all runtime checks recover. The UI must show why answering is paused.

`activating` is the transient state after a valid go-live command while the
durable provider-routing projection is pending. A failed projection returns to a
safe actionable state with the receptionist disabled; it never reports `active`
optimistically.

The snapshot contains at least:

```text
workflow_version
stage
completed_milestones[]
next_action
blockers[]
warnings[]
profile
billing
number
forwarding
verification
runtime_readiness
evaluated_at
```

Blockers and next actions are stable machine-readable codes. Customer copy is
mapped by the web application and is never derived by parsing backend prose.

## Command and Query API

The primary query is:

```http
GET /api/activation
```

It returns the canonical activation snapshot and is the only source the
activation UI uses to determine progress or available actions.

Commands are explicit:

```http
PUT  /api/business-profile
POST /api/activation/lookup-carrier
POST /api/activation/confirm-profile
POST /api/activation/confirm-provisioning
POST /api/activation/retry-provisioning
POST /api/activation/open-verification-window
POST /api/activation/go-live
```

The existing billing checkout and Stripe webhook endpoints remain responsible
for real billing. The qualifying paid event updates subscription and minute
state but no longer enqueues number provisioning. Provisioning begins only from
the explicit confirm-provisioning command.

Every command:

- authenticates the Opevo user and enforces ownership;
- validates the canonical current state;
- locks the user or activation ordering boundary as appropriate;
- performs its database changes atomically;
- creates durable outbox work for external effects;
- uses deterministic idempotency for repeat requests;
- returns the refreshed activation snapshot;
- returns bounded machine-readable errors without raw provider messages.

Expected errors distinguish at least validation, conflict/stale state,
temporarily unavailable provider, retryable provisioning failure, permanent
provider rejection, expired verification window, and readiness failure.

## Provider Boundaries

### Carrier lookup

`CarrierLookupProvider.lookup(e164)` returns a small provider-neutral result:

```text
normalized_number
country_code
carrier_name
normalized_carrier
number_type
looked_up_at
```

The Telnyx implementation uses number lookup for a normalized E.164 number.
The product maps results to Orange, SFR, Bouygues Telecom, Free, or Other.
Timeouts, rate limits, and provider failures do not block manual carrier
selection. The customer always confirms the normalized result.

### Number provisioning

`NumberProvisioningProvider` retains the existing Telnyx and local fake
implementations. The application requests a France-compatible number through a
durable job after explicit consent. The provider receives a stable customer
reference or idempotency key so reconciliation cannot create a second order.

### Forwarding instructions

`ForwardingInstructionCatalog` is Opevo-owned content keyed by confirmed
carrier and forwarding condition. It provides instructions for:

- unanswered calls;
- busy calls;
- unreachable calls.

Unconditional forwarding is not the default journey. The Other path uses safe
generic guidance and clearly states that exact codes may vary by plan or
carrier. Instruction content is versioned so Opevo can identify which guidance
the customer saw.

### Billing

Stripe remains the production billing authority. A local fake billing adapter
activates the same starter-subscription application service without contacting
Stripe. Fake billing is available only in local and test environments.

### Forwarding verification

The verification workflow belongs to Opevo's inbound-call path, not to an SMS
or voice-OTP product.

Opening a window creates a durable start and expiry time. Only one window may be
active per customer. The window lasts ten minutes according to server time.

When a SIP participant arrives for the assigned Opevo number, inbound routing
checks for a valid verification window before normal receptionist dispatch. If
one exists, it atomically claims a verification session and emits a dedicated
verification dispatch intent. This path requires a provider-ready assigned
number but does not require the receptionist to be active.

The verification session:

- contains no customer-authored prompt or knowledge;
- plays the versioned fixed message, "Forwarding test successful. Return to
  Opevo to go live," without invoking an LLM;
- does not create a normal `Call` row;
- does not start recording or transcription;
- does not enqueue summary generation or usage charging;
- reports completion through a scoped, short-lived session credential;
- marks forwarding verified only after successful playback completion;
- hangs up immediately afterward.

Provider redirect or diversion metadata is validated when available. Some
carriers may not preserve it, so the launch mechanism is an operational
forwarding check rather than cryptographic proof of number ownership. Its
protections are the assigned destination, the short owner-opened window, and
single-use atomic claim.

An expired or failed session returns the customer to forwarding guidance and
allows another window. Duplicate webhook or completion events are idempotent.

## Local-First Modes and Production Guards

Local development must complete the whole journey without purchasing a number
or deploying infrastructure:

- a local identity adapter bootstraps one deterministic development user and
  issues only the fixed local credential accepted by the development API;
- fake billing activates the starter subscription deterministically;
- fake provisioning assigns a reserved, non-routable test number;
- fake carrier lookup returns configurable normalized results or controlled
  failures;
- a development simulator emits the same application-level forwarded-call
  event consumed by real inbound verification;
- browser tests use these adapters and the normal activation APIs.

Development controls are registered only in an explicit local/test environment.
They are absent in production routing, not merely hidden in the UI. Production
startup fails closed if local identity, fake billing, or fake telephony is
selected. The local credential is never accepted when Clerk mode is active.

Selecting real Telnyx, Stripe, or LiveKit behavior requires explicit settings
and credentials. Credentialed provider evaluations remain optional during local
development and become pre-release certification work. This slice does not
create cloud resources or deploy Opevo.

## Runtime Readiness Integration

The existing central readiness policy remains the final authority for real call
handling. Extend its authoritative snapshot with activation prerequisites rather
than creating a second dispatch policy.

Real dispatch requires, at minimum:

- active local user;
- supported France launch profile;
- complete business profile;
- active eligible subscription and positive usable balance;
- succeeded provisioning and provider-ready assigned number;
- complete bounded receptionist configuration;
- successful forwarding verification for the current routing fingerprint;
- current final go-live approval;
- enabled provider routing projection;
- called-number match at inbound dispatch time.

The go-live command locks the same user ordering boundary used by dispatch,
re-evaluates readiness, requests enablement, and reports `active` only when the
provider projection and central policy agree. It cannot bypass a missing
prerequisite.

The verification path is a separate, tightly bounded system-call policy. It is
evaluated before normal dispatch and cannot create a normal call accidentally.

## Activation Experience

### Route and shell

Activation uses a dedicated `/activate` route. Before go-live, the normal entry
point takes the user to the activation journey while preserving access to
account, billing, sign-out, and already existing call history where applicable.

The layout is a focused, responsive workflow with one primary action per screen,
visible save state, a compact milestone navigator, and a clear way to leave and
resume. It uses calm typography, warm tinted neutrals, restrained color, and
strong semantic status. It must support keyboard navigation, visible focus,
screen readers, reduced motion, and at least 20 percent future copy expansion.

Detailed component and template selection is a separate implementation
checkpoint. Existing shadcn primitives may be composed where they serve the
design; the UI must not become a nested grid of generic cards.

### Five milestones

1. **Your business**
   - Owner and business fields.
   - Structured hours.
   - Existing French number.
   - Carrier lookup and confirmation.

2. **Your receptionist**
   - Receptionist name.
   - Public description.
   - FAQs, special instructions, and escalation notes.
   - Plain-language preview of what Opevo knows.

3. **Your Opevo number**
   - Subscription/payment.
   - Explicit provisioning review and consent.
   - Waiting, success, correction, and retry states.

4. **Forward missed calls**
   - Carrier-specific conditional-forwarding guidance.
   - Separate unanswered, busy, and unreachable instructions.
   - Copyable codes only where they are reliable.
   - Self-service troubleshooting and an Other-carrier path.

5. **Test and launch**
   - Ten-minute verification window and server-synchronized countdown.
   - Waiting, success, expiry, and retry states.
   - Final readiness review.
   - Explicit **Go live** action.

The current product copy that describes an "Irish number" is incorrect and must
be changed to a French Opevo number. Ireland is not presented as a customer
number country.

### Error and recovery behavior

- Saving a draft never advances a milestone silently.
- The UI explains whether a failed action charged the customer or ordered
  anything.
- A provisioning action remains disabled while the durable request is pending.
- Retry actions reuse the existing provisioning identity.
- Provider failures show customer-safe copy and a reference code derived from
  the audit event, not raw exceptions.
- Carrier lookup failure immediately offers manual selection.
- An expired test window preserves the forwarding instructions and offers a new
  window.
- A readiness blocker links to the exact milestone that can correct it.
- Process restarts and refreshes reload the same authoritative next action.

### Post-activation handoff

After activation, the normal dashboard leads with whether Opevo is answering.
The recent-calls surface must show, when available:

- caller identity or masked number;
- call time and duration;
- one-sentence summary;
- caller intent as the initial outcome label;
- whether follow-up is required;
- action items or an explicit no-action state;
- access to the original recording and deletion action.

The backend call-history contract currently exposes `summary_text` but not the
stored structured fields. Extend it with a bounded customer-facing projection of
`caller_intent`, `action_items`, `sentiment`, and `follow_up_required`. Until a
summary is ready, the UI shows a truthful processing or review-required state;
it does not invent an outcome in the browser.

This is the minimum first-call handoff included in the slice. Broader filtering,
analytics, pagination redesign, and full call-detail redesign remain separate.

## Concurrency and Failure Rules

- Unique constraints enforce one profile, activation, provisioning record, and
  assigned number per user.
- Number-order consent produces one deterministic idempotency key.
- Concurrent consent requests result in one durable order intent.
- Provider timeout is not interpreted as provider failure until reconciliation
  checks the stable customer reference.
- Retry never searches for or buys another number while an ambiguous order may
  exist.
- Verification-window claim, success, expiry, and profile invalidation use a
  locked activation row.
- A phone or carrier change racing a verification completion cannot verify the
  new routing fingerprint.
- A go-live request racing subscription, profile, or provider changes must
  re-evaluate under the same user lock used by dispatch.
- Duplicate webhooks, outbox deliveries, and verification completions return
  idempotent outcomes.
- An unrecoverable external effect is surfaced as a safe action-required state;
  it never requires the customer to edit hidden state.

## Privacy and Security

- Every activation API requires authenticated user ownership.
- Local identity mode is server-configured, development-only, uses no
  customer-selected identity, and is rejected by both API and web production
  startup validation.
- French business numbers are normalized and validated server-side.
- Original recordings remain available for normal calls until user deletion or
  a future retention policy removes them.
- The repository's existing local MinIO 30-day expiration rule is removed in
  this slice so local behavior does not silently enforce a retention decision
  that has been deferred. Any future automatic retention policy requires its
  own approved product/legal decision and implementation.
- The current user-triggered deletion path is a product dependency and must not
  be described as permanent until database and object-storage purge is proven.
- Verification sessions create no recording, transcript, summary, normal call
  metadata, or usage charge.
- Provider secrets and session credentials never enter browser responses or
  activation events.
- Provider errors and lookup payloads are minimized before storage and logging.
- Customer-authored content is bounded before it reaches persistence, prompts,
  logs, or agent dispatch metadata.
- The mandatory recording and AI disclosure remains outside customer control.
- Qualified French/EU legal review of disclosure and recording behavior remains
  a release gate.
- Automatic 30-day retention is documented as future work and is not claimed by
  this implementation.

## Observability

Add low-cardinality metrics and structured events for:

- activation stage transitions;
- activation blocker codes;
- carrier lookup outcome and latency;
- provisioning outcome, attempt, latency, and reconciliation;
- verification window opened, expired, succeeded, or failed;
- go-live outcome and blocker;
- runtime pause and restoration reason;
- local-fake versus real-provider mode at startup.

Logs use internal IDs and safe enums. Customer-entered profile text, complete
phone numbers, raw provider responses, and credentials are excluded. Activation
events provide the customer-support reference when recovery cannot be completed
automatically.

## Test Strategy

### Domain and database

- Migration upgrade and downgrade coverage.
- Unique constraints for profile, activation, provisioning, and phone number.
- Business-hour validation, split days, closed days, and timezone validation.
- French E.164 normalization and rejection cases.
- Profile completeness and bounded-content tests.
- Routing-fingerprint invalidation rules.
- Every allowed and rejected activation transition.

### API and services

- Authentication and cross-account isolation for every endpoint.
- Local identity acceptance in development and unconditional rejection in
  production or Clerk mode.
- Canonical snapshot precedence and stable blocker codes.
- Carrier lookup success, normalization, fallback, timeout, rate limit, and
  provider failure.
- Payment without provisioning consent.
- Concurrent and repeated provisioning consent.
- Retry and ambiguous-order reconciliation.
- Verification success, playback failure, expiry, duplicate events, and race
  conditions.
- Proof that verification creates no normal call, recording, transcript,
  summary, outbox summary work, or usage charge.
- Go-live rejection for each missing readiness condition.
- Runtime pause and safe resumption.
- Redaction tests for logs, events, and error responses.

### Agent and inbound routing

- Verification dispatch uses only the fixed-message mode.
- No customer content can reach the verification session.
- Completion credentials are short-lived and session-scoped.
- Normal receptionist dispatch is impossible before final go-live.
- Existing disclosure, prompt-safety, call-limit, recording, and summary tests
  remain green.

### Web

- Every milestone, loading state, empty state, error state, and retry state.
- Autosave and resume behavior.
- Server-synchronized verification countdown and expiry.
- Provisioning double-click protection.
- Accessible names, focus flow, keyboard use, and mobile layout.
- Dashboard runtime-pause banner.
- Structured call outcome, follow-up, summary-processing, recording, and delete
  states.
- Correction of every Irish-number reference in the launch UI.

### End to end

A deterministic browser test must create a local user and complete:

```text
profile
→ carrier confirmation
→ fake payment
→ explicit provisioning consent
→ fake French number
→ forwarding guidance
→ verification window
→ simulated forwarded call
→ fixed-message completion
→ explicit go-live
→ active dashboard
```

The journey must require no database edits, external provider calls, or cloud
resources. Additional end-to-end cases cover refresh/resume, lookup fallback,
provisioning retry, expired verification, and rejected go-live.

Credentialed Telnyx and LiveKit evaluations are separate, opt-in pre-release
certification and do not block ordinary local development.

## Acceptance Criteria

The slice is complete when:

- a new local user can complete the full activation journey without staff or
  database intervention;
- activation survives browser refreshes and local service restarts;
- profile, activation, provisioning, and assigned-number cardinality are
  database-enforced;
- payment cannot order a number;
- only explicit consent can start idempotent provisioning;
- carrier lookup failure has a manual self-service path;
- forwarding guidance covers the four approved carrier choices plus Other and
  never defaults to unconditional forwarding;
- verification is single-use, expires after ten minutes, plays only the fixed
  message, and produces no normal call data;
- no real call can dispatch before current verification and explicit go-live;
- routing-sensitive changes pause answering until reverification and another
  go-live approval;
- every expected failure produces a safe customer next action;
- real provider calls are disabled by default locally and fake providers cannot
  be enabled in production;
- the provider-free browser journey does not require Clerk, and local identity
  mode cannot start in production;
- the dashboard accurately reports answering state and exposes summary,
  structured outcome, follow-up, original audio, and deletion controls;
- all deterministic API, agent, web, and browser tests pass;
- the README and product roadmap describe this local-first capability without
  claiming cloud deployment, legal approval, automatic 30-day retention, or
  finished provider certification.

## Deferred Work

- Cloud architecture and deployment, with Ireland as the current future-region
  preference.
- Credentialed provider certification and real French carrier testing.
- Qualified French/EU legal review.
- French localization and French receptionist behavior.
- Appointment booking and calendar integration.
- Configurable conversation flows similar to later-stage voice-agent builders.
- Multiple users, businesses, receptionists, numbers, and plans.
- Notifications, live transfer, and outbound calls.
- Automatic 30-day retention enforcement.
- A complete call-review and analytics redesign.
