# Opevo Self-Service Production Program Roadmap

**Approved design:** `docs/superpowers/specs/2026-07-16-self-service-production-launch-design.md`

**Program goal:** Release Opevo as a production-grade, self-service missed-call receptionist for English-speaking SMEs and independent professionals in France, with a Opevo-provided French number, conditional call forwarding, useful post-call outcomes, and minimal Opevo support.

**Delivery model:** Build launch-focused vertical slices. Each slice must leave the product in a deployable, testable state and pass its exit gate before the next dependent slice begins.

## Fixed Launch Decisions

- Market: France.
- Hosting region: AWS Ireland (`eu-west-1`).
- Product language: English at launch; French is post-launch.
- Core job: answer missed calls and produce a useful call record.
- Telephony: one Opevo-provided French number per account, reached through conditional forwarding from the customer's existing number.
- Account model: one owner, one business, one receptionist, and one Opevo number per account.
- Call result: original audio, summary, and structured outcomes stay inside Opevo.
- Call control: the owner can permanently delete a call and its owned artifacts.
- Retention: user-triggered deletion is launch scope; automatic 30-day retention follows unless legal review makes it a launch blocker.
- Conversation fallback: ask one clarification, then acknowledge uncertainty and collect name, callback number, reason, urgency, and preferred callback time without promising an action or response time.
- Appointment booking: post-launch.
- Release: internal use, then 5–10 customers, then 25–50, then public self-service.

## Program Map

| Order | Implementation plan | Primary outcome | Depends on | Exit gate |
|---:|---|---|---|---|
| 1 | `2026-07-16-runtime-correctness-and-voice-safety.md` | Opevo has one authoritative readiness decision, safe bounded prompt inputs, reliable production ordering, and a tested receptionist fallback | Approved design | Gate 1: safe authoritative state |
| 2 | `2026-07-16-aws-ireland-staging-foundation.md` | A reproducible, observable, recoverable staging platform exists in `eu-west-1` | Plan 1 interfaces | Staging deploy, restore, rollback, and provider smoke tests pass |
| 3 | `2026-07-16-self-service-activation.md` | A customer can configure the business, provision a number, receive carrier-specific forwarding help, verify forwarding, and go live without Opevo support | Plans 1–2 | Gate 2: support-free activation |
| 4 | `2026-07-16-useful-call-review-and-deletion.md` | Every completed call exposes audio, an obvious summary, structured outcomes, and durable deletion | Plans 1–3 | Gate 3: useful and controllable calls |
| 5 | `2026-07-16-production-hardening-and-certification.md` | Security, privacy, operations, accessibility, performance, and end-to-end release evidence are complete | Plans 1–4 | Gates 4–5: operable and certifiable platform |
| 6 | `2026-07-16-progressive-release.md` | Opevo moves through internal, 5–10, 25–50, and public cohorts with measured stop/go decisions | Plans 1–5 | Gate 6: public self-service |

Only Plan 1 is executable from this roadmap today. Write each later detailed plan after the preceding interface gate passes so it reflects the implemented contracts rather than stale assumptions.

## Slice 1: Runtime Correctness and Voice Safety

The first slice removes contradictions that can tell the customer Opevo is live when dispatch or telephony will reject the call. It also makes customer-authored prompt content bounded and subordinate to mandatory Opevo behavior.

Deliverables:

- One pure customer-readiness policy used by onboarding, agent enablement, phone routing, webhook admission, and durable dispatch.
- Machine-readable readiness blockers exposed through onboarding and enablement APIs.
- Production startup fails when Telnyx ordering is disabled.
- Customer-controlled agent content has consistent size and normalization limits at API, dispatch, and worker boundaries.
- The first spoken message clearly discloses that the receptionist is AI and that the call is recorded.
- The receptionist follows the approved low-confidence message-taking flow and never treats customer content as higher-priority instructions.
- Deterministic tests and credential-gated LiveKit behavior evaluations cover the safety contract.
- Rejected Clerk tokens cannot place unverified claims or exception text in logs.

Exit evidence:

- Every readiness consumer passes the same policy matrix.
- Zero balance, expired period, incomplete setup, disabled agent, inactive provider projection, or number mismatch cannot be reported as live or dispatched.
- Production configuration tests reject disabled Telnyx ordering.
- Prompt-injection, unknown-answer, callback-capture, and no-appointment-promise behaviors pass.
- API, agent, and web affected suites pass.

## Slice 2: AWS Ireland Staging Foundation

Write the detailed plan after Slice 1. It must preserve the readiness interfaces and cover:

- AWS account/environment boundaries and least-privilege deployment roles.
- VPC, private database/cache access, TLS ingress, DNS, and certificate management in `eu-west-1`.
- PostgreSQL, Redis, S3 recording storage, application services, worker, agent, and web deployment topology.
- Secrets management, image provenance, database migrations, health/readiness checks, and zero-secret CI.
- Encryption, log retention, metrics, traces, alarms, dashboards, and provider correlation IDs.
- PostgreSQL backup restore, object recovery expectations, rollback, provider outage, and incident runbooks.
- Staging Telnyx, LiveKit, Stripe, Clerk, email/notification, and storage smoke tests.

Exit evidence:

- A clean environment can be created from versioned infrastructure.
- A versioned release can be deployed, rolled back, and redeployed.
- A database backup is restored into an isolated target and verified.
- An inbound staging call completes through audio, transcript, summary, and playback.
- Alerts fire for deliberately induced API, worker, and provider failures.

## Slice 3: Self-Service Activation

Write the detailed plan after Slice 2. It must cover:

- Persistent business profile: owner name, business name/type, public description, hours, timezone, existing business number, carrier, receptionist name, greeting context, FAQs, and escalation instructions.
- Automatic phone-number carrier lookup with customer confirmation and manual correction.
- Explicit onboarding state machine with retryable and terminal failures.
- Number provisioning only after the required profile and paid access are valid.
- Carrier-specific conditional-forwarding instructions with generic fallback guidance.
- A controlled outbound verification call as the only launch outbound-call exception.
- Activation checklist, resumable progress, verification result, and clear recovery actions.
- Quietly premium, responsive, accessible UI using the existing Next.js, Tailwind, and shadcn foundation.
- A documented template/component evaluation before visual implementation, including URLs, licenses, compatibility, and adoption cost.

Exit evidence:

- A new customer completes signup through a successful forwarded test call without Opevo intervention.
- Refreshing or signing out never loses onboarding progress.
- Unsupported carrier, provisioning delay, and failed verification all show a self-service recovery path.
- No account can provision or activate more than one launch number or receptionist.

## Slice 4: Useful Call Review and Deletion

Write the detailed plan after Slice 3. It must cover:

- Call-list and call-detail information architecture optimized for missed-call follow-up.
- Inline recording playback from private, short-lived authenticated URLs.
- Stable summary and structured outcomes: caller identity, callback details, reason, urgency, preferred callback time, unresolved questions, and follow-up status.
- Explicit `unknown` values instead of fabricated extraction.
- Summary/extraction versioning and retry states.
- Real deletion state machine covering database records, transcript, recording object, derived summary/outcomes, and idempotent retry.
- Audit events without transcript, prompt, or phone-number leakage.

Exit evidence:

- The owner can understand the call outcome without reading the transcript.
- Audio is playable only by the owning account and links expire.
- Repeated delete requests converge on the same deleted state.
- Deleted owned artifacts are absent from database and object storage checks.
- Failure and retry states remain visible and actionable.

## Slice 5: Production Hardening and Certification

Write the detailed plan after Slice 4. It must cover:

- Threat model and authorization matrix for every customer and agent endpoint.
- Dependency, container, secret, migration, and static-analysis gates.
- French/EU counsel review of recording disclosure, privacy notice, terms, processor/subprocessor disclosures, deletion, and automatic retention timing.
- A launch decision on whether 30-day automatic retention is mandatory; implement it before launch if counsel requires it.
- Accessibility, keyboard, focus, screen-reader, responsive, and reduced-motion certification.
- Capacity, soak, concurrency, rate-limit, abuse, degraded-provider, and cost tests.
- Support-free product recovery paths, operator dashboards, alerts, runbooks, and ownership.
- Three consecutive clean golden-journey staging runs and failure drills.

Exit evidence:

- Gates 4 and 5 in the approved design are signed off with links to evidence.
- No severity-one or severity-two launch defect remains open.
- Restore, rollback, provider outage, stuck job, failed provisioning, and deletion retry drills pass.
- Counsel-approved copy and privacy behavior are deployed.

## Slice 6: Progressive Release

Write the detailed plan after Slice 5. It must define cohort criteria and explicit stop/go thresholds for:

1. Internal Opevo traffic.
2. Five to ten design-partner customers.
3. Twenty-five to fifty controlled customers.
4. Public self-service availability.

Every cohort review must include:

- Signup-to-live completion and median completion time.
- Human support interventions per activated account.
- Forwarding verification success by carrier.
- Inbound answer, dispatch, recording, summary, and playback success rates.
- Unknown-answer and false-promise review samples.
- Deletion success and retry latency.
- Provider cost, gross margin risk, incident count, and customer-reported trust issues.

Promotion stops when any safety, privacy, data-loss, false-promise, or cross-tenant issue is unresolved, regardless of growth metrics.

## Recommended Release Thresholds

These are the initial measurable stop/go thresholds for later detailed plans. They are intentionally strict on safety and data loss and moderately conservative on early conversion. Revisit them only through a documented product decision supported by cohort evidence.

| Measure | 5–10 customer cohort | 25–50 customer cohort | Public-release minimum |
|---|---:|---:|---:|
| Observation window | At least 14 days and 50 eligible calls | At least 30 days and 250 eligible calls | The 25–50 cohort window passes in full |
| Signup to required profile completion | At least 75% | At least 80% | At least 80% |
| Paid account to number ready | 95% within 30 minutes | 97% within 30 minutes | 97% within 30 minutes and 99% within 24 hours |
| Activation completed without Opevo staff action | At least 80% | At least 90% | At least 90% |
| Forwarding test passed within two attempts | At least 85% | At least 90% | At least 90%, with no supported carrier below 80% |
| Eligible inbound calls admitted and dispatched | At least 99.0% | At least 99.5% | At least 99.5% |
| Dispatched calls produce playable audio and outcomes | At least 98.0% | At least 99.0% | At least 99.0% |
| Call end to playable audio and outcomes | 95% within 10 minutes | 95% within 5 minutes | 95% within 5 minutes and 99% within 30 minutes |
| Required outcome fields represented as value or explicit `unknown` | At least 95% | At least 97% | At least 97% |
| Fabricated answer, booking, transfer, or completed action | Zero severity-one or severity-two cases | Zero severity-one or severity-two cases | Zero unresolved cases of any severity in the observation window |
| User-requested deletion | 99% within 1 hour; 100% within 24 hours | 99% within 30 minutes; 100% within 24 hours | 99% within 30 minutes; no unresolved deletion older than 24 hours |
| Terminal provisioning/activation requiring staff repair | Below 10% | Below 5% | Below 3% |
| Support contacts per activated account | At most 0.75 | At most 0.30 | At most 0.20 during the final observation window |
| Backup restore and release rollback drills | 100% of scheduled drills pass | 100% pass | Most recent restore and rollback evidence is less than 30 days old |

Measure provider-caused and Opevo-caused failures separately, but do not exclude provider failures from the customer-facing availability thresholds. Exclude customer hang-ups before the disclosure completes, deliberate internal fault-injection calls, and calls rejected for correctly enforced ineligibility; keep those events in a separate safety report.

## Cross-Program Rules

- PostgreSQL remains authoritative for customer state, access, usage, call lifecycle, and deletion progress.
- Provider state is a projection reconciled through durable outbox work; the UI never equates a requested state with a confirmed provider state.
- Every behavior change starts with a failing test and ends with affected full-suite verification.
- Customer-authored text is data, never trusted policy.
- Logs exclude audio, transcripts, summaries, prompts, knowledge-base text, full phone numbers, tokens, signatures, credentials, and raw provider exception messages.
- No destructive data migration or provider change is deployed without a tested retry and rollback/recovery path.
- Legal decisions are recorded as approved product behavior before engineering encodes them.
- Scope stays focused on missed-call answering; booking, French conversations, multi-user accounts, complex call flows, and general outbound calling remain post-launch.

## Program Completion

Opevo is production-ready only when all six slices pass. “Deployed,” “works in a demo,” or “handles a happy-path call” is not sufficient. Public release requires a new customer to activate without Opevo support, a forwarded call to be answered safely, a useful and playable call result to appear, deletion to complete durably, and operators to detect and recover failures using documented procedures.
