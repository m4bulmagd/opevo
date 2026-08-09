# Opevo Project Status

This document is the canonical source for Opevo's implemented capabilities,
known limitations, production-readiness gates, and roadmap. Historical specs,
plans, and audits describe the repository at earlier points in time and may no
longer match the current implementation.

## Current status

**Active development; production-oriented and locally verified, not
production-certified.**

Opevo is a working MVP with a production-oriented architecture. The local
five-milestone self-service journey, durable recording lifecycle, and
reversible account-deactivation/reactivation lifecycle are implemented and
locally verified. Compliance, recovery evidence, cloud deployment, and
real-provider certification remain controlled-beta gates.

The voice worker uses one coherent exact LiveKit Agents 1.6.9 package family.
Its public lifecycle migration, asset download, dependency audit, final
container build, non-root runtime imports/assets, and health endpoint are
locally verified. The four credential-gated behavioral voice evaluations still
require a configured real-provider run.

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
| Customer dashboard | **Implemented** | The complete Opevo visual system is applied across the responsive dashboard, calls, live assistant settings, billing, account, onboarding status, empty states, and server actions. Exact colors, typography, spacing, borders, shadows, card hierarchy, and light/dark layouts are regression-protected. |
| Guided onboarding | **Implemented** | A resumable five-milestone web journey covers business/receptionist content, local or Stripe billing, explicit number consent, provisioning, carrier-aware conditional forwarding, a timed test call, and explicit go-live. The disposable Playwright path proves local operation; real-provider certification remains a release gate. |
| Starter billing | **Implemented** | Stripe Checkout, a pinned Billing Portal configuration, paid-invoice minute grants, subscription lifecycle handling, and PostgreSQL-backed usage accounting are present. Portal subscription-only cancellation remains active until the paid-period end; owner account deactivation cancels immediately without automatic proration or refund. The Portal configuration still requires deployment-time review and real Stripe certification. |
| French number provisioning | **Implemented** | Queue-backed provisioning, persisted status, retry handling, assignment, and routing gates are present. Payment eligibility and explicit provisioning consent are separate. The fake local path is browser-proven; Telnyx still needs fresh staging certification. |
| Agent configuration | **Implemented** | Customer-owned agent identity, owner context, system prompt, knowledge base, fixed launch pipeline, guarded routing toggle, persisted profile-owned content overrides, and unsaved-change protection are present. |
| Inbound voice runtime | **Implemented** | LiveKit dispatch and a separate agent worker support Speechmatics or Deepgram STT, Gemini LLM, and Speechmatics or ElevenLabs TTS. The coherent LiveKit Agents 1.6.9 family uses public turn-handling and asset-download APIs; 742 local tests, the coverage gate, dependency audit, final image build, non-root runtime smoke, and health probe pass, while credentialed behavioral evaluation remains a production-readiness gate. |
| Native-audio STS runtime | **Partial** | Gemini native-audio support exists in the worker and tests but is intentionally hidden from the customer-facing France launch. |
| Durable call lifecycle | **Implemented** | Incremental transcript persistence, call-scoped agent JWTs, a state machine, reconciliation, duration limits, and idempotent finalization are present. |
| Call review | **Implemented** | Call list/detail, transcript, summary, recording availability, signed recording URLs, and usage charge are present. |
| Account deactivation and reactivation | **Implemented** | Authenticated owners can enter exact `DEACTIVATE` confirmation. `active -> deactivating` immediately blocks new service, then durable reference-only work disables routing, cancels an owner-requested subscription without automatic proration/refund, drains any admitted call, releases the number, resets number-cycle state, and reaches `inactive`. Inactive owners keep read-only historical calls, recordings, billing, and retained business/receptionist configuration. A generation-matched new subscription reactivates the account, preserves the confirmed profile/carrier, and resumes at fresh number consent for a new number, forwarding verification, and explicit go-live. |
| Terminal-call removal | **Implemented** | An authenticated owner can use **Remove call** on a terminal call. One local transaction purges customer content, hides the call, and returns `204` without LiveKit or storage I/O. When a private recording operation or legacy recording metadata exists, it also records stop/delete intent and reference-only reconciliation work; non-exhausting asynchronous cleanup follows. Repeated removal is idempotent, active calls reject removal, and no synchronous provider, backup, or historical-copy erasure claim is made. |
| Rich call-review workflow | **Partial** | Server-rendered pagination and caller-number/summary/intent search, inline original-audio playback, and structured next-action presentation are implemented; tags and notes remain. |
| Recording lifecycle | **Implemented** | A private recording operation and reference-only reconciliation intent commit before recording-start provider I/O. Completion requests stop reconciliation even without a provider ID; signed egress webhooks store sanitized facts and wake reconciliation after commit. Private object storage, signed playback, and asynchronous owner-removal cleanup are implemented; no automatic bucket lifecycle is configured. |
| Transactional outbox | **Implemented** | Handlers cover `phone.provision`, `phone.enable`, `phone.disable`, `livekit.dispatch`, `summary.generate`, `recording.reconcile`, and `account.deactivate`. Recording and account-deactivation events use private operation aggregates and carry only `operation_id`. |
| Worker isolation (4A + 4B) | **Implemented** | `worker-lifecycle` owns `arq:queue` call finalization/reconciliation at 10 default slots; `worker-background` owns `arq:queue:background` outbox delivery/reconciliation and verification expiry at 4. Health and class-specific queue metrics are present. The evidence is controlled ten-call local/CI evidence only: four blocked background slots while ten lifecycle probes start simultaneously, with local/CI queue-delay p95 `<= 2 seconds`. |
| Call finalization effects | **Implemented** | Usage debit and the pending notification row are direct writes in the same call-finalization transaction. |
| Live dashboard and intervention | **Partial** | A complete, visibly labelled local-only live-call Preview exists. The optional backend WebSocket observer remains disabled by default, has a documented identity-key mismatch, and is not connected to the Preview; live monitoring and intervention remain unimplemented. |
| Push notifications | **Partial** | A visibly labelled local notification Preview exists, and notification records/provider boundaries exist, but private device-token delivery is not part of the launch path. |
| Production observability and CI | **Partial** | Readiness checks, safe logging, bounded recording and account-deactivation metrics, OpenTelemetry, pinned CI actions, dependency audits, secret scanning, and container scanning are configured and locally verified. Cloud monitoring, required alert routing, and operating evidence are absent. |
| Production deployment | **Partial** | Hardened images, release migrations, deployment and rollback runbooks, and a provider comparison exist; a production platform and operating evidence are not yet approved. |
| French localization and legal surfaces | **Planned** | The launch UI is still English and approved privacy, terms, legal notice, support, retention, and subprocessor surfaces are absent. |
| Account export and permanent deletion | **Planned** | Reversible account deactivation and per-terminal-call **Remove call** are implemented, but account-wide export, permanent deletion orchestration, and recording-access audit records are absent. No retention, backup-erasure, or historical-copy-erasure claim is made. |
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
- The account lifecycle has provider-free acceptance evidence only. Its Stripe
  cancellation/Portal and Telnyx disable/release behavior has not been
  certified against real providers, and the required Portal and monitoring
  configuration are external deployment artifacts.
- The optional realtime observer is not a supported customer feature. The
  live-call interface is explicitly local-only Preview UI and makes no
  telephony or realtime mutation.
- Worker isolation is implemented, but its controlled ten-call local/CI evidence
  does not establish cloud scheduling, provider/database saturation, production
  SLOs, alert routing, a recovery drill, or production certification. Issue 16A
  load, monitoring, and recovery drills remain open; realtime remains deferred.
- The application lacks French localization, approved legal pages,
  account-wide export/permanent-deletion orchestration, an approved retention
  and backup-erasure policy, and a complete account menu.
- The repository contains four credential-gated LiveKit behavioral voice
  evaluations, but no completed credentialed run or evidence against an
  approved production-equivalent model. Browser-level accessibility regression
  checks cover keyboard, focus, reduced motion, landmarks, and responsive
  overflow, but formal accessibility conformance, load definitions, frontend
  performance budgets, and completed recovery-drill evidence are absent.

## Production-readiness gates

Opevo is intended for production, but it should not be described as
production-ready until all of these gates have evidence:

- Real-provider certification of the implemented guided activation workflow
- Real Stripe/Telnyx certification of immediate and period-end cancellation,
  active-call drainage, number release, reactivation, and provider-failure
  recovery
- A reviewed Stripe Portal configuration that permits period-end cancellation,
  disables proration, and is pinned through
  `STRIPE_BILLING_PORTAL_CONFIGURATION_ID`
- Production alerts that page on every increment of
  `opevo.account_deactivation.attention` and alert when
  `opevo.account_deactivation.oldest_incomplete_age` exceeds
  `MAX_CALL_DURATION_SECONDS + 900`
- Approved French legal, privacy, recording, retention, and support surfaces
- Auditable account export, permanent deletion, and recording access
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
- Call tags, notes, and richer review workflows
- Account data export and permanent deletion
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

Opevo will begin with the conversation runtime rather than a canvas:

1. Typed flow model and business templates
2. Conversation steps, conditional transitions, fallbacks, and end states
3. Validation, versioning, simulation, and call-path traces
4. Visual node editor after the runtime is proven
5. Reusable subflows and tool/function nodes after the authoring model is stable

Before or alongside the runtime work, revisit the Python backend package
responsibilities so the flow engine, LiveKit adapter, versioning, and durable
traces do not accumulate in central modules. Any reorganization requires its
own current design after the conversation-flow runtime boundary is understood.

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

- [Backend runtime contract](architecture/runtime-contract.md)
- [Integration endpoints](architecture/integration-endpoints.md)
- [Production deployment decision](architecture/production-deployment.md)
- [Staging smoke runbook](runbooks/staging-smoke.md)
- [Local self-service activation](architecture/local-self-service-activation.md)
- [Controlled deployment and account-deactivation recovery](runbooks/deploy.md)
- [Incident response](runbooks/incident-response.md)
- [CI and branch protection](engineering/ci-and-branch-protection.md)
- [Dependency security exceptions](security/dependency-exceptions.md)
