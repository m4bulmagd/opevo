# Verdict

  Do not start over. Continue building, but do not launch this to paying public customers yet.

  This is a credible pre-production MVP with a sensible architecture and a meaningful test suite. It is not currently production-safe because several weaknesses
  affect money, customer isolation, call durability, secrets, subscription enforcement, and privacy.

  My approximate assessment:

   Area                             Score    Assessment
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Product scope and positioning     8/10    Focused France-first launch is a good constraint
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   High-level architecture           7/10    Sensible API, worker, agent, database, and provider boundaries
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Code organization                 7/10    Generally understandable and testable
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Automated local verification      8/10    179 tests pass; lint and production build pass
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Billing and usage correctness     3/10    Dangerous concurrency and lifecycle gaps
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Security and privacy              2/10    Immediate credential rotation and hardening required
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Reliability and recovery          3/10    Calls and transcripts can be lost or become stuck
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Operations and observability      2/10    No real production deployment or incident foundation
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Product and UX completeness       4/10    Attractive foundation, but important customer workflows are missing
  ───────────────────────────────  ───────  ─────────────────────────────────────────────────────────────────────
   Overall production readiness      4/10    Good MVP, not safe for general production

  I would describe it as:

  > Architecturally promising and worth continuing, but currently suitable only for development and controlled internal testing.

  ## Urgent security action

  While validating the Compose configuration, the resolved docker compose config output expanded active-looking local credentials into command output. Treat those
  credentials as disclosed.

  Rotate all credentials represented in the local environment, including:

  - Stripe and Stripe webhook secrets
  - Clerk and Clerk webhook secrets
  - LiveKit credentials
  - Telnyx credentials
  - Gemini, Speechmatics, ElevenLabs, Mistral, or other AI provider keys
  - S3/MinIO credentials
  - The API-to-agent internal token

  I did not repeat any secret values in this report.

  The three application .env files also have mode 664, meaning they are readable more broadly than they should be and group-writable. Change them to 600, use a
  managed secret store for production, and avoid commands that print fully interpolated Compose configurations.

  I checked Git history for tracked .env files and did not find these local environment files committed. That is good, but rotation is still necessary because of the
  command output exposure.

  # What is good

  ## 1. The high-level system decomposition is sensible

  The split between these responsibilities is healthy:

  - FastAPI for the control plane and customer-facing API
  - LiveKit agent for the real-time voice path
  - PostgreSQL as the durable system of record
  - Redis/ARQ for transient messaging and background work
  - Adapters for Telnyx, Stripe, storage, notifications, recording, and summaries
  - Next.js dashboard as a separate frontend

  That is a much better foundation than a monolithic request handler attempting to manage calls, billing, provisioning, and UI together.

  The voice agent can start without putting FastAPI directly in the audio loop. That is an important latency and availability property.

  ## 2. The launch scope is unusually disciplined

  The documents consistently narrow the initial product toward:

  - France
  - One starter plan
  - One agent per customer
  - Inbound calls
  - A constrained voice pipeline
  - Automatic number provisioning after payment
  - A guided onboarding state

  That restraint is good product engineering. The repository is not trying to support every country, pipeline, call direction, pricing model, and organization
  hierarchy before proving the core use case.

  The README is also reasonably honest that the product remains in staging and still requires provider validation rather than pretending it is finished:
  README.md:165.

  ## 3. There are good defensive ideas already present

  Examples include:

  - Clerk-authenticated routes
  - Per-user repository queries
  - Cross-tenant tests
  - Webhook signature verification
  - Hosted Stripe Checkout and Billing Portal
  - Fresh signed recording URLs rather than permanently public URLs
  - Soft deletion for call history
  - Deterministic background job IDs in some call-finalization paths
  - A Redis lock around finalization
  - Provider interfaces that can be replaced in tests
  - An initial voice greeting that identifies the assistant and mentions recording

  These choices show the system has been designed with production concerns in mind, even where the implementation is incomplete.

  ## 4. The test baseline is solid for an MVP

  Fresh verification produced:

  - API: 127 passed
  - Voice agent: 33 passed
  - Web: 19 passed
  - Total: 179 passing tests
  - Web lint: passed
  - Next.js production build: passed
  - Docker Compose configuration: rendered successfully

  This is a real strength. The tests cover authentication, tenant isolation, onboarding, billing events, provisioning, call finalization, agent wiring, prompt
  construction, and major dashboard surfaces.

  The limitation is that many tests use fakes or SQLite. They prove local behavior, but not production concurrency or real provider compatibility.

  ## 5. The frontend has a good visual foundation

  The dashboard is more polished than a typical technical prototype:

  - Responsive application layout
  - Reusable shadcn-based components
  - Strong server-component bias
  - Parallel data loading in several views
  - Consistent loading and error surfaces
  - Motion wrappers that generally respect reduced-motion preferences
  - A production build that completes successfully

  I would retain this frontend rather than replacing it.

  # What prevents production launch

  ## 1. Billing and minute accounting are not authoritative enough

  This is the most serious business-logic problem.

  Call finalization trusts information supplied by the agent:

  minutes_remaining = payload["minutes_remaining"]
  balance_after = max(0, minutes_remaining - minutes_charged)

  That logic is visible in apps/api/app/services/call_lifecycle_service.py:54.

  The finalization service also accepts the payload’s user_id without verifying that it matches the call’s actual owner. A stolen or reused internal token could
  therefore finalize one call while charging another customer.

  There is no database row lock or atomic balance update. With concurrent calls:

  1. Both calls can receive the same starting balance.
  2. Both can finish using that stale value.
  3. Both can write an apparently valid balance.
  4. Total consumed minutes can exceed the customer’s allowance.

  The usage ledger also lacks sufficient database-enforced idempotency. Duplicate call-completion records should be impossible through a unique constraint such as
  (call_id, event_type).

  What to change:

  - Treat PostgreSQL as the only authority for balance.
  - Look up the call and derive the user from the call.
  - Lock the current balance or subscription row with SELECT ... FOR UPDATE.
  - Calculate the charge inside a database transaction.
  - Add unique constraints for call usage events and transcript sequence numbers.
  - Reject any finalization token whose call and user claims do not match the database.
  - Enforce a maximum call duration or stop a call when the allowed balance is exhausted.

  ## 2. Subscription lifecycle handling is incomplete

  The billing service currently handles only:

  - customer.subscription.created
  - invoice.paid

  See apps/api/app/services/billing_service.py:57.

  It does not properly handle:

  - Subscription cancellation
  - Subscription updates
  - past_due
  - unpaid
  - Payment failures
  - Paused subscriptions
  - Failed renewal requiring customer action
  - Subscription deletion

  This means a customer who cancels or stops paying may remain locally “active,” with their number still routed.

  Stripe explicitly recommends using subscription and invoice webhook state transitions, including payment failures, to control product access: Stripe subscription
  webhook documentation (https://docs.stripe.com/billing/subscriptions/webhooks).

  There is also a fragile definition of “first activation”: the absence of any usage-ledger entry. That can break if another usage event exists before the first paid
  invoice.

  What to change:

  - Model a subscription state machine.
  - Handle all relevant Stripe subscription and invoice events.
  - Disable routing for canceled, unpaid, or sufficiently overdue subscriptions.
  - Make webhook event IDs unique at the database level.
  - Reconcile local subscriptions against Stripe periodically.
  - Use an explicit activation/grant identifier instead of “no usage rows exist.”
  - Restrict accepted price lookup keys to configured products.

  ## 3. Webhook idempotency is vulnerable to races

  The application checks whether an external event has already been recorded, then inserts it. But the database index on provider and external event ID is not unique.

  Two workers receiving the same webhook simultaneously can therefore both pass the check and both process the event.

  That is dangerous for:

  - Stripe minute grants
  - Phone provisioning
  - LiveKit dispatch
  - Recording startup
  - Call finalization

  Make (provider, external_event_id) a database UNIQUE constraint and treat a uniqueness conflict as “already processed.”

  LiveKit events are also not recorded through the same durable webhook-event mechanism, despite documentation suggesting external events are idempotent.

  ## 4. The voice call is not durable while it is happening

  The transcript primarily lives in the agent process’s memory until shutdown/finalization.

  If the agent process crashes or the machine is terminated:

  - The partial transcript can disappear.
  - The call can remain pending.
  - Minutes may never be reconciled.
  - The recording may exist without a finalized call.
  - The customer may never receive a notification.

  Redis Pub/Sub does not solve this because it is ephemeral rather than a durable event log.

  What to change:

  - Persist transcript segments incrementally or place them in a durable stream.
  - Add a call-state machine: created → connected → recording → ending → finalizing → completed/failed.
  - Add a reconciliation worker for stuck calls and orphaned recordings.
  - Query LiveKit/telephony state when a call remains pending beyond a threshold.
  - Make finalization retryable from durable inputs.
  - Add a dead-letter path and operator recovery action.

  ## 5. Sensitive customer content is being logged

  The agent pipeline prints the complete generated prompt and knowledge-base content unconditionally in apps/agent/agent/pipeline_factory.py:184.

  That could expose:

  - Private business instructions
  - Customer knowledge-base content
  - Personal data
  - Prompt injection content
  - Potentially regulated or contract-sensitive information

  Other logs include caller numbers, called numbers, SIP attributes, call IDs, and user IDs.

  Remove the prompt print entirely, disable transcript debugging in production, redact telephone numbers, and introduce a documented structured-logging policy.

  ## 6. Real-time events currently use incompatible identities

  The WebSocket manager registers connections using the Clerk user ID. Call publishers publish using the application’s internal PostgreSQL UUID.

  As a result, real customers would not receive their intended real-time events, even though tests pass because they use matching arbitrary IDs on both sides.

  The dashboard does not currently appear to depend materially on the WebSocket, so the simplest MVP decision may be to remove or disable the real-time path and rely
  on refresh/polling until it is implemented correctly.

  If retained:

  - Resolve Clerk identity to the local user before registering the socket.
  - Publish only with the canonical local identity.
  - Add connection timeout and reconnect/resynchronization semantics.
  - Test with genuinely different Clerk and database IDs.

  ## 7. External side effects and database commits can diverge

  Several flows perform an external operation before committing database state:

  - LiveKit dispatch is created before the call transaction commits: apps/api/app/services/livekit_dispatch_service.py:164.
  - Telnyx may purchase or change a number before local state commits.
  - A provisioning job can be enqueued before the surrounding Stripe transaction commits.

  Failure examples:

  - Telnyx purchases a number, then the database transaction rolls back.
  - The agent joins a room, but the call row is not yet visible.
  - The queue worker starts before the subscription grant commits.
  - A database failure leaves an external dispatch with no local record.

  You cannot make PostgreSQL, Stripe, Telnyx, and LiveKit one ACID transaction. Use:

  - A transactional outbox
  - Explicit operation states
  - Idempotency keys
  - Retry-safe provider calls
  - Periodic reconciliation
  - Compensating actions where appropriate

  ## 8. There are unsafe asynchronous database patterns

  Some services use asyncio.gather to run multiple queries concurrently through the same SQLAlchemy AsyncSession.

  An AsyncSession is not safe for concurrent operations. This may work in fake tests or SQLite but fail under real PostgreSQL traffic with “concurrent operation”
  errors.

  Run these queries sequentially, combine them into a query, or use separate sessions.

  ## 9. Recording retention is documented, not enforced

  The storage service generates a presigned URL but does not first confirm the object exists: apps/api/app/providers/storage/s3.py:74.

  Consequences:

  - An expired or removed recording may still produce a URL that returns 404.
  - The API’s “null after expiry” contract is not reliable.
  - Read paths may create a missing bucket and hide infrastructure misconfiguration.

  The intended 30-day retention is not enforced by repository-controlled infrastructure or a tested lifecycle policy.

  What to change:

  - HEAD/stat the object before presigning.
  - Return null for object-not-found.
  - Never create storage infrastructure on a read path in production.
  - Define bucket encryption and lifecycle rules in infrastructure-as-code.
  - Verify expiry with an automated integration test.
  - Add access logs and explicit deletion workflows.

  ## 10. Privacy and legal readiness are incomplete

  For a France-focused product that answers and may record telephone calls, this is not only a UI issue.

  The repository lacks complete implementations for:

  - Privacy policy
  - Terms
  - Company/legal identity
  - Recording purpose and legal basis
  - Retention information
  - Data-subject access/export/deletion
  - Recording opt-out or non-recording paths
  - Subprocessor documentation
  - Access audit logs
  - Account deletion and data purge
  - A clear second layer of caller information

  CNIL guidance expects callers to be informed about the recording, controller, purpose, legal basis, recipients, retention, rights, and complaint route, often
  through concise oral information followed by a second information layer: CNIL telephone recording guidance
  (https://www.cnil.fr/fr/lenregistrement-des-conversations-telephoniques-afin-detablir-la-preuve-de-la-formation-dun-contrat).

  The assistant’s current greeting is directionally good because it identifies itself as AI and mentions recording, but it is not a complete compliance
  implementation.

  The EU AI Act’s transparency requirements for interactive AI systems are also scheduled to become applicable on 2 August 2026, which is imminent relative to the
  current project date: European Commission AI Act overview (https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai).

  Get qualified French/EU legal review before public launch.

  ## 11. Production operations are largely absent

  The application has a basic liveness endpoint, but not a meaningful readiness endpoint: apps/api/app/routers/health.py:7.

  Missing production capabilities include:

  - Database and Redis readiness checks
  - Metrics
  - Distributed traces
  - Error aggregation
  - Alerting
  - SLOs
  - Queue-depth and stuck-job alerts
  - Deployment manifests or infrastructure-as-code
  - CI/CD
  - Database backups and restore tests
  - Redis security and high availability
  - Recording-storage recovery procedures
  - Incident-response runbooks
  - Dependency and secret scanning
  - Load testing
  - Rollback or canary deployment
  - Provider-outage behavior

  The API container also runs Alembic migrations during startup. Multiple API replicas can race. Migrations should be a separate release/deployment job.

  The current Docker Compose stack is appropriate for local development, not production. It exposes infrastructure ports, uses development-style passwords and
  services, and does not provide a hardened network or secret model.

  ## 12. Voice-agent tests do not evaluate actual behavior

  The agent tests successfully cover factories, event wiring, prompt construction, and error paths. They do not meaningfully test whether the voice assistant:

  - Gives the correct disclosure
  - Stays grounded in the customer’s knowledge
  - Resists prompt injection
  - Handles silence and interruptions
  - Avoids invented answers
  - Deals with provider failures
  - Behaves correctly in French
  - Handles long or adversarial calls

  LiveKit provides a testing framework specifically for behavioral assertions, tool usage, grounding, misuse, and error cases: LiveKit agent testing documentation
  (https://docs.livekit.io/agents/start/testing/).

  This is important because a voice product can be technically online while still behaving badly enough to damage a customer’s business.

  # Frontend and product gaps

  These are less dangerous than billing or data-integrity issues, but they matter before a real launch.

  ## Customer experience

  The France launch is currently presented almost entirely in English, including lang="en" and hardcoded English text and date formatting.

  The dashboard is also missing or incomplete in areas such as:

  - Visible sign-out/account controls
  - Pricing and tax presentation
  - Support/contact entry point
  - Privacy and terms pages
  - Call archive/delete controls, despite backend support
  - Pagination beyond the first 20 calls
  - Inline recording player
  - Structured summary rendering
  - Action items and follow-up display
  - Useful empty transcript handling
  - Clear routing prerequisites
  - Unsaved-change warnings
  - Field length limits and character counts

  The backend stores richer summary data, but the UI mostly presents summary_text. That leaves meaningful product value unused.

  ## Frontend technical issues

  Notable items:

  - Production auth configuration fails open in places when Clerk keys are absent. Production should fail startup/build.
  - Backend fetches do not have explicit timeouts.
  - The sidebar accepts layout props but internally hardcodes its variant/collapse behavior.
  - One layout-control icon button lacks an accessible label.
  - There is no skip-to-content link.
  - Date rendering does not consistently use the customer’s locale and timezone.
  - Eighteen Google font families are instantiated, creating unnecessary frontend weight.
  - The full-screen waveform canvas draws thousands of segments continuously and does not sufficiently account for visibility, device pixel ratio, reduced motion, or
    battery use.

  - There is dead/demo waveform code excluded from the normal formatter.

  The frontend’s strict check currently fails with 11 formatting/import-order violations, even though lint and the production build pass. That means the documented
  full quality gate is not green.

  # Testing and verification gaps

  The passing test suite is valuable, but it does not yet cover the highest-risk production behavior.

  Still needed:

  - Real PostgreSQL concurrency tests
  - Unique-constraint and row-lock tests
  - Redis/ARQ integration tests with an actual worker
  - Stripe webhook replay tests
  - Telnyx staging provisioning tests
  - LiveKit staging call/recording tests
  - Full browser onboarding E2E
  - Accessibility automation
  - Load and soak testing
  - Agent crash/recovery testing
  - Provider outage testing
  - Backup and restore drills
  - Security and dependency scanning

  The dependency audit could not complete in the environment, so dependency vulnerability status remains unknown.

  There is also no CI workflow enforcing the passing tests, build, lint, or formatting checks.

  # Should you continue or start over?

  Continue.

  A rewrite would discard several things that are already valuable:

  - Good service boundaries
  - A usable domain model
  - Provider abstractions
  - A reasonable dashboard
  - Substantial documentation
  - 179 passing tests
  - A focused launch contract
  - Existing onboarding and provisioning logic

  The problems are concentrated in production hardening and a few authoritative domain workflows. They do not require throwing away the whole repository.

  I would selectively redesign these parts:

  1. Billing and usage accounting

     Move all balance decisions into one transactional PostgreSQL-backed module.

  2. Call lifecycle

     Introduce a durable state machine, incremental transcript persistence, and reconciliation.

  3. External operations

     Introduce an outbox/idempotency/reconciliation pattern around Telnyx, LiveKit, Stripe, and queues.

  4. Production configuration

     Fail fast, use managed secrets, separate local and production deployment configuration, and add observability.

  5. Real-time layer

     Either fix identity mapping and resynchronization properly or remove it from the initial launch.

  A restart would only be justified if the intended product has fundamentally changed—for example, if you now want multi-tenant organizations, many agents per
  customer, outbound campaigns, many countries, or a contact-center platform. For the currently documented France-first inbound assistant, the architecture is
  salvageable.

  # Recommended path forward

  ## Phase 0: Immediate containment

  Before further provider testing:

  - Rotate the exposed credentials.
  - Restrict local environment file permissions.
  - Remove full-prompt and transcript logging.
  - Disable debug streams outside local development.
  - Add process-specific production configuration validation.
  - Ensure production cannot start with missing auth or webhook secrets.
  - Add automated secret scanning.

  ## Phase 1: Correctness blockers

  Before accepting money:

  - Implement full Stripe subscription lifecycle handling.
  - Make usage deductions database-authoritative and concurrency-safe.
  - Verify call ownership during finalization.
  - Strengthen dispatch tokens and remove the shared-token fallback.
  - Add unique webhook, ledger, subscription, and transcript constraints.
  - Fix same-session concurrent SQLAlchemy queries.
  - Add maximum call-duration and minute enforcement.
  - Fix or remove real-time delivery.
  - Move enqueue/dispatch actions behind committed state using an outbox.
  - Add stuck-call and provisioning reconciliation.

  ## Phase 2: Reliability and production platform

  Before a closed customer pilot:

  - Persist partial transcripts durably.
  - Test agent crashes and provider outages.
  - Enforce and verify recording lifecycle policies.
  - Add readiness checks, metrics, traces, error reporting, and alerts.
  - Add production deployment infrastructure.
  - Add database backups and perform a restore.
  - Separate migration execution from API startup.
  - Run integration tests against PostgreSQL, Redis, Stripe test mode, Telnyx staging, and LiveKit Cloud.
  - Add LiveKit behavioral evaluations.

  ## Phase 3: Product and compliance

  Before broader release:

  - Localize the complete experience to French.
  - Add privacy, terms, legal identity, support, and pricing information.
  - Finalize recording disclosure and opt-out behavior with counsel.
  - Implement export, deletion, retention, and access auditing.
  - Add account/sign-out controls.
  - Add call pagination, archive controls, audio playback, and structured summaries.
  - Improve form validation and routing guidance.
  - Complete accessibility and mobile performance work.

  # My launch criteria

  I would not call this production-ready until all of these are true:

  - Canceling or failing payment reliably disables service.
  - Replaying any webhook cannot create duplicate minutes, numbers, calls, or charges.
  - Two concurrent calls cannot overspend a balance.
  - An agent crash preserves partial call evidence and is automatically reconciled.
  - Dispatch cannot finalize or charge another customer’s call.
  - Storage expiry and deletion are actually enforced and tested.
  - Three or more complete staging journeys succeed with new users and no manual database edits.
  - Database restoration has been demonstrated.
  - Alerts exist for failed webhooks, stuck calls, provisioning failures, and queue backlog.
  - No high/critical security findings remain.
  - French privacy and recording behavior has received legal review.
  - CI blocks merging when tests, build, formatting, or security checks fail.

  After that, I would launch first to roughly 5–10 design partners with close monitoring, not immediately to an unrestricted public audience.

  Depending on team size, this looks like several focused engineering sprints rather than a small polishing pass. The core is good enough to justify that investment.

  No product files were intentionally changed during the audit. The workspace remains with the pre-existing deletion/modification and untracked files that were
  already present when I began.