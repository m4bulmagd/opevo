# Presvo Project Status

This document is the canonical source for Presvo's implemented capabilities,
known limitations, production-readiness gates, and roadmap. Historical specs,
plans, and audits describe the repository at earlier points in time and may no
longer match the current implementation.

## Current status

**Active development.**

Presvo is a working pre-production MVP with a production-oriented architecture.
Work is progressing toward a controlled
beta, with onboarding, compliance, recovery testing, and real-provider
certification still in progress.

## Current product boundary

- France-only phone-number provisioning
- One `starter` subscription plan
- One agent configuration per customer
- Inbound calls only
- `stt_llm_tts` as the customer-facing launch pipeline
- Stripe-hosted checkout and billing portal
- Telnyx telephony and LiveKit voice runtime
- PostgreSQL as the durable source of truth

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
| Guided onboarding | **Partial** | The backend exposes explicit onboarding states and the dashboard shows a checklist, but there is no resumable step-by-step wizard, forwarding guide, or test-call step. |
| Starter billing | **Implemented** | Stripe Checkout, Billing Portal, paid-invoice minute grants, subscription lifecycle handling, and PostgreSQL-backed usage accounting are present. |
| French number provisioning | **Implemented** | Queue-backed Telnyx provisioning, persisted status, retry handling, assignment, and routing gates are present; the current end-to-end path still needs fresh staging certification. |
| Agent configuration | **Implemented** | Customer-owned agent identity, owner context, system prompt, knowledge base, fixed launch pipeline, and guarded routing toggle are present. |
| Inbound voice runtime | **Implemented** | LiveKit dispatch and a separate agent worker support Speechmatics or Deepgram STT, Gemini LLM, and Speechmatics or ElevenLabs TTS. |
| Native-audio STS runtime | **Partial** | Gemini native-audio support exists in the worker and tests but is intentionally hidden from the customer-facing France launch. |
| Durable call lifecycle | **Implemented** | Incremental transcript persistence, call-scoped agent JWTs, a state machine, reconciliation, duration limits, and idempotent finalization are present. |
| Call review | **Implemented** | Call list/detail, transcript, summary, recording availability, signed recording URLs, usage charge, and soft archive are present. |
| Rich call-review workflow | **Partial** | Pagination contracts exist, but the web UI lacks pagination controls, inline audio playback, search, tags, notes, and structured action-item presentation. |
| Recording lifecycle | **Implemented** | LiveKit room-composite egress, private object storage, signed access, and bucket lifecycle configuration are present. |
| Transactional outbox | **Implemented** | Handlers cover `phone.provision`, `phone.enable`, `phone.disable`, `livekit.dispatch`, `summary.generate`, and `recording.stop`. |
| Call finalization effects | **Implemented** | Usage debit and the pending notification row are direct writes in the same call-finalization transaction. |
| Live dashboard and intervention | **Partial** | An optional backend WebSocket observer exists but is disabled by default, has a documented identity-key mismatch, and has no live-call web interface. |
| Push notifications | **Partial** | Notification records and provider boundaries exist, but private device-token delivery is not part of the launch path. |
| Production observability and CI | **Implemented** | Readiness checks, safe logging, OpenTelemetry, metrics, pinned CI actions, dependency audits, secret scanning, and container scanning are configured. |
| Production deployment | **Partial** | Hardened images, release migrations, deployment and rollback runbooks, and a provider comparison exist; a production platform and operating evidence are not yet approved. |
| French localization and legal surfaces | **Planned** | The launch UI is still English and approved privacy, terms, legal notice, support, retention, and subprocessor surfaces are absent. |
| Account export and deletion | **Planned** | User-facing export, deletion orchestration, and recording-access audit records are absent. |
| Mobile application | **Exploratory** | No mobile application is present in the repository. |

## Known limitations

- The customer journey is distributed across dashboard cards, billing, and
  agent settings rather than a guided onboarding experience.
- Real phone calls require external Clerk, Stripe, Telnyx, LiveKit, storage,
  and model-provider configuration.
- The current self-serve flow has not completed fresh multi-customer staging
  certification against all real providers.
- The optional realtime observer is not a supported customer feature.
- The application lacks French localization, approved legal pages, data
  export/deletion, and a complete account menu.
- The repository does not yet contain behavioral voice evaluations,
  accessibility end-to-end tests, load definitions, or completed recovery-drill
  evidence.

## Production-readiness gates

Presvo is intended for production, but it should not be described as
production-ready until all of these gates have evidence:

- Guided onboarding and complete customer account workflows
- Approved French legal, privacy, recording, retention, and support surfaces
- Auditable account export, deletion, and recording access
- Managed backups with a demonstrated restore
- Three clean real-provider staging certification journeys
- Load, concurrency, provider-outage, and recovery drills
- Behavioral voice-agent evaluations
- Accessibility and frontend performance gates
- A monitored controlled beta with explicit stop conditions

## Roadmap

### Phase 1 — Guided onboarding

- Resumable setup wizard
- Business and use-case templates
- Agent identity, context, and structured knowledge collection
- Carrier-aware forwarding instructions
- AI and recording-disclosure acknowledgement
- Test call or browser preview
- Readiness review and go-live action
- Resumable failure and delayed-provisioning states

### Phase 2 — Customer workflow completion

- French localization and locale-aware formatting
- Account and session controls
- Inline recording playback
- Call pagination, search, and richer review workflows
- Account data export and deletion
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
- Reusable conversation components
- Mobile experience
- Additional countries and plans after the France-first path is proven

## Related documentation

- [Backend context](architecture/backend-context.md)
- [Integration endpoints](architecture/integration-endpoints.md)
- [Production deployment decision](architecture/production-deployment.md)
- [Staging smoke runbook](architecture/staging-smoke-runbook.md)
- [Production-readiness hardening design](superpowers/specs/2026-07-12-production-readiness-hardening-design.md)
