# Presvo Project Status

This document is the canonical source for Presvo's implemented capabilities,
known limitations, production-readiness gates, and roadmap. Historical specs,
plans, and audits describe the repository at earlier points in time and may no
longer match the current implementation.

## Current status

**Active development.**

Presvo is a working pre-production MVP with a production-oriented architecture.
The local five-milestone self-service journey is implemented and covered by a
disposable browser test. Compliance, recovery evidence, cloud deployment, and
real-provider certification remain controlled-beta gates.

## Current product boundary

- France-only phone-number provisioning
- One `starter` subscription plan
- One agent configuration per customer
- Inbound calls only
- `stt_llm_tts` as the customer-facing launch pipeline
- Stripe-hosted checkout and billing portal
- Telnyx telephony and LiveKit voice runtime
- PostgreSQL as the durable source of truth
- English launch experience for professionals and small businesses in France
- Conditional forwarding for unanswered, busy, and unreachable calls

## Status vocabulary

- **Implemented** — present in the repository and supported by relevant tests.
- **Partial** — a technical foundation exists, but the customer workflow,
  operational proof, or real-provider validation is incomplete.
- **Planned** — accepted roadmap work with a defined outcome.
- **Exploratory** — a possible direction, not a delivery commitment.

## Feature matrix

| Area | Status | Current evidence and limitation |
|---|---|---|
| Public landing page and authentication shell | **Implemented** | Next.js landing page, Clerk sign-in/sign-up routes, and protected dashboard routing are present. |
| Customer dashboard | **Implemented** | Dashboard, calls, agent settings, billing, onboarding status, empty states, and server actions are present. |
| Guided onboarding | **Implemented** | A resumable five-milestone web journey covers business/receptionist content, local or Stripe billing, explicit number consent, provisioning, carrier-aware conditional forwarding, a timed test call, and explicit go-live. The disposable Playwright path proves local operation; real-provider certification remains a release gate. |
| Starter billing | **Implemented** | Stripe Checkout, Billing Portal, paid-invoice minute grants, subscription lifecycle handling, and PostgreSQL-backed usage accounting are present. |
| French number provisioning | **Implemented** | Queue-backed provisioning, persisted status, retry handling, assignment, and routing gates are present. Payment eligibility and explicit provisioning consent are separate. The fake local path is browser-proven; Telnyx still needs fresh staging certification. |
| Agent configuration | **Implemented** | Customer-owned agent identity, owner context, system prompt, knowledge base, fixed launch pipeline, and guarded routing toggle are present. |
| Inbound voice runtime | **Implemented** | LiveKit dispatch and a separate agent worker support Speechmatics or Deepgram STT, Gemini LLM, and Speechmatics or ElevenLabs TTS. |
| Native-audio STS runtime | **Partial** | Gemini native-audio support exists in the worker and tests but is intentionally hidden from the customer-facing France launch. |
| Durable call lifecycle | **Implemented** | Incremental transcript persistence, call-scoped agent JWTs, a state machine, reconciliation, duration limits, and idempotent finalization are present. |
| Call review | **Implemented** | Call list/detail, transcript, summary, recording availability, signed recording URLs, and usage charge are present. |
| Terminal-call removal | **Implemented** | An authenticated owner can use **Remove call** on a terminal call. Presvo synchronously stops persisted recording egress, deletes the original-audio object from active storage, then purges and hides transcript, summary, caller data, and call content. Active calls reject removal, and no backup-erasure claim is made. |
| Rich call-review workflow | **Partial** | Pagination contracts, inline original-audio playback, and structured next-action presentation are implemented, but the web UI lacks pagination controls, search, tags, and notes. |
| Recording lifecycle | **Implemented** | LiveKit room-composite egress, private object storage, signed access, and manual terminal-call removal are implemented; no automatic bucket lifecycle is configured. |
| Transactional outbox | **Implemented** | Handlers cover `phone.provision`, `phone.enable`, `phone.disable`, `livekit.dispatch`, `summary.generate`, and `recording.stop`. |
| Call finalization effects | **Implemented** | Usage debit and the pending notification row are direct writes in the same call-finalization transaction. |
| Live dashboard and intervention | **Partial** | An optional backend WebSocket observer exists but is disabled by default, has a documented identity-key mismatch, and has no live-call web interface. |
| Push notifications | **Partial** | Notification records and provider boundaries exist, but private device-token delivery is not part of the launch path. |
| Production observability and CI | **Implemented** | Readiness checks, safe logging, OpenTelemetry, metrics, pinned CI actions, dependency audits, secret scanning, and container scanning are configured. |
| Production deployment | **Partial** | Hardened images, release migrations, deployment and rollback runbooks, and a provider comparison exist; a production platform and operating evidence are not yet approved. |
| French localization and legal surfaces | **Planned** | The launch UI is still English and approved privacy, terms, legal notice, support, retention, and subprocessor surfaces are absent. |
| Account export and deletion | **Planned** | Per-terminal-call **Remove call** is implemented, but account-wide export, deletion orchestration, and recording-access audit records are absent. The intended account-deletion contract removes active call content and active object storage but makes no claim that historical backup copies are erased. |
| Automatic 30-day retention | **Planned** | Bucket lifecycle primitives exist, but an approved customer-facing automatic 30-day retention policy and operating proof are not implemented. |
| Appointment booking | **Planned** | No appointment workflow or calendar integration is part of the launch path. |
| Conversation flows | **Planned** | The current runtime uses one bounded receptionist configuration; typed flows, transitions, simulation, and authoring remain roadmap work. |
| Mobile application | **Exploratory** | No mobile application is present in the repository. |

## Known limitations

- Real phone calls require external Clerk, Stripe, Telnyx, LiveKit, storage,
  and model-provider configuration.
- The provider-free browser proof deliberately does not start the LiveKit agent;
  the deterministic simulator invokes the same forwarding-verification
  application service.
- The current self-serve flow has not completed fresh multi-customer staging
  certification against all real providers.
- The optional realtime observer is not a supported customer feature.
- One narrow recording-egress race remains: an egress start still in provider
  I/O has no persisted provider ID. If deletion or tombstoning wins that race
  and late best-effort cleanup fails, Presvo cannot durably record a pending
  stop. Persisted egress IDs are already synchronously stopped before active
  storage or database content is deleted.
- The application lacks French localization, approved legal pages,
  account-wide export/deletion orchestration, and a complete account menu.
- The repository contains four credential-gated LiveKit behavioral voice
  evaluations, but no completed credentialed run or evidence against an
  approved production-equivalent model. Accessibility end-to-end tests, load
  definitions, and completed recovery-drill evidence are also absent.

## Production-readiness gates

Presvo is intended for production, but it should not be described as
production-ready until all of these gates have evidence:

- Real-provider certification of the implemented guided activation workflow
- Approved French legal, privacy, recording, retention, and support surfaces
- Auditable account export, deletion, and recording access
- Managed backups with a demonstrated restore
- Three clean real-provider staging certification journeys
- Load, concurrency, provider-outage, and recovery drills
- Behavioral voice-agent evaluations
- Accessibility and frontend performance gates
- A monitored controlled beta with explicit stop conditions

## Roadmap

### Phase 1 — Real-provider activation certification

- Three clean Clerk/Stripe/Telnyx/LiveKit staging journeys
- Delayed and failed provisioning recovery evidence
- Carrier and number-type coverage for conditional forwarding
- Recording disclosure, legal, and support approval
- Cloud deployment, monitoring, rollback, and restore evidence

### Phase 2 — Customer workflow completion

- French localization and locale-aware formatting
- Account and session controls
- Call pagination, search, and richer review workflows
- Account data export and deletion
- Approved automatic 30-day retention behavior
- Approved legal, privacy, retention, subprocessor, and support surfaces
- Accessibility and frontend performance gates

### Phase 3 — Production certification

- Real-provider staging automation and repeated certification
- Backup restoration and object-lifecycle proof
- Provider-outage and incident drills
- Concurrency and load testing
- Behavioral voice-agent evaluations
- Controlled design-partner beta

### Phase 4 — Conversation-flow builder

Presvo will begin with the conversation runtime rather than a canvas:

1. Typed flow model and business templates
2. Conversation steps, conditional transitions, fallbacks, and end states
3. Validation, versioning, simulation, and call-path traces
4. Visual node editor after the runtime is proven
5. Reusable subflows and tool/function nodes after the authoring model is stable

This direction is inspired by [Retell AI's structured conversation flows](https://docs.retellai.com/build/conversation-flow/overview). Recall.ai is a meeting-bot platform and is not the intended product reference.

### Phase 5 — Advanced capabilities

- Live call monitoring and intervention
- Human transfer and tool calls
- Calendar and CRM integrations
- Appointment booking workflows
- Reusable conversation components
- Mobile experience
- Additional countries and plans after the France-first path is proven

## Related documentation

- [Backend context](architecture/backend-context.md)
- [Integration endpoints](architecture/integration-endpoints.md)
- [Production deployment decision](architecture/production-deployment.md)
- [Staging smoke runbook](architecture/staging-smoke-runbook.md)
- [Local self-service activation](architecture/local-self-service-activation.md)
- [Production-readiness hardening design](superpowers/specs/2026-07-12-production-readiness-hardening-design.md)
