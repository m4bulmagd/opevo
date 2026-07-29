# Presvo Configuration, Billing, and Account Implementation Plan

> **Execution:** Follow the repository TDD workflow task by task. Keep
> `Presvo_frontend` read-only and adapt its presentation into `apps/web`.

**Goal:** Finish the authenticated Presvo experience by migrating assistant
configuration, billing, and account surfaces while keeping every live mutation
backend-confirmed and every unsupported extension visibly local-only.

**Architecture:** Existing server pages remain the data boundary. Live assistant,
billing, and lifecycle actions keep their typed API clients and server actions.
Client components own only drafts, accessibility state, unsaved-change guards,
and explicit Preview simulations. No Preview component imports production API
clients, server actions, billing, telephony, or account lifecycle modules.

**Visual source:** `Presvo_frontend/src/routes/assistant.tsx`,
`Presvo_frontend/src/routes/billing.tsx`,
`Presvo_frontend/src/routes/settings.tsx`, and their assistant/billing common
components.

**Product truth:**

- France-first launch and `Europe/Paris` formatting remain unchanged.
- Only `starter` can be purchased through the live checkout action.
- Existing Stripe Checkout and Customer Portal links are the live invoice and
  payment-management boundary; do not invent invoice amounts or IDs.
- The saved agent contract remains the fixed `agent_name`, `owner_context`,
  `system_prompt`, `knowledge_base`, `pipeline_mode`, and `is_enabled` payload.
- Account deactivation and reactivation keep their exact confirmation and
  lifecycle semantics.
- Advanced personality, response style, voice, language, speed, provider,
  transfer, plan-comparison, notification, privacy, and security controls are
  Preview until corresponding backend contracts exist.

---

## Task 1: Record the Post-Phase-3 Contract Inventory

- [x] **Step 1: Confirm live route boundaries**

Inventory:

- `/dashboard/agent`: `getAgentConfigForRequest`, `getAccount`,
  `saveAgentSettingsAction`;
- `/dashboard/billing`: `getSubscription`, `getUsageSnapshot`,
  `getUsageLedger`, Checkout and Portal actions;
- `/dashboard/account`: `getAccount`, deactivation, reactivation;
- Clerk/session and theme controls remain in the workspace shell.

- [x] **Step 2: Confirm unsupported template behavior**

Classify advanced assistant settings, voice playback, test assistant, extra
plans, notification preferences, recording retention, password, and MFA as
Preview.

- [x] **Step 3: Commit this plan**

```bash
git commit -m "docs: plan Presvo configuration and account migration"
```

---

## Task 2: Add Shared Dirty-State and Preview Foundations

**Files:**

- Create: `apps/web/src/components/forms/unsaved-changes-bar.tsx`
- Create: `apps/web/src/hooks/use-unsaved-changes-guard.ts`
- Create: `apps/web/tests/components/unsaved-changes.test.tsx`

- [ ] **Step 1: Write failing tests**

Cover:

- no bar or navigation warning when clean;
- sticky Presvo save bar when dirty;
- discard callback and pending lockout;
- `beforeunload` warning while dirty;
- same-origin anchor confirmation while dirty;
- baseline reset after a confirmed save.

- [ ] **Step 2: Implement the shared primitives**

Use the template's compact sticky card, exact semantic tokens, minimum 44px
actions, a polite status region, native confirmation for route departure, and
reduced-motion-safe feedback.

- [ ] **Step 3: Verify and commit**

```bash
cd apps/web
npm run test:ci -- tests/components/unsaved-changes.test.tsx
npm run check
npm run typecheck
git commit -m "feat(web): add Presvo unsaved-change guard"
```

---

## Task 3: Port Live Assistant Configuration

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Modify: `apps/web/src/components/agent/agent-settings-form.tsx`
- Modify: `apps/web/src/components/agent/agent-runtime-card.tsx`
- Modify: `apps/web/tests/app/agent-page.test.tsx`

- [ ] **Step 1: Add failing behavior and hierarchy tests**

Require:

- one `Assistant` page heading with the configured agent name as context;
- the Presvo runtime banner and horizontal tab hierarchy;
- live General, Instructions, and Knowledge controls;
- no exposed runtime architecture selector;
- dirty bar only after real edits;
- discard restores the server baseline;
- successful save replaces the baseline so the form is clean;
- server errors remain visible and dirty for retry;
- lifecycle read-only states disable live edits.

- [ ] **Step 2: Recompose without changing the live payload**

Preserve every existing backend field and action. Use the template card spacing,
section labels, bordered selected state, compact control sizing, and responsive
tab strip. Keep all live fields in the server-action path.

- [ ] **Step 3: Verify and commit**

```bash
cd apps/web
npm run test:ci -- tests/app/agent-page.test.tsx tests/app/agent-actions.test.ts
npm run check
npm run typecheck
git commit -m "feat(web): port Presvo assistant configuration"
```

---

## Task 4: Add Advanced Assistant and Voice Preview

**Files:**

- Create: `apps/web/src/components/agent/assistant-preview.tsx`
- Create: `apps/web/src/components/agent/voice-preview-selector.tsx`
- Create: `apps/web/src/components/agent/test-assistant-preview.tsx`
- Create: `apps/web/tests/app/assistant-preview.test.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`

- [ ] **Step 1: Write failing Preview-isolation tests**

Require persistent `Preview` labels, French fictional voice data, correct
radiogroup keyboard behavior, local personality/language/speed/provider state,
simulated voice status, a test-assistant drawer with a progressing finite state
machine, reset controls, and zero production fetch/action calls.

- [ ] **Step 2: Implement isolated local state**

Use the template's voice cards, advanced-control grid, waveform treatment,
drawer hierarchy, transcript bubbles, and call controls. Copy no US business
claims. Explicitly state that preview changes reset on reload and consume no
minutes.

- [ ] **Step 3: Verify isolation and commit**

```bash
cd apps/web
npm run test:ci -- tests/app/assistant-preview.test.tsx tests/app/agent-page.test.tsx
rg -n '@/lib/api|/actions|billing|telephony' src/components/agent/assistant-preview.tsx \
  src/components/agent/voice-preview-selector.tsx \
  src/components/agent/test-assistant-preview.tsx
npm run check
npm run typecheck
git commit -m "feat(web): add assistant configuration Preview"
```

---

## Task 5: Port Billing and Non-Purchasable Plan Preview

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/billing/page.tsx`
- Modify: `apps/web/src/components/billing/billing-summary-cards.tsx`
- Modify: `apps/web/src/components/billing/billing-actions-card.tsx`
- Modify: `apps/web/src/components/billing/usage-ledger-list.tsx`
- Create: `apps/web/src/components/billing/plan-comparison-preview.tsx`
- Modify: `apps/web/tests/app/billing-page.test.tsx`

- [ ] **Step 1: Add failing hierarchy and Preview tests**

Cover:

- current starter plan and usage lead the page;
- period and ledger values remain backend-authoritative;
- Checkout and Portal behavior is unchanged;
- invoices and receipts route truthfully to the live Stripe Portal boundary;
- extra plans are visibly Preview, non-purchasable, and local-only;
- Preview comparison controls reset locally and make no billing request.

- [ ] **Step 2: Implement the Presvo billing composition**

Use the template's current-plan card, usage progress, comparison-card rhythm,
status treatment, and responsive ledger. Do not display invented prices,
renewal claims, invoice rows, or downloadable files.

- [ ] **Step 3: Verify and commit**

```bash
cd apps/web
npm run test:ci -- tests/app/billing-page.test.tsx
npm run check
npm run typecheck
git commit -m "feat(web): port Presvo billing workspace"
```

---

## Task 6: Port Account and Settings Extensions

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/account/page.tsx`
- Modify: `apps/web/src/components/account/account-status-card.tsx`
- Create: `apps/web/src/components/account/account-settings-preview.tsx`
- Modify: `apps/web/tests/app/account-page.test.tsx`

- [ ] **Step 1: Add failing hierarchy and isolation tests**

Require:

- service status first;
- calm account destination rows;
- theme and session guidance remain truthful to the shell/Clerk boundary;
- notification, privacy, retention, password, and MFA extensions are one
  persistent Preview region with local reset;
- Preview text never says a production setting was saved/enabled;
- deactivation remains separate and exact;
- inactive/reactivation presentations remain unchanged.

- [ ] **Step 2: Implement the Presvo settings composition**

Use the template's two-column settings hierarchy where space permits, compact
preference rows, right-side account context, and separate danger card. Keep
real lifecycle controls outside the Preview region.

- [ ] **Step 3: Verify and commit**

```bash
cd apps/web
npm run test:ci -- tests/app/account-page.test.tsx tests/app/account-actions.test.ts
npm run check
npm run typecheck
git commit -m "feat(web): port Presvo account settings"
```

---

## Task 7: Add Configuration Visual and Browser Coverage

**Files:**

- Create: `apps/web/tests/e2e/configuration-visual.spec.ts`
- Create: `apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/*`
- Modify: `scripts/run-local-e2e.sh`

- [ ] **Step 1: Add Playwright coverage**

Cover:

- Assistant, Billing, and Account at desktop light and mobile dark;
- agent live edit, discard, successful save, and navigation warning;
- assistant Preview voice/test/reset with no API mutation;
- billing live action presence and plan Preview isolation;
- account Preview reset and exact deactivation confirmation;
- overflow at 390, 768, 1024, and 1440;
- reduced-motion Preview interactions.

- [ ] **Step 2: Update and inspect screenshots**

```bash
UPDATE_SNAPSHOTS=1 bash scripts/run-local-e2e.sh
```

Inspect every new image for exact tokens, typography, spacing, borders,
shadows, card hierarchy, responsive stacking, Preview visibility, and dark
contrast.

- [ ] **Step 3: Run immutable lifecycle and commit**

```bash
bash scripts/run-local-e2e.sh
git commit -m "test(web): lock Presvo configuration visuals"
```

---

## Task 8: Run the Phase 4 Production Gate

- [ ] **Step 1: Scan production routes**

```bash
rg -n 'slate-|transition-all|TODO|FIXME|\\$[0-9]|America/Los_Angeles|San Francisco' \
  apps/web/src/app/'(app)'/dashboard/agent \
  apps/web/src/app/'(app)'/dashboard/billing \
  apps/web/src/app/'(app)'/dashboard/account \
  apps/web/src/components/agent \
  apps/web/src/components/billing \
  apps/web/src/components/account
```

- [ ] **Step 2: Run full web verification**

```bash
cd apps/web
npm run check
npm run typecheck
npm run test:ci
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsuZXhhbXBsZS5jb20k \
CLERK_SECRET_KEY=ci-build-only-placeholder \
API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_APP_URL=http://127.0.0.1:3000 \
NEXT_PUBLIC_REALTIME_ENABLED=false \
  npm run build
```

- [ ] **Step 3: Verify phase invariants**

Confirm real save baselines, lifecycle truth, hosted billing boundaries,
explicit Preview labels, local reset behavior, and zero Preview mutation.

- [ ] **Step 4: Complete and commit**

```bash
git commit -m "docs: complete Presvo configuration and account phase"
```

---

## Phase 4 Completion Checklist

- [ ] Assistant live settings retain the exact backend payload and readiness rules.
- [ ] Successful saves clear dirty state; failures remain retryable and dirty.
- [ ] Advanced assistant and voice/test controls are explicit local-only Preview.
- [ ] Billing presents only backend-authoritative plan, period, usage, and ledger data.
- [ ] Unsupported plan comparison is non-purchasable Preview.
- [ ] Account lifecycle actions preserve exact confirmation and backend truth.
- [ ] Settings extensions are explicit local-only Preview.
- [ ] Assistant, Billing, and Account pass visual, interaction, isolation, overflow, and production gates.
