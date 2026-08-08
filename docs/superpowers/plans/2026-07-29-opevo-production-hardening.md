# Opevo Production Hardening and Release Plan

> **Execution:** Audit the completed migration as one product. Preserve the
> approved Opevo visual language; hardening changes may improve semantics,
> resilience, and loading behavior but must not restyle the interface.

**Goal:** Prove that the complete France-first Opevo frontend is accessible,
responsive, production-buildable, backend-integrable, and honest about every
live and Preview capability.

**Architecture:** Server pages remain the authenticated data boundary. Live
mutations continue through typed API clients and server actions. Preview
components remain client-local and may not import backend, provider, billing,
or lifecycle mutation modules. Production validation must fail closed when
required Clerk or API configuration is absent.

---

## Task 1: Audit the Completed Interface

- [x] **Step 1: Run repository-wide static UX scans**

Check production routes and components for unlabeled icon actions, controls
without names, non-semantic click handlers, missing form metadata, broad
transitions, unsafe focus suppression, hard-coded dates/numbers, stale
placeholders, disabled zoom, and unbounded content.

- [x] **Step 2: Audit route landmarks and keyboard paths**

Confirm skip-link/main targeting, one page heading, hierarchical section
headings, dialog/drawer focus trap and restoration, tab semantics, visible
focus, URL-owned live tabs/filters, and reduced-motion behavior.

- [x] **Step 3: Record and classify findings**

Fix actionable production issues. Record deliberate exceptions only when an
existing automated test proves the behavior is bounded and accessible.

---

## Task 2: Apply Accessibility and Interaction Fixes

- [x] **Step 1: Add regression tests for every finding**

Use component tests for semantics and keyboard behavior, and Playwright only
where browser layout, focus, or reduced-motion behavior is material.

- [x] **Step 2: Implement minimal Opevo-preserving corrections**

Keep exact tokens, typography, spacing, borders, shadows, card hierarchy, and
responsive composition. Use semantic elements and scoped transitions.

- [x] **Step 3: Re-run focused tests and inspect affected screenshots**

No screenshot may be updated unless the visual change is intentional and
manually inspected.

---

## Task 3: Audit Preview Isolation, Dead Code, and Bundles

- [x] **Step 1: Prove Preview mutation isolation**

Scan Preview components for API/action imports and retain browser request
observers across assistant, live call, plan comparison, notifications, privacy,
security, and shell notification previews.

- [x] **Step 2: Remove dead production controls and stale content**

Remove unreachable branches, duplicate presentation helpers, fake current
dates, unsupported claims, dead links, and accidental internal metadata.

- [x] **Step 3: Inspect production route bundles**

Use the production build output and source import graph to confirm future-use
Preview modules are loaded only by routes that render them.

---

## Task 4: Dependency and Backend Release Gate

- [x] **Step 1: Audit JavaScript and Python dependencies**

Run the lockfile-aware frontend audit and the repository’s Python dependency
audit. Resolve actionable high-severity findings without broad dependency
upgrades; document valid time-bounded exceptions already governed by the
repository.

- [x] **Step 2: Run all changed-backend checks**

Run Ruff, mypy, the full API test suite, migration verification, and the agent
suite because assistant persistence now spans profile projection and lifecycle
confirmation.

- [x] **Step 3: Run all frontend checks**

Run Biome, TypeScript, all Vitest tests, and the production Next.js build with
CI-safe configuration.

---

## Task 5: Integration and Operations Handoff

- [x] **Step 1: Document runtime configuration**

Document required web/API origins, Clerk keys, local-auth behavior, Stripe and
telephony ownership, France-first defaults, and the database migration.

- [x] **Step 2: Document live and Preview seams**

List every backend-backed route/action and every local-only Preview capability,
including the modules to replace when backend contracts are added.

- [x] **Step 3: Document validation commands and evidence**

Record exact passing test counts, production build routes, browser matrices,
visual baselines, dependency results, and any external deployment prerequisites.

---

## Task 6: Final Release Verification

- [x] **Step 1: Run the immutable Docker lifecycle**

Run landing, activation, dashboard, calls, configuration, deactivation, service
restart, history retention, and fresh-number reactivation in one disposable
stack.

- [x] **Step 2: Verify repository state**

Require a clean worktree, no uncommitted generated files, no accidental
`Opevo_frontend` edits, and a reviewable commit sequence.

- [x] **Step 3: Complete roadmap and handoff**

Mark all five migration phases complete only after every release criterion has
fresh evidence.

---

## Completion Checklist

- [x] Opevo visual tokens and hierarchy remain regression-protected.
- [x] All live features are backend-confirmed and all Preview features are visibly local-only.
- [x] Keyboard, focus, landmarks, labels, live regions, contrast, and reduced motion are verified.
- [x] No dead link, fake success, stale current date, or internal metadata remains in production UI.
- [x] Frontend and changed-backend checks, production build, and full browser lifecycle pass.
- [x] Integration and deployment prerequisites are documented without embedding secrets.
