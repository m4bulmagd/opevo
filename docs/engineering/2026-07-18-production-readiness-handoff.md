# Presvo production-readiness handoff

## Purpose

This document is the durable continuation point for the local-first Presvo
production-readiness program completed on 2026-07-18. It exists so a later
coding session can continue without relying on ignored worktree notes or prior
conversation history.

At this checkpoint the implementation is production-oriented and locally
verified, but it is not production-certified. No cloud deployment, live
provider mutation, real credential use, or provider-account change was part of
this program.

## Git checkpoint

- Intended continuation branch: `feat/presvo-production-readiness`
- Base branch: `main`
- Base commit: `c5a5994cb1162d7f3e600cf6fc25ab54cc01e430`
- Last implementation commit before this handoff:
  `64ceb309232a7777326dfdd5a3a1d7d4291c3d67`
- Final implementation review: `SPEC PASS`, `QUALITY APPROVED`, ready to merge,
  with no Critical, Important, or Minor findings.
- User checkpoint: pause implementation while the user performs manual tests.

The handoff document itself is committed after the implementation checkpoint,
so use `git log -1` to obtain the current branch head.

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
- Automatic 30-day retention remains future work and is deliberately not
  enabled.
- Appointment booking and configurable conversation flows remain later phases.
- Ireland is the preferred possible future hosting region, not a customer
  number country. No hosting decision was executed.
- Customer support should remain minimal or unnecessary through self-service
  recovery and clear next actions.

## Completed program

The following four implementation plans are complete:

1. `docs/superpowers/plans/2026-07-17-activation-domain-and-profile-api.md`
   - persisted activation/profile domain;
   - structured business hours and bounded profile contracts;
   - carrier confirmation and runtime projection;
   - canonical activation snapshot and audit events;
   - authenticated, default-off API surface.
2. `docs/superpowers/plans/2026-07-17-consent-provisioning-and-local-providers.md`
   - payment separated from explicit provisioning consent;
   - idempotent, durable French-number provisioning;
   - fake local carrier, billing, and telephony providers;
   - fail-closed production provider modes;
   - complete provider-free local API journey.
3. `docs/superpowers/plans/2026-07-17-forwarding-verification-and-go-live.md`
   - versioned conditional-forwarding guidance for Orange, SFR, Free,
     Bouygues Telecom, and Other;
   - exact ten-minute verification windows;
   - fixed-message, no-record/no-transcript/no-summary/no-usage verification;
   - scoped verification credentials and replay protection;
   - explicit go-live with central readiness and durable provider projection;
   - safe compensation for routing races.
4. `docs/superpowers/plans/2026-07-17-activation-web-journey-and-dashboard-handoff.md`
   - server-authenticated Next.js boundary with local/Clerk modes;
   - resumable five-milestone activation UI;
   - robust profile autosave and carrier fallback;
   - payment, provisioning consent, forwarding, verification, and launch UI;
   - dashboard answering/paused state;
   - structured call summary, outcome, action, follow-up, original-audio, and
     terminal-call removal UI;
   - disposable provider-free Playwright activation proof;
   - full-service restart/resume proof with preserved volumes.

Current capability and limitation wording is maintained in
`docs/PROJECT_STATUS.md`. The local operating contract is in
`docs/architecture/local-self-service-activation.md`.

## Final verification evidence

- API Ruff: passed.
- API mypy: 141 source files passed.
- API SQLite suite: 1,279 passed; 69 PostgreSQL-only tests skipped.
- Complete isolated PostgreSQL 17/Redis 7 API suite: 1,348 passed, 0 skipped.
- Agent Ruff and mypy: passed.
- Agent deterministic tests: 250 passed.
- Four credential-gated LiveKit behavioral evaluations skipped intentionally;
  they require `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and an explicitly chosen
  `LIVEKIT_EVAL_MODEL`. A skip is not provider certification.
- Web Biome: 145 files passed.
- Web TypeScript: passed.
- Web Vitest: 228 passed.
- Deployment/readiness regressions: 90 passed.
- Next.js 16 production build: passed; 9/9 static pages generated.
- Provider-free browser activation: 1/1 passed.
- Full local service restart and active-dashboard resume: 1/1 passed.
- Disposable Docker containers, volumes, and networks were removed and verified
  absent after each acceptance run.
- Shell syntax, Playwright discovery, safety scans, and `git diff --check`
  passed.

The only recurring warning was the known upstream Starlette/httpx deprecation
warning. Sandboxed Python/Next worker stalls were not counted; the exact commands
were rerun outside the process sandbox and completed.

## Remaining production blockers

These are not implemented or not evidenced yet:

1. Close the recording start/delete race. While an egress start is still in
   provider I/O, no provider ID is durable. If deletion/tombstoning wins and a
   late best-effort stop fails, Presvo cannot durably record a pending stop.
   Persisted egress IDs are already proven non-running before content deletion.
2. Account-wide export and deletion orchestration, recording-access audit, and
   complete account/session controls.
3. Qualified French/EU legal, privacy, recording-disclosure, retention,
   subprocessor, and minimal-support approval/surfaces.
4. Approved automatic retention behavior. The old local automatic expiration
   configuration was removed intentionally.
5. Accessibility end-to-end tests, frontend performance budgets, load and
   concurrency definitions, provider-outage tests, recovery drills, and a
   demonstrated backup restore.
6. Credentialed behavioral evaluation evidence against an approved
   production-equivalent model.
7. Fresh multi-customer Clerk/Stripe/Telnyx/LiveKit staging certification and
   real Orange/SFR/Free/Bouygues/Other forwarding evidence.
8. Approved Ireland deployment, DNS/TLS, secret management, monitoring,
   rollback, restore, and controlled-beta operating evidence.
9. The optional realtime observer remains unsupported and has a documented
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

After the user completes manual testing and explicitly asks to continue, design
the narrow recording-egress start/pending-stop synchronization fix first. Keep
it independent from account deletion and other compliance work so its state
machine, retry behavior, and failure recovery can be reviewed and tested in
isolation.

Do not start that implementation from this handoff alone. First collect the
user's manual-test results and confirm that this remains the preferred next
unit.

## Resume procedure for a new coding session

1. Check out `feat/presvo-production-readiness` and verify a clean worktree.
2. Read this handoff, `README.md`, `docs/PROJECT_STATUS.md`, and
   `docs/architecture/local-self-service-activation.md` completely.
3. Inspect recent commits from `64ceb30` through branch head.
4. Ask for the user's manual-test results before changing code.
5. If a defect was found, reproduce it and use the repository's test-first and
   systematic-debugging workflows.
6. If manual tests pass and the user approves the recommended next unit, create
   a focused design and implementation plan before editing.
7. Preserve the boundary: local development and test infrastructure are
   authorized; provider accounts, cloud resources, deployments, real
   credentials, and external publishing require separate approval.

Useful local checks after checkout:

```bash
git status --short --branch
git log --oneline --decorate -10
cd apps/web && npm run check && npm run typecheck && npm run test:ci
```

The complete disposable acceptance journey is available when needed:

```bash
bash scripts/run-local-e2e.sh
```

It uses only local identity and fake product providers, but it does require the
local Docker daemon and Chromium.
