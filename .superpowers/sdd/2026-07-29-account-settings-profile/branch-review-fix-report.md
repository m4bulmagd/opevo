# Account Settings Final Branch-Review Fix Report

## Status

DONE_WITH_CONCERNS

The complete final branch-review fix set is implemented in the
`feat/account-settings-profile` worktree. All focused tests, the full web unit
suite, TypeScript, Biome, the prescribed snapshot update, and the Account-only
non-update E2E selection pass.

The only remaining concern is inherited from the preceding Account E2E task:
the repository's broad non-update configuration run encounters four stale
Assistant/Billing snapshots before the serial suite reaches Account. Those
unrelated images, source files, plans, and briefs were not changed.

## Implemented Findings

### Important 1: Pending-save race and data loss

- Full name, Personal phone, Business name, and Timezone are disabled while the
  profile action is pending.
- The regression test holds the request open, attempts interaction with a
  disabled field, verifies the submitted draft remains intact, and then proves
  the confirmed server profile becomes the new baseline.

### Important 2: Persistent save announcement

- A successful profile action now renders a persistent `role="status"` region
  with `aria-live="polite"` after the dirty bar disappears.
- The success state clears on the next edit or discard.

### Important 3: Retry-oriented action errors

- Profile snapshot failure now returns exactly:
  `Your profile is temporarily unavailable. Try saving again.`
- Profile save failure now returns exactly:
  `We couldn't save your profile. Try saving again.`
- Tests use exact result equality and continue to prove provider details do not
  escape. Lifecycle and reactivation copy was not changed.

### Important 4: Truthful missing-email copy

- `AccountProfileForm` receives the resolved identity security mode.
- Local unavailable mode renders
  `Email unavailable in local development`.
- Hosted Clerk mode renders
  `Email is temporarily unavailable.`
- Form and page tests cover both modes; the existing identity resolver tests
  continue to cover local and Clerk-failure behavior without provider leakage.

### Important 5: Server-authoritative timezone allow-list

- `apps/web/src/lib/account-timezone.ts` defines the shared allow-list rule.
- `Europe/Paris` is always accepted.
- A valid saved legacy timezone is accepted only as the current saved legacy
  value.
- Invalid saved values and other valid-but-unsupported IANA timezones are
  rejected server-side with a field-specific invalid-input result.
- Client options and server authorization use the same rule.

### Important 6: 44px interaction targets

- All Profile inputs and the Timezone select are at least 44px tall.
- The assigned-number copy target is at least 44px in both dimensions.
- Forwarding and number-setup links are at least 44px tall.

### Important 7: Lifecycle matrix coverage

Table-driven tests now cover:

- serving and action-needed active accounts;
- all six normal deactivation progress states;
- attention required from either state or blocker;
- inactive accounts with reactivation available or not ready;
- the expected action, copy, enabled state, and suppression of raw state and
  blocker codes.

The existing production lifecycle mapping already satisfied this matrix, so
the coverage addition required no production-copy change.

### Minor findings

- Assigned number is now a semantic level-two heading while preserving the
  labelled region and card-title styling.
- Missing-email and phone descriptions are associated through
  `aria-describedby`; the phone keeps both help and error descriptions.
- Server-returned profile fields map to field-associated errors in deterministic
  rendered form order. Focus moves to the first affected control after pending
  state ends, while the draft and retry action remain available.

## TDD Evidence

Each behavioral finding was handled independently with a focused regression
test before its production change.

| Finding | RED evidence | GREEN evidence |
| --- | --- | --- |
| Pending save | Profile form test failed because Full name remained enabled. | All editable controls disabled; retained draft test passed. |
| Save announcement | Test could not find the persistent status region. | Polite success status persisted and cleared on edit. |
| Retry errors | Exact equality exposed the old refresh-oriented copy. | Both exact retry messages passed. |
| Missing email | Hosted-mode test still received local-development copy. | Local and Clerk form/page cases passed. |
| Timezones | Invalid saved and unsupported valid timezones reached the save API. | Four-case authorization table passed with rejected saves suppressed. |
| 44px targets | Three focused assertions failed for undersized controls/links. | Profile and assigned-number target tests passed. |
| Lifecycle matrix | Required coverage was absent; characterization cases were added. | All 12 selected lifecycle cases passed without production changes. |
| Assigned heading | Heading query failed against the styled `div`. | Semantic `h2` assertion passed. |
| Descriptions | Accessible-description assertions failed. | Missing-email and phone help/error associations passed. |
| Server fields | Returned fields did not focus or annotate controls. | Phone-first focus, both errors, retained draft, and retry passed. |

Representative focused commands:

```bash
cd apps/web
npm run test:ci -- tests/components/account-profile-form.test.tsx
npm run test:ci -- tests/app/account-actions.test.ts
npm run test:ci -- tests/components/assigned-number-card.test.tsx
npm run test:ci -- tests/app/account-page.test.tsx
```

Final combined focused result:

```bash
npm run test:ci -- \
  tests/components/account-profile-form.test.tsx \
  tests/components/assigned-number-card.test.tsx \
  tests/app/account-actions.test.ts \
  tests/app/account-page.test.tsx \
  tests/lib/account-identity.test.ts
```

```text
5 test files passed
74 tests passed
exit 0
```

## Full Web Verification

Run fresh on the final post-review source tree:

```bash
cd apps/web
npm run test:ci
npm run typecheck
npm run check
```

Results:

```text
Vitest: 51 test files passed, 481 tests passed, exit 0
TypeScript: tsc --noEmit, exit 0
Biome: Checked 218 files. No fixes applied, exit 0
```

Vitest emitted the existing Node `module.register()` deprecation warning. The
warning did not affect the result.

## E2E and Visual Verification

The first in-sandbox attempt could not access the Docker socket/build cache.
The same repository command was rerun through the normal scoped Docker
approval:

```bash
E2E_FOCUS=configuration UPDATE_SNAPSHOTS=1 bash scripts/run-local-e2e.sh
```

```text
activation.spec.ts: 1 passed
configuration-visual.spec.ts: 15 passed
exit 0
```

Playwright refreshed all six configuration images. The four unrelated
Assistant/Billing images were immediately restored, leaving only:

```text
account-desktop-light.png
account-mobile-dark.png
```

A temporary `/tmp` command shim then constrained the repository runner to the
eight Account cases for the required non-update proof:

```bash
PATH=/tmp/account-settings-e2e-npm:$PATH \
  E2E_FOCUS=configuration \
  bash scripts/run-local-e2e.sh
```

```text
activation.spec.ts: 1 passed
selected Account configuration tests: 8 passed
exit 0
```

The selection covered both Account snapshots, live Profile
persistence/restoration, Preview/deactivation behavior, and all four
configuration viewport cases. The temporary shim was deleted and the
non-update run changed no tracked files.

Both approved images were inspected at original resolution:

- desktop light: 1440 × 2160;
- mobile dark: 390 × 2809.

The Account hierarchy, 44px controls, responsive stacking, and danger-zone
content remain visible without clipping or horizontal overflow. The assigned
number alone retains the expected deterministic visual mask.

## Self-Review

Two independent read-only review passes examined the working-tree patch:

- Standards: no remaining documented-standard breach or baseline code smell.
  The review's unused-export suggestion was resolved by keeping the canonical
  timezone constant module-private; the new module is included in the final
  change set.
- Specification: no missing or partial requirement, scope creep, or apparently
  incorrect implementation.

`git diff --check` passes.

## Scope and Files

Production:

```text
apps/web/src/app/(app)/dashboard/account/actions.ts
apps/web/src/app/(app)/dashboard/account/page.tsx
apps/web/src/components/account/account-profile-form.tsx
apps/web/src/components/account/assigned-number-card.tsx
apps/web/src/lib/account-timezone.ts
```

Tests and approved visuals:

```text
apps/web/tests/app/account-actions.test.ts
apps/web/tests/app/account-page.test.tsx
apps/web/tests/components/account-profile-form.test.tsx
apps/web/tests/components/assigned-number-card.test.tsx
apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/account-desktop-light.png
apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/account-mobile-dark.png
```

This report is included in the final focused commit with subject:

```text
fix(web): harden account settings interactions
```
