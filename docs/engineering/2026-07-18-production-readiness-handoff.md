# Presvo production-readiness handoff

## Purpose

This document is the durable continuation point for Presvo's local-first
production-readiness work through the recording-egress implementation completed
on 2026-07-20. It lets a later coding session continue without relying on
ignored worktree notes or prior conversation history.

Presvo is production-oriented and locally verified, but it is not
production-certified. This work did not deploy anything, contact or mutate live
providers, use real credentials, change provider accounts, push, or publish
externally.

## Git checkpoint

- Implementation branch before local integration:
  `feat/durable-recording-reconciliation`, based on local `main`.
- Approved recording plan/base: `8b2c0e6`.
- Complete inclusive recording implementation range: `c6bf2bb^..e911143`.
- Task 7 race, privacy, migration, and observability hardening range:
  `a774dad..e911143`.
- The final documentation/status commit follows `e911143` on that branch.
- The earlier production-readiness branch was merged into local `main` after
  the user's manual test passed.

Use `git status --short --branch` and `git log -1` to confirm the current
checkout. Nothing at this checkpoint was pushed or deployed.

## Product decisions that remain authoritative

- Brand: Presvo only.
- Launch customer: SMEs and individual professionals.
- Initial market and phone-number country: France.
- Initial interface and receptionist language: English; French comes later.
- Initial job: answer missed calls and make the summary, outcome, follow-up, and
  original audio obvious.
- Existing business numbers reach Presvo through conditional forwarding for
  unanswered, busy, and unreachable calls. Unconditional forwarding is not the
  default journey.
- Onboarding collects owner, business, receptionist, existing-number, and
  carrier information.
- Payment establishes eligibility only. A separate explicit confirmation is
  required before number provisioning.
- Provisioning, forwarding verification, and go-live are separate durable
  decisions.
- Original audio remains available until owner removal or a future approved
  retention policy.
- Per-terminal-call removal is implemented. Account-wide export/deletion is
  not.
- Automatic 30-day retention remains future work and is deliberately disabled.
- Conversation flows come before appointment booking. The product reference is
  Retell AI's structured flows, not Recall.ai.
- Ireland is the preferred possible future hosting region, not a customer
  number country. No hosting decision was executed.
- Customer support should remain minimal or unnecessary through self-service
  recovery and clear next actions.

## Completed program

The activation work remains complete:

1. `docs/superpowers/plans/2026-07-17-activation-domain-and-profile-api.md`
   established the persisted activation/profile domain, bounded contracts,
   carrier confirmation, activation snapshot, audit events, and authenticated
   default-off API.
2. `docs/superpowers/plans/2026-07-17-consent-provisioning-and-local-providers.md`
   separated payment from explicit provisioning consent and added durable
   French-number provisioning, local providers, fail-closed real-provider
   modes, and the provider-free local journey.
3. `docs/superpowers/plans/2026-07-17-forwarding-verification-and-go-live.md`
   added carrier-aware conditional-forwarding guidance, the bounded test-call
   window, scoped verification, explicit go-live, and safe routing
   compensation.
4. `docs/superpowers/plans/2026-07-17-activation-web-journey-and-dashboard-handoff.md`
   added the resumable five-milestone UI, active dashboard, structured call
   review, owner removal, and disposable browser/restart proof.

The durable recording work in
`docs/superpowers/plans/2026-07-19-durable-recording-egress-synchronization.md`
is also implemented:

- one private database recording operation and reference-only start intent
  commit before recording-start provider I/O;
- every terminal transition requests reconciliation even without a known
  provider egress ID;
- `recording.reconcile` is the only active recording outbox topic, with
  aggregate `recording-egress-operation` and payload containing only
  `operation_id`;
- signed egress webhooks persist sanitized facts and wake reconciliation only
  after commit, with no provider or storage I/O in the webhook;
- terminal owner removal atomically purges and hides customer content and
  returns `204` without waiting for LiveKit or storage; when recording cleanup
  metadata exists, it also records stop/delete intent and reference-only work;
- asynchronous cleanup is idempotent and non-exhausting, including late,
  uncertain, conflicting, restored, and legacy recording identities;
- the resolved start/delete race, PostgreSQL concurrency behavior, migration
  round trip, privacy bounds, readiness contracts, and low-cardinality signals
  have focused test coverage.

Current capability and limitation wording is maintained in
`docs/PROJECT_STATUS.md`. The active recording contract is in
`docs/superpowers/specs/2026-07-19-recording-egress-synchronization-design.md`.

## Current verification evidence

Fresh local evidence completed through this documentation refresh:

- API Ruff: clean.
- API mypy: clean.
- Focused recording/readiness regression gate: 475 passed, 33 skipped, 1
  upstream Starlette/httpx deprecation warning.
- Authoritative Task 7 PostgreSQL/Redis infrastructure gate: 30 passed, 0
  skipped.
- Provider-free full API suite: 1,718 passed, 87 skipped, 1 upstream
  Starlette/httpx deprecation warning.
- Complete isolated PostgreSQL 17/Redis 7 API suite: 1,805 passed, 0 skipped, 1
  upstream Starlette/httpx deprecation warning.
- Agent lock, Ruff, and mypy checks: clean; deterministic tests: 250 passed, 4
  credentialed LiveKit evaluations deselected.
- Web checks used Node 22.23.1: Biome checked 145 files, TypeScript passed, and
  Vitest passed 228 tests. The exact default `npm run build` Turbopack
  production gate compiled, completed TypeScript, and generated 9/9 static
  pages in the clean normal checkout after `git diff --exit-code main..HEAD --
  apps/web` proved its tracked web source identical. The isolated worktree's
  out-of-root `node_modules` symlink was not used as build evidence.
- Disposable provider-free browser activation: 1 passed. Full-service restart
  and persisted active-dashboard resume: 1 passed.
- Shell syntax, Playwright discovery, stale-contract scans, and
  `git diff --check`: clean. Both disposable Compose projects were removed and
  their containers, networks, and volumes were verified absent.

These local gates do not constitute cloud, legal, behavioral-model, carrier, or
real-provider certification.

Four agent behavioral evaluations require `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, and an explicit `LIVEKIT_EVAL_MODEL` selection.
`LIVEKIT_EVAL_MODEL` chooses the model used only by those opt-in evaluation
tests. It is required only when explicitly running those tests; it is not used
by the API or worker runtime and is not required to run or deploy Presvo. A
skipped or unrun credentialed evaluation is not provider certification.

## Remaining production blockers

These eight items are not implemented or not evidenced yet:

1. Account-wide export and deletion orchestration, recording-access audit, and
   complete account/session controls.
2. Qualified French/EU legal, privacy, recording-disclosure, retention,
   subprocessor, and minimal-support approval and surfaces.
3. Approved automatic retention behavior. The old local automatic expiration
   configuration was removed intentionally.
4. Accessibility end-to-end tests, frontend performance budgets, load and
   concurrency definitions, provider-outage tests, recovery drills, and a
   demonstrated backup restore.
5. Credentialed behavioral evaluation evidence against an approved
   production-equivalent model.
6. Fresh multi-customer Clerk/Stripe/Telnyx/LiveKit staging certification and
   real Orange/SFR/Free/Bouygues/Other forwarding evidence.
7. Approved Ireland deployment, DNS/TLS, secret management, monitoring,
   rollback, restore, and controlled-beta operating evidence.
8. The optional realtime observer remains unsupported and has a documented
   identity-key mismatch. Private push delivery is also incomplete.

Do not describe Presvo as production-ready until the applicable gates in
`docs/PROJECT_STATUS.md` have evidence.

## Planned product phases after blockers

1. Customer workflow completion: call pagination, search, tags, notes, richer
   account controls, export/deletion, and later French localization.
2. Conversation-flow runtime: typed flows, business templates, transitions,
   fallbacks, validation, versioning, simulation, and call-path traces. Build a
   visual node editor only after the runtime is proven.
3. Appointment booking: calendar availability, booking, confirmation,
   rescheduling, and CRM integration.
4. Advanced capabilities: live monitoring/intervention, human transfer, tools,
   outbound calls, mobile, multiple numbers/plans, and additional countries.

## Recommended next implementation unit

Implement auditable account-wide export and deletion orchestration, including
safe drainage of private recording cleanup before any hard-delete boundary.
Keep legal approval, retention-policy activation, provider certification, and
cloud deployment as separate workstreams so none is implied by the local data
workflow.

Before editing product code, write and review the account-level contract. It
must define export scope, deletion states, failure recovery, audit evidence,
active-call behavior, private cleanup drainage, and the boundary for backups or
historical copies without claiming synchronous erasure.

## Resume procedure for a new coding session

1. Check out the latest local branch and verify a clean worktree.
2. Read this handoff, `README.md`, `docs/PROJECT_STATUS.md`, and
   `docs/architecture/integration-endpoints.md` completely.
3. Inspect the recording implementation from `c6bf2bb` through `e911143` and
   the documentation commit that follows it.
4. Read
   `docs/superpowers/specs/2026-07-19-recording-egress-synchronization-design.md`,
   `docs/superpowers/plans/2026-07-19-durable-recording-egress-synchronization.md`,
   and the root `CONTEXT.md`.
5. Confirm the documentation commit and local merge preserve the fresh evidence
   above; do not copy older counts into later completion claims.
6. For the recommended next unit, create and review a focused design and
   test-first implementation plan before editing product code.
7. If a defect is found, reproduce it and use the repository's test-first and
   systematic-debugging workflows.
8. Preserve the boundary: local development and test infrastructure are
   authorized; provider accounts, cloud resources, deployments, real
   credentials, external publishing, and pushes require separate approval.

Useful local checks after checkout:

```bash
git status --short --branch
git log --oneline --decorate -10
cd apps/web && npm run check && npm run typecheck && npm run test:ci
```

The disposable local acceptance journey is available when needed:

```bash
bash scripts/run-local-e2e.sh
```

It uses only local identity and fake product providers, but it requires the
local Docker daemon and Chromium.
