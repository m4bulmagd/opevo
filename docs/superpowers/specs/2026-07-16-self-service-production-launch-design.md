# Self-Service Production Launch Design

## Status

Approved product and architecture design based on the 2026-07-16 repository
exploration and the product decisions made during review.

This document refines the earlier France MVP and production-hardening designs.
Where they differ, this design establishes the current target:

- the first market is France;
- the first product language is English, with French deferred;
- the release must be self-service for SMEs and independent professionals;
- the primary job is answering missed inbound calls;
- the production region is AWS Europe (Ireland), `eu-west-1`;
- the release is staged, but normal activation must not require Opevo staff.

## Goal

Enable an SME owner or independent professional in France to create an account,
subscribe, receive a Opevo number, configure an English-speaking AI
receptionist, forward missed calls, verify the setup, and review useful call
results without Opevo manually configuring or repairing the account.

The product is ready for public self-service only when it can safely answer a
missed call, preserve the original audio, produce a useful summary and
structured outcomes, explain failures, and permanently delete the call data at
the customer's request.

## Product Position

Opevo's launch product is an AI receptionist for missed calls. It is not a
general contact center or a visual agent-building platform.

The launch promise is:

> Forward the calls you cannot answer to Opevo. Your AI receptionist handles
> the conversation, and Opevo gives you the recording, summary, and outcome.

### Target customer

- SMEs operating in France.
- Independent professionals operating in France.
- One owner managing one business, one receptionist, and one Opevo number.
- Customers who expect setup and normal recovery to work without support.

### Launch language

- Product interface: English.
- AI receptionist: English.
- Caller disclosure: English, subject to qualified French legal review.
- French interface and voice behavior are post-launch work.

The interface must nevertheless allow at least 20 percent copy expansion so a
future French translation does not require a layout rewrite.

## Launch Scope

The production launch includes:

- France-only customer and number provisioning;
- one `starter` subscription plan;
- inbound customer calls only, plus a controlled system verification call;
- missed-call routing through conditional call forwarding;
- one business, owner, agent configuration, and Opevo number per account;
- a guided, resumable onboarding workflow;
- automatic carrier and number-type lookup with manual confirmation;
- carrier-aware forwarding instructions;
- a required end-to-end test call before activation;
- an English AI receptionist with mandatory disclosure and safety policy;
- original audio playback in Opevo;
- transcript, concise summary, and structured call outcomes;
- permanent user-triggered deletion across database and object storage;
- self-service retry and recovery for expected provider failures;
- AWS Ireland infrastructure, deployment, monitoring, backup, and rollback;
- a progressive release that exercises the same self-service journey at every
  stage.

The launch does not include:

- appointment booking;
- French localization or French agent behavior;
- teams, roles, invitations, or multiple locations;
- multiple agents, numbers, businesses, or plans per account;
- customer-initiated outbound calling, outbound campaigns, or live transfer;
- email, SMS, or push notifications for call results;
- customer-authored raw system prompts;
- configurable or automatically enforced 30-day retention;
- a general Retell-style conversation-flow builder.

## Current-State Findings

The repository already contains a strong backend foundation, provider adapters,
durable outbox processing, billing and usage ledgers, call persistence, agent
runtime, recording storage, a Next.js dashboard, CI, and a substantial automated
test suite. The launch risks are concentrated in product state, the first-run
journey, voice behavior, data lifecycle, and production operations.

### Runtime correctness

- Readiness is duplicated across `OnboardingService`,
  `DispatchEligibilityPolicy`, `SubscriptionAccessPolicy`, agent configuration,
  dispatch, and outbox processing.
- The dashboard can report `live` or `ready_to_enable` while dispatch rejects the
  call because the UI read model omits period bounds or a positive minute
  balance.
- The production Compose contract does not pass `TELNYX_ORDERING_ENABLED`, while
  its default is false. Number search can therefore work while self-service
  ordering remains disabled.
- Agent setup rules are duplicated and private helpers are imported across
  modules, making policy changes difficult to reason about.

### Voice safety and product behavior

- Required output and safety rules are currently conditional on a customer
  system prompt. A knowledge-base-only configuration can launch without those
  rules.
- The current greeting is generic and does not implement the approved
  receptionist fallback.
- Recording begins before the agent greeting, so the current disclosure timing
  does not match the real recording behavior.
- There is no behavioral evaluation suite for disclosure, grounding,
  uncertainty, interruptions, silence, urgency, or prompt injection.

### Self-service journey

- Onboarding is a collection of status cards rather than a persisted workflow.
- A new Clerk account can reach the dashboard before its webhook creates the
  local user and receive `User not synced` with no recovery path.
- Business identity, existing number, carrier, forwarding state, disclosure
  acknowledgement, and test-call state are not modeled.
- The agent configuration accepts unbounded text and relies on a raw prompt-like
  setup experience.
- Enablement is attempted before the frontend knows whether all backend
  prerequisites pass.

### Call review and data lifecycle

- Call listing has limit and offset but no total, next cursor, or `has_more`
  contract.
- The dashboard opens recordings externally rather than providing a complete
  inline review experience.
- Structured summary data is stored but not exposed as useful outcomes.
- The current delete path soft-archives a call; it does not purge audio,
  transcript, summary, or metadata.
- Recording access does not have a complete customer-visible audit trail.
- Full Clerk and Stripe webhook envelopes are retained without a defined
  minimization and purge policy.

### Frontend and operations

- There are no route-level loading, error, or global error boundaries.
- The app shell lacks a skip link and account/support controls, and the root
  language is hard-coded independently of a localization strategy.
- One failed dashboard request can collapse an entire page.
- The font registry loads far more families than the product needs.
- Production deployment is described but not implemented as infrastructure as
  code. There is no provider-specific CD workflow or executable recovery tool.
- Existing runbooks contain placeholder deployment commands.
- Metrics exist in application code, but production collectors, dashboards,
  alerts, backup proof, load evidence, and outage drills do not.

## Considered Delivery Approaches

### A. Production infrastructure first

Build AWS, deployment, monitoring, backups, and security before customer-facing
product work.

This lowers platform risk but delays validation of the activation and call-review
journeys. It also risks optimizing infrastructure around an unsettled product
workflow.

### B. Product experience first

Build onboarding and call review on the current runtime and deployment model.

This creates visible product progress quickly, but it puts a polished interface
over contradictory readiness rules and an unproven platform.

### C. Launch-focused vertical slices

Deepen one customer journey incrementally. Every slice includes domain logic,
API, frontend, tests, observability, and the production-platform work necessary
to exercise it.

This is the selected approach. It gives Opevo customer feedback without
postponing correctness or production engineering.

## Golden Customer Journey

1. The owner creates a Clerk account.
2. Opevo creates or recovers the local account without exposing a webhook race.
3. The owner enters their name, business details, existing business number, and
   other required profile information.
4. Opevo looks up the existing number's carrier and type. The owner confirms or
   corrects the result.
5. The owner purchases the single starter subscription.
6. A durable provisioning workflow orders and assigns a French Opevo number.
7. The owner configures the receptionist through guided business fields rather
   than a raw system prompt.
8. Opevo previews the greeting and recording disclosure, and records the
   accepted disclosure version.
9. Opevo shows instructions matched to the confirmed carrier and forwarding
   condition.
10. An automated test call proves forwarding, routing, disclosure, agent
    behavior, audio storage, transcript, summary, and structured outcomes.
11. The central readiness policy allows activation only after every launch
    prerequisite passes.
12. Real missed calls are handled by the receptionist.
13. Completed calls appear in Opevo with inline audio, transcript, summary, and
    outcomes.
14. The owner may permanently delete a call and all associated customer data.

Every expected failure state must provide a safe retry, correction, or next step.
Normal recovery must not require a database edit or Opevo employee action.

## Domain Architecture

The current FastAPI, PostgreSQL, Redis/ARQ, LiveKit agent, and Next.js
decomposition remains. The design deepens launch-critical modules instead of
rewriting the system.

### Business profile

Add one business profile per user containing at least:

- owner display name;
- business name and description;
- service description;
- opening hours;
- existing business phone number in E.164 format;
- detected and confirmed carrier;
- detected and confirmed number type;
- common questions and approved answers;
- information the receptionist should collect;
- urgency and escalation rules;
- callback expectations;
- prohibited topics.

Clerk-owned identity fields should not be copied unless Opevo needs an
authoritative product value. Fields must have explicit length, format, and item
limits shared by API schemas and frontend validation.

### Onboarding workflow

Introduce a persisted, versioned onboarding workflow. It records:

- workflow version;
- current step;
- completed steps;
- disclosure version and acknowledgement time;
- carrier lookup and confirmation state;
- forwarding-instruction state;
- test-call identifier and result;
- activation time;
- last recoverable failure and retry eligibility.

The workflow is resumable across devices and deploys. The frontend may suggest
the next step, but the server owns completion and transition rules.

### Customer readiness

Create one launch-readiness query service and one pure policy. The query service
loads authoritative data; the policy returns a versioned result such as:

```text
stage
can_activate
can_route
blockers[]
warnings[]
evaluated_at
policy_version
```

The policy evaluates:

- local user synchronization;
- business-profile completeness;
- supported market and plan;
- subscription status and current period bounds;
- positive minute balance;
- provisioning state and assigned number;
- agent configuration and content limits;
- disclosure acknowledgement;
- forwarding confirmation;
- successful test call;
- requested agent enablement;
- real provider routing projection;
- called-number match at dispatch time.

The same policy result drives onboarding, enablement, phone projection, dashboard
status, and dispatch. Dispatch may add call-specific checks but must not redefine
customer readiness.

Recommended customer-visible stages are:

- `account_sync_pending`
- `profile_required`
- `subscription_required`
- `number_provisioning`
- `number_provisioning_failed`
- `receptionist_setup_required`
- `forwarding_required`
- `test_call_required`
- `ready`
- `live`
- `suspended`

Machine-readable blocker codes accompany every stage. The UI maps codes to
helpful copy and actions; it does not parse backend prose.

### Receptionist policy

Separate mandatory system behavior from customer business content. The mandatory
policy is always present and cannot be overridden by customer-entered text.

It must require the receptionist to:

- identify itself as an AI receptionist;
- deliver the legally reviewed recording disclosure at the correct time;
- answer only from approved business information;
- ask one clarifying question when a request is uncertain;
- if still uncertain, state that it cannot confirm the answer;
- collect or confirm caller name, callback number, reason, urgency, and preferred
  callback time;
- say that the owner will review the message without promising a response time;
- never invent an answer, appointment, transfer, or completed action;
- follow configured prohibited-topic and emergency behavior.

The prompt builder must treat business content as data below the mandatory
policy. Prompt and knowledge-base sizes must be bounded to protect latency, cost,
and provider limits.

### Telephony setup

One telephony setup module coordinates:

- Telnyx number lookup for carrier and type;
- manual correction when lookup is missing or wrong;
- France-only Opevo number ordering;
- durable provider-side connection projection;
- carrier-aware conditional-forwarding instructions;
- test-call issuance and verification;
- idempotent retries and reconciliation.

Launch forwarding conditions are unanswered, busy, and unreachable. Opevo does
not initially manage customer schedules or PBX rules.

The automated forwarding test may originate one controlled verification call to
the customer's existing number and correlate the forwarded leg with the assigned
Opevo number. This is the only launch-time outbound-call exception. It cannot be
used to contact arbitrary recipients or exposed as a customer outbound feature.

### Call review

The call-review contract exposes:

- call and caller identifiers appropriate for the account;
- start time, duration, and completion state;
- recording availability and storage lifecycle state;
- transcript;
- concise summary;
- caller intent;
- urgency;
- requested follow-up;
- resolution state;
- action items;
- safe pagination metadata.

Sentiment is not a launch outcome because it is difficult to make reliable and
does not serve the primary job.

Recording objects are addressed by private storage keys, never durable public
URLs. Playback uses short-lived signed URLs after authorization and object
existence checks.

### Deletion

Deleting a call is a durable, idempotent purge workflow, not a soft archive. It
must remove or irreversibly redact:

- the recording object;
- transcript segments;
- summary and structured outcomes;
- caller-derived metadata not required for a narrowly defined audit record;
- related derived artifacts.

A minimal tombstone may retain the call identifier, account identifier, deletion
time, reason, actor, and purge status when legally and operationally justified.
The tombstone must not retain the deleted conversation.

The UI uses an explicit irreversible confirmation because cross-system deletion
cannot offer a reliable undo.

## Error and Recovery Model

- New-user synchronization displays a bounded `account_sync_pending` state and
  retries/reconciles safely instead of returning a terminal 401 experience.
- Duplicate Stripe, Clerk, LiveKit, and Telnyx events succeed idempotently.
- Provider timeouts preserve committed intent in the outbox and expose a pending
  or retryable state.
- Non-retryable failures move to an operator-visible terminal state with a fixed
  error category and correlation identifier.
- Customers can retry only transitions the server marks retryable.
- Exhausted minutes and expired subscription periods suspend routing and explain
  the exact recovery action.
- A failed test call does not activate routing and reports which verified stage
  failed.
- Dashboard sections fail independently and retain working content where safe.
- User-visible errors explain what happened, why when known, and the available
  corrective action without leaking provider or internal details.

An audited operator command surface supports inspection and replay of terminal
outbox or provisioning work. A general administrative UI is not required for
launch.

## Frontend Design

### Direction

The approved brand direction is a quietly premium, dependable receptionist:

- calm and trustworthy rather than futuristic;
- warm enough for independent professionals;
- operationally clear for busy business owners;
- minimal decorative AI imagery;
- subtle motion only when it explains progress or state change.

Use one production theme instead of exposing font and visual-preset controls to
customers. The recommended visual system is:

- light-first warm mist surfaces;
- deep ink text;
- one restrained indigo accent;
- semantic colors reserved for status;
- one humanist sans-serif family, with Figtree as the initial recommendation;
- mostly flat hierarchy created by spacing, alignment, typography, and dividers;
- cards only for distinct actionable regions.

Remove the multi-font runtime payload and keep only fonts the product actually
uses.

### Information architecture

Use a focused authenticated `/onboarding` journey outside the normal dashboard
shell. It has autosave, resume, visible progress, one primary action per step,
and a contextual help region for forwarding instructions.

The production application contains:

- **Home:** recent missed calls first, plus required account actions. Decorative
  metrics do not dominate the page.
- **Calls:** URL-based pagination and filters with a responsive list/table.
- **Call detail:** inline audio, summary, outcomes, transcript, and deletion.
- **Receptionist:** structured business knowledge, response rules, and greeting
  preview. No launch-time raw system prompt.
- **Phone setup:** assigned number, carrier, forwarding instructions, and test
  status.
- **Billing:** starter plan, current subscription, minutes, and billing portal.
- **Account:** owner and business details, privacy controls, and sign-out.

Status surfaces appear when action is needed. A healthy live account should not
be surrounded by setup cards.

### Interaction requirements

- All routes define loading, error, empty, and success states.
- Long provider operations show durable progress and may be safely resumed.
- Forms use visible labels, autocomplete metadata, exact validation, character
  limits, dirty-state protection, and accessible inline errors.
- Async updates use accessible live regions.
- Keyboard users receive a skip link and visible focus states.
- Touch targets are at least 44 by 44 CSS pixels.
- Mobile layouts adapt workflows rather than hiding critical actions.
- Call filters and pagination live in the URL.
- Dates, durations, and numbers use locale-aware formatters rather than fixed
  English date patterns.

The existing Tailwind 4 and shadcn source components remain the UI foundation.
Use installed primitives before adding custom controls. Before implementing the
onboarding UI, produce a short template and component review with URLs, preview
images when useful, license terms, accessibility notes, and compatibility with
Next.js 16, React 19, Tailwind 4, and the repository's `radix-vega` setup.

## Data and Privacy Decisions

The original audio is a launch feature because owners need to hear calls.
Transcripts, summaries, and outcomes remain visible only inside Opevo; launch
does not send them by email, SMS, or push notification.

The caller disclosure must be delivered before the recorded business
conversation in a manner approved by qualified French counsel. The system must
not claim that a call "may" be recorded when recording is enabled.

Customer-triggered deletion is launch scope. Automatic 30-day retention is a
deliberate post-launch product item, but indefinite retention is a known privacy,
security, and cost risk. Before public launch:

- counsel must approve the interim retention policy and disclosure;
- the privacy notice must state the actual behavior;
- existing object-store lifecycle configuration must be reconciled with the
  approved behavior so audio is not deleted or retained accidentally;
- the product must record who accessed or deleted a recording;
- Opevo must define and implement minimization for stored webhook payloads.

Legal review may elevate automatic retention into launch scope. The engineering
plan must treat that outcome as a launch-gate change, not a documentation-only
update.

## Production Platform

### Region and services

Deploy production and staging in AWS Europe (Ireland), `eu-west-1`.

- ECS Fargate: web, API, worker, and voice-agent containers.
- Application Load Balancer: TLS termination and health-based HTTP routing.
- RDS PostgreSQL: authoritative state and automated backups.
- ElastiCache: authenticated, encrypted Redis for transient coordination.
- S3: encrypted private recording objects.
- ECR: immutable application images.
- Secrets Manager: production secrets and rotation workflow.
- CloudWatch and OpenTelemetry: centralized logs, metrics, traces, dashboards,
  and alerts.
- WAF and edge/application controls: abuse protection and request limits.
- Route 53 and certificate management: production DNS and TLS.

LiveKit Cloud, Telnyx, Stripe, and Clerk remain managed external providers.

Terraform owns reproducible staging and production infrastructure. Local Compose
remains a development environment, not a deployment model.

### Deployment

1. CI runs formatting/lint, types, tests, dependency audits, secret scans, and
   container scans.
2. Immutable images are built and published to ECR.
3. A controlled one-off task runs forward-compatible database migrations.
4. ECS deploys new tasks and waits for container and load-balancer health.
5. Smoke tests exercise health, authentication, and a safe application path.
6. Failed health or smoke checks stop promotion and support documented rollback.

GitHub Actions authenticates to AWS using short-lived OIDC credentials. Long-lived
AWS deployment keys are not stored in GitHub.

### Security controls

- Least-privilege task and deployment roles.
- Private database, Redis, and object storage access.
- Encryption in transit and at rest.
- Content Security Policy and standard browser security headers.
- Trusted-proxy and client-IP configuration before rate limiting.
- Shared or edge rate limits appropriate for multiple replicas.
- Request-body limits for public APIs and webhooks.
- Fixed-category authentication logs that do not render unverified JWT claims.
- Short-lived recording URLs and recording-access audit events.
- Secret rotation and credential-revocation runbooks.
- Dependency-exception expiry enforced by CI.

### Observability and operations

Dashboards and alerts cover:

- signup-to-local-account synchronization;
- checkout and paid subscription activation;
- number provisioning duration and terminal failure;
- readiness blockers and activation conversion;
- inbound dispatch acceptance and rejection by fixed reason;
- agent join latency and answered-call success;
- call completion, transcript durability, recording finalization, summary, and
  outcome generation;
- minute exhaustion and routing suspension;
- outbox backlog, retries, and terminal events;
- provider webhook rejection and lag;
- deletion backlog and failure;
- API/web error rate, latency, and availability.

Runbooks use real AWS, application, and provider commands. Backups are not
considered complete until restoration has been tested and the result recorded.

## Verification Strategy

Each vertical slice ships with tests at the appropriate boundaries.

### Domain and integration tests

- Table-driven readiness-policy cases for every stage and blocker.
- Onboarding transition, resume, version, retry, and idempotency tests.
- Agent prompt-policy and content-boundary tests.
- PostgreSQL concurrency tests for subscription, provisioning, usage, call
  finalization, and deletion.
- Redis/outbox retry and terminal-failure tests.
- S3 signing, missing-object, access-audit, and purge tests.
- API schema tests for field bounds and machine-readable errors.

### Product and behavioral tests

- Browser tests for signup, sync recovery, profile setup, payment, provisioning,
  onboarding resume, forwarding, test call, activation, call review, and delete.
- Voice evaluations for English disclosure, grounding, clarification, fallback,
  caller detail capture, urgency, prohibited topics, prompt injection,
  interruptions, silence, and provider failure.
- Accessibility automation plus keyboard and screen-reader review.
- Responsive and cross-browser checks, including real mobile devices.
- Performance budgets for initial JavaScript, font payload, route response, and
  interaction latency.

### Staging and operational tests

- Provider certification using Clerk, Stripe test mode, Telnyx, LiveKit, and S3.
- Concurrent-call and webhook-burst load tests.
- Agent and worker soak tests.
- Provider outage and delayed-webhook drills.
- Backup restoration and data-integrity verification.
- Failed migration, failed deployment, rollback, and credential-rotation drills.

## Delivery Sequence

### Phase 1: Runtime correctness and safety

- Replace duplicate readiness logic with the central policy.
- Correct zero-minute, subscription-period, and provider-projection behavior.
- Pass and validate production number-ordering configuration.
- Add strict agent and business-content bounds.
- Make receptionist safety policy unconditional.
- Implement approved English greeting, disclosure timing, and fallback behavior.
- Remove unverified JWT claim values from authentication logs.
- Add regression and behavioral tests for these changes.

### Phase 2: AWS Ireland staging foundation

- Create Terraform modules and isolated staging state.
- Deploy web, API, worker, agent, RDS, Redis, and S3 foundations.
- Add ECR publication, migration tasks, health checks, GitHub OIDC, and staged
  deployment.
- Establish baseline telemetry, dashboards, alerts, and smoke tests.

### Phase 3: Self-service activation

- Add business-profile and onboarding-workflow persistence.
- Recover safely from Clerk synchronization delay.
- Integrate carrier and number-type lookup with manual confirmation.
- Build guided onboarding with autosave and resume.
- Harden paid activation and automatic French number provisioning.
- Add carrier-aware forwarding guidance.
- Add the end-to-end test call and readiness-based activation.
- Review frontend templates and reusable component sources before UI coding.

### Phase 4: Useful call review

- Define and expose structured outcomes.
- Add robust pagination and independent route failure states.
- Build responsive call history and inline audio playback.
- Add recording access audits.
- Replace soft archive with durable cross-system purge.
- Minimize stored webhook payloads and define their lifecycle.

### Phase 5: Production hardening

- Complete production Terraform and deployment promotion.
- Add security headers, WAF/request limits, shared rate limiting, and IAM review.
- Complete operator recovery commands and executable runbooks.
- Complete backup restoration, outage, load, soak, accessibility, performance,
  and voice-behavior evidence.
- Resolve or formally renew the expiring agent dependency exception.
- Complete qualified legal review and implement any launch-gate changes.

### Phase 6: Progressive release

1. Complete internal end-to-end certification.
2. Invite 5–10 France-based customers through the exact self-service flow.
3. Expand to 25–50 monitored customers without introducing manual setup.
4. Open public signup only after all release gates pass for the agreed
   observation period.

### Phase 7: Post-launch development

Prioritize from real customer evidence:

1. French localization and French receptionist behavior.
2. Configurable and automatic retention.
3. Appointment booking.
4. Teams, roles, multiple numbers, and multiple locations.
5. External notification channels and richer analytics.
6. A versioned conversation-flow runtime.
7. A visual Retell-style flow editor only after the runtime is proven.

## Release Gates

### Gate 1: Safe authoritative state

- One readiness result drives UI, activation, provider projection, and dispatch.
- Money, usage, and number-ordering operations remain idempotent under replay and
  concurrency.
- Prompt safety and input bounds cannot be bypassed by customer configuration.

### Gate 2: Support-free activation

- A new customer can recover from identity-sync delay.
- The customer can complete every onboarding step without staff action.
- Provisioning, forwarding instructions, and the test call have actionable
  failure states.
- Activation cannot succeed until the real call path is verified.

### Gate 3: Useful and controllable calls

- Eligible forwarded calls are answered reliably.
- Ineligible calls fail safely and truthfully.
- Every completed call produces reviewable audio and useful outcomes.
- Permanent deletion removes conversation content from every owned system.

### Gate 4: Operable platform

- Staging and production are reproducible from reviewed Terraform.
- Alerts detect launch-critical failures before customers report them.
- An operator can recover supported failures without editing production data.
- Backup restoration and deployment rollback have current evidence.

### Gate 5: Security, privacy, and quality

- No unresolved high or critical release-blocking finding remains.
- Legal review accepts the actual disclosure, recording, deletion, and retention
  behavior.
- Accessibility, performance, behavioral, load, outage, and recovery gates pass.
- Privacy and support documentation match deployed behavior.

### Gate 6: Public self-service

- The invited cohorts use the normal product path without hidden manual setup.
- Activation, answered-call, outcome-generation, deletion, and error-recovery
  targets remain within agreed thresholds for the observation period.
- Support demand is low enough to meet the product promise.

## Success Measures

Define exact thresholds before implementation planning, then measure at least:

- signup-to-profile completion;
- profile-to-paid activation;
- paid activation-to-number-ready time;
- onboarding and forwarding completion without support;
- test-call pass rate;
- eligible inbound dispatch and answer rate;
- time from call end to playable audio and outcomes;
- outcome-generation completeness and evaluation score;
- deletion completion time;
- terminal provider failure and manual-intervention rate;
- support contacts per activated customer;
- rollback and restoration success.

The design optimizes for reliable customer value and low support, not feature
count. Appointment booking and visual conversation flows do not move into launch
scope until missed-call answering is demonstrably dependable.

## Known Tradeoffs

- English-first operation narrows the useful France audience and must be stated
  clearly in acquisition and onboarding.
- Keeping original audio increases privacy, security, storage, and legal burden.
- Deferring automatic retention is an explicit launch risk subject to legal
  review.
- AWS adds more initial platform work than a PaaS, but it provides the selected
  regional, networking, recovery, and operational controls.
- A single-account model delays team revenue but avoids premature authorization
  and routing complexity.
- Staged cohorts reduce launch risk but do not permit concierge-only setup; they
  must exercise the public product path.
