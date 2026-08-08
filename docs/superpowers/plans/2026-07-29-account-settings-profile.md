# Account Settings Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Account page's duplicate destination links with a live user/business Profile editor, real assigned-number and lifecycle context, truthful Clerk security ownership, and clearly isolated Preview preferences.

**Architecture:** Keep `/dashboard/account` server-first: load mandatory account lifecycle state and independently resolve activation/profile and Clerk identity context. Save the four editable business-profile fields through a narrow account server action that re-reads and merges the complete backend profile before calling the replacement-style profile `PUT`; client components receive focused view models rather than full API snapshots.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Clerk, Zod, shadcn-style Opevo primitives, Vitest/Testing Library, Playwright, Biome.

## Global Constraints

- Preserve the approved two-column hierarchy: Profile is primary; Assigned number and compact Account status form the right service-context column.
- Profile owns Full name, read-only Email, Personal phone, Business name, and Timezone.
- `BusinessProfile.owner_name`, `business_name`, `existing_phone_e164`, and `timezone` are the only editable live profile fields.
- Email comes from Clerk and is never written through the business-profile API.
- The backend `PUT /api/business-profile` replaces the complete draft; every Account save must re-read the latest profile and preserve all fields outside the four-field Account editor.
- Notifications, Privacy & recordings, and inline MFA remain visibly labeled Preview and never perform production mutations.
- Password and sign-in management is Clerk-owned; local development must present honest unavailable guidance.
- Preserve all existing account lifecycle and exact deactivation behavior.
- Remove `Receptionist profile`, `Billing and subscription`, and `Theme and session` from the Account page.
- Do not copy US fixture values or unsupported behavior from `Opevo_frontend`.
- Do not add new profile, notification, retention, password, or MFA backend endpoints.
- Keep the France launch constraint and canonical `Europe/Paris` timezone option.
- Keep minimum interactive target height at `min-h-11`, semantic named regions, one page `h1`, field-associated errors, and no horizontal overflow at 390, 768, 1024, or 1440 pixels.
- `Opevo_frontend/` is an untracked visual reference and must never be edited or staged.
- The main worktree currently contains a user-owned one-line `pt-6` edit in `apps/web/src/app/(app)/dashboard/account/page.tsx`. Execute in an isolated worktree, preserve the intent as top spacing in the new service-context column, and never reset or overwrite the main-worktree edit without explicit user direction.

---

### Task 1: Establish the Safe Account Profile and Identity Boundary

**Files:**

- Create: `apps/web/src/lib/types/account-settings.ts`
- Create: `apps/web/src/lib/phone-numbers.ts`
- Create: `apps/web/src/lib/auth/account-identity.ts`
- Modify: `apps/web/src/app/(activation)/activate/_components/profile/carrier-confirmation.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/profile/profile-form.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/profile/business-fields.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/account/actions.ts`
- Modify: `apps/web/tests/app/account-actions.test.ts`
- Create: `apps/web/tests/lib/account-identity.test.ts`
- Create: `apps/web/tests/lib/phone-numbers.test.ts`

**Interfaces:**

- Produces:

```ts
export type AccountProfileValues = Readonly<{
  owner_name: string;
  business_name: string;
  existing_phone_e164: string;
  timezone: string;
}>;

export type AccountIdentity = Readonly<{
  email: string | null;
  securityMode: "clerk" | "unavailable";
}>;

export function normalizeFrenchNumber(value: string): string | null;
export function formatFrenchNumber(value: string): string;
export async function resolveAccountIdentity(): Promise<AccountIdentity>;
export async function saveAccountProfileAction(input: unknown): Promise<AccountProfileActionResult>;
```

- `AccountProfileActionResult` is a discriminated union in Account actions:

```ts
export type AccountProfileActionResult =
  | {
      status: "success";
      message: string;
      profile: AccountProfileValues;
    }
  | {
      status: "error";
      code: string;
      message: string;
      fields?: Array<keyof AccountProfileValues>;
    };
```

- Consumes the existing `getActivationSnapshot()`, `saveBusinessProfile()`,
  `requireServerSession()`, and `revalidateAccountPaths()` boundaries.

- [ ] **Step 1: Write failing utility and identity tests**

Add assertions that:

```ts
expect(normalizeFrenchNumber("06 12 34 56 78")).toBe("+33612345678");
expect(normalizeFrenchNumber("+33 (0)6 12 34 56 78")).toBeNull();
expect(formatFrenchNumber("+33612345678")).toBe("06 12 34 56 78");
```

In `account-identity.test.ts`, reset modules between auth modes and assert:

```ts
expect(await resolveAccountIdentity()).toEqual({
  email: null,
  securityMode: "unavailable",
});
```

for local mode, and:

```ts
expect(await resolveAccountIdentity()).toEqual({
  email: "owner@opevo.test",
  securityMode: "clerk",
});
```

when mocked Clerk `currentUser()` returns that primary email. Add a Clerk
lookup rejection case that returns `{ email: null, securityMode: "clerk" }`
without throwing.

- [ ] **Step 2: Run the utility and identity tests to verify RED**

Run:

```bash
cd apps/web
npm run test:ci -- tests/lib/phone-numbers.test.ts tests/lib/account-identity.test.ts
```

Expected: FAIL because `@/lib/phone-numbers` and
`@/lib/auth/account-identity` do not exist.

- [ ] **Step 3: Extract the shared French phone helpers**

Move the exact normalization and display behavior out of
`carrier-confirmation.tsx` into `src/lib/phone-numbers.ts`:

```ts
export function normalizeFrenchNumber(value: string): string | null {
  const compact = value.trim().replace(/[\s().-]/g, "");
  if (/^0[1-9]\d{8}$/.test(compact)) return `+33${compact.slice(1)}`;
  if (/^\+33[1-9]\d{8}$/.test(compact)) return compact;
  if (/^0033[1-9]\d{8}$/.test(compact)) return `+${compact.slice(2)}`;
  return null;
}

export function formatFrenchNumber(value: string): string {
  const normalized = normalizeFrenchNumber(value);
  const local = normalized ? `0${normalized.slice(3)}` : value.replace(/\D/g, "").slice(0, 10);
  return local.replace(/(\d{2})(?=\d)/g, "$1 ").trim();
}
```

Update the three activation imports so their behavior and tests remain
unchanged.

- [ ] **Step 4: Implement the focused account types and identity resolver**

Create `account-settings.ts` with the exact types above. In
`account-identity.ts`, keep the module server-only and dynamically import Clerk:

```ts
export async function resolveAccountIdentity(): Promise<AccountIdentity> {
  if (!shouldWrapClerk) {
    return { email: null, securityMode: "unavailable" };
  }

  try {
    const { currentUser } = await import("@clerk/nextjs/server");
    const user = await currentUser();
    const email = user?.primaryEmailAddress?.emailAddress ?? user?.emailAddresses[0]?.emailAddress ?? null;
    return { email, securityMode: "clerk" };
  } catch {
    return { email: null, securityMode: "clerk" };
  }
}
```

- [ ] **Step 5: Run the utility and identity tests to verify GREEN**

Run:

```bash
cd apps/web
npm run test:ci -- tests/lib/phone-numbers.test.ts tests/lib/account-identity.test.ts tests/app/carrier-confirmation.test.tsx tests/app/profile-form.test.tsx
```

Expected: PASS with unchanged activation phone behavior.

- [ ] **Step 6: Write failing Account profile action tests**

Extend the activation API mock with `getActivationSnapshot` and
`saveBusinessProfile`. Use the shared activation fixture and assert that:

```ts
const result = await saveAccountProfileAction({
  owner_name: "  Maya Martin  ",
  business_name: "Atelier Martin",
  existing_phone_e164: "06 12 34 56 78",
  timezone: "Europe/Paris",
});

expect(saveBusinessProfileMock).toHaveBeenCalledWith({
  owner_name: "Maya Martin",
  business_name: "Atelier Martin",
  existing_phone_e164: "+33612345678",
  timezone: "Europe/Paris",
  business_type: "Florist",
  public_description: "A neighbourhood florist.",
  business_hours: expect.any(Object),
  confirmed_carrier: "orange",
  receptionist_name: "Lea",
  faqs: [{ question: "Parking?", answer: "Street parking is available." }],
  special_instructions: "Keep replies concise.",
  escalation_notes: "Escalate urgent requests.",
});
expect(result).toMatchObject({
  status: "success",
  message: "Profile saved.",
});
```

Also require:

- malformed or empty fields return `invalid_input` and never call either
  profile API;
- current server constraints reject an overlong owner or business name;
- snapshot failure returns bounded `profile_unavailable` or
  `request_failed` copy;
- a backend save failure retains a customer-safe error and never leaks provider
  details;
- success revalidates `/dashboard`, `/dashboard/account`, `/dashboard/agent`,
  `/dashboard/billing`, and `/activate`.

- [ ] **Step 7: Run Account action tests to verify RED**

Run:

```bash
cd apps/web
npm run test:ci -- tests/app/account-actions.test.ts
```

Expected: FAIL because `saveAccountProfileAction` is not exported.

- [ ] **Step 8: Implement the safe replacement-style profile save**

Add a strict Zod input schema for exactly the four editable keys. The action
must:

1. require a server session;
2. structurally parse and trim input;
3. normalize `existing_phone_e164`;
4. fetch the latest `ActivationSnapshot`;
5. enforce `profile_constraints.name_max_length`;
6. construct the complete `BusinessProfileDraft` from the latest snapshot,
   overlaying only the four editable values;
7. call `saveBusinessProfile()` with that complete draft;
8. revalidate Account and other profile consumers;
9. return the confirmed four-field profile.

Use this merge shape:

```ts
const completeDraft: BusinessProfileDraft = {
  owner_name: parsed.data.owner_name,
  business_name: parsed.data.business_name,
  business_type: snapshot.profile.business_type,
  public_description: snapshot.profile.public_description,
  timezone: parsed.data.timezone,
  business_hours: snapshot.profile.business_hours,
  existing_phone_e164: normalizedPhone,
  confirmed_carrier: snapshot.profile.confirmed_carrier,
  receptionist_name: snapshot.profile.receptionist_name,
  faqs: snapshot.profile.faqs.map((faq) => ({ ...faq })),
  special_instructions: snapshot.profile.special_instructions,
  escalation_notes: snapshot.profile.escalation_notes,
};
```

Do not reuse the broad activation form action from the Account client.

- [ ] **Step 9: Run Task 1 verification**

Run:

```bash
cd apps/web
npm run test:ci -- tests/app/account-actions.test.ts tests/lib/account-identity.test.ts tests/lib/phone-numbers.test.ts tests/app/carrier-confirmation.test.tsx tests/app/profile-form.test.tsx
npm run typecheck
npm run check
```

Expected: all commands exit 0.

- [ ] **Step 10: Commit Task 1**

```bash
git add \
  apps/web/src/lib/types/account-settings.ts \
  apps/web/src/lib/phone-numbers.ts \
  apps/web/src/lib/auth/account-identity.ts \
  'apps/web/src/app/(activation)/activate/_components/profile/carrier-confirmation.tsx' \
  'apps/web/src/app/(activation)/activate/_components/profile/profile-form.tsx' \
  'apps/web/src/app/(activation)/activate/_components/profile/business-fields.tsx' \
  'apps/web/src/app/(app)/dashboard/account/actions.ts' \
  apps/web/tests/app/account-actions.test.ts \
  apps/web/tests/lib/account-identity.test.ts \
  apps/web/tests/lib/phone-numbers.test.ts
git commit -m "feat(web): add safe account profile boundary"
```

---

### Task 2: Build the Live Profile Editor

**Files:**

- Create: `apps/web/src/components/account/account-profile-form.tsx`
- Create: `apps/web/tests/components/account-profile-form.test.tsx`

**Interfaces:**

- Consumes:

```ts
type AccountProfileFormProps = Readonly<{
  initialProfile: AccountProfileValues;
  email: string | null;
  nameMaxLength: number;
  readOnly: boolean;
}>;
```

- Calls `saveAccountProfileAction()` and uses the existing
  `UnsavedChangesBar` and `useUnsavedChangesGuard`.

- Produces one named `Profile` region with Full name, Email, Personal phone,
  Business name, and Timezone controls.

- [ ] **Step 1: Write failing Profile form behavior tests**

Cover these exact behaviors:

- initial values map to all five labeled controls;
- Email has `type="email"`, `autoComplete="email"`, and `readOnly`, and shows
  `Email unavailable in local development` when `email` is null;
- Full name uses `autoComplete="name"`, Business name uses
  `autoComplete="organization"`, and Personal phone uses
  `type="tel"`, `inputMode="tel"`, and `autoComplete="tel"`;
- Personal phone includes the persistent warning `Changing this forwarding
  number may pause incoming calls until forwarding is verified again.`;
- editing a supported field shows the existing `Unsaved changes` status;
- Discard restores the confirmed baseline;
- invalid blank names and invalid French phone values focus the first invalid
  field and do not call the action;
- Save sends exactly four keys with the normalized phone;
- pending Save disables Save and Discard;
- a confirmed success replaces the baseline and removes the dirty bar;
- an error keeps the draft, reports the returned message, and permits retry;
- `readOnly` disables editable inputs and never renders the save bar.

Use a deferred promise in the pending-state test rather than timers.

- [ ] **Step 2: Run Profile form tests to verify RED**

Run:

```bash
cd apps/web
npm run test:ci -- tests/components/account-profile-form.test.tsx
```

Expected: FAIL because `AccountProfileForm` does not exist.

- [ ] **Step 3: Implement the minimal Profile editor state machine**

Use confirmed baseline plus draft state:

```ts
const [baseline, setBaseline] = useState(initialProfile);
const [draft, setDraft] = useState(initialProfile);
const [result, setResult] = useState<AccountProfileActionResult | null>(null);
const [isPending, startTransition] = useTransition();
const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
```

Normalize phone only in the submitted payload, not while the user types. Use
field-associated `FieldError` values for validation. On success, use
`result.profile` as both the next baseline and visible state. On error, leave
the draft untouched and pass the error message to `UnsavedChangesBar`.

The Personal phone description must state the forwarding-verification impact
before the user edits or saves it. Do not imply that changing this routing
source number is a harmless contact-only update.

Render only the canonical `Europe/Paris` option, plus the saved timezone as a
temporary option if legacy data contains another valid value.

- [ ] **Step 4: Run Profile form tests to verify GREEN**

Run:

```bash
cd apps/web
npm run test:ci -- tests/components/account-profile-form.test.tsx tests/components/unsaved-changes.test.tsx
npm run typecheck
npm run check
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit Task 2**

```bash
git add \
  apps/web/src/components/account/account-profile-form.tsx \
  apps/web/tests/components/account-profile-form.test.tsx
git commit -m "feat(web): add live account profile editor"
```

---

### Task 3: Add Assigned Number and Compact Lifecycle Context

**Files:**

- Create: `apps/web/src/components/account/assigned-number-card.tsx`
- Modify: `apps/web/src/components/account/account-status-card.tsx`
- Create: `apps/web/tests/components/assigned-number-card.test.tsx`
- Modify: `apps/web/tests/app/account-page.test.tsx`

**Interfaces:**

- Produces:

```ts
export function AssignedNumberCard({
  number,
  forwarding,
}: Readonly<{
  number: string | null;
  forwarding: ForwardingGuide | null;
}>): ReactNode;

export function CompactAccountStatusCard({
  account,
}: Readonly<{ account: AccountStatus }>): ReactNode;
```

- `AssignedNumberCard` uses `formatFrenchNumber()` and never mutates telephony
  state.
- `CompactAccountStatusCard` reuses `getAccountLifecyclePresentation()` and
  the existing reactivation/overview actions.

- [ ] **Step 1: Write failing Assigned number tests**

Assert that a real number:

```ts
render(<AssignedNumberCard number="+33612345678" forwarding={forwardingGuide()} />);
expect(screen.getByText("06 12 34 56 78")).toBeVisible();
expect(screen.getByRole("button", { name: "Copy assigned number" })).toBeEnabled();
expect(screen.getByRole("link", { name: "Review forwarding setup" })).toHaveAttribute(
  "href",
  "/activate?milestone=forwarding",
);
```

Mock `navigator.clipboard.writeText` and cover success and failure status
announcements. Cover a null number with:

```ts
expect(screen.getByText("No Opevo number is assigned yet.")).toBeVisible();
expect(screen.queryByRole("button", { name: "Copy assigned number" })).toBeNull();
expect(screen.getByRole("link", { name: "Review number setup" })).toHaveAttribute(
  "href",
  "/activate?milestone=number",
);
```

- [ ] **Step 2: Write failing compact lifecycle assertions**

Extend Account page tests to render `CompactAccountStatusCard` for Active,
Action needed, each deactivation progress value, Attention required, and
Inactive. Require a named `Account status` region, the bounded lifecycle badge,
the existing action for non-serving/inactive states, and no provider codes or
internal blockers.

- [ ] **Step 3: Run service-context tests to verify RED**

Run:

```bash
cd apps/web
npm run test:ci -- tests/components/assigned-number-card.test.tsx tests/app/account-page.test.tsx
```

Expected: FAIL because the assigned-number and compact status exports do not
exist.

- [ ] **Step 4: Implement AssignedNumberCard**

Render a compact named card with:

- uppercase `Assigned number` label;
- formatted number or truthful empty-state copy;
- icon-only copy button with accessible name;
- polite copy result status;
- a subdued forwarding block only when `forwarding` is non-null;
- a `Review forwarding setup` link rather than duplicating the full activation
  guide or inventing dial instructions.

- [ ] **Step 5: Implement CompactAccountStatusCard**

Extract the existing lifecycle action selection into a private helper used by
both `AccountStatusCard` and `CompactAccountStatusCard`. The compact card
renders:

- an `h2` named `Account status`;
- the existing animated lifecycle badge;
- `lifecycle.title`, `description`, and progress;
- current Attention-required guidance;
- the existing `Review Overview` or reactivation action where applicable.

Do not change `getAccountLifecyclePresentation()` or deactivation copy.

- [ ] **Step 6: Run Task 3 verification**

Run:

```bash
cd apps/web
npm run test:ci -- tests/components/assigned-number-card.test.tsx tests/app/account-page.test.tsx tests/app/account-actions.test.ts
npm run typecheck
npm run check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit Task 3**

```bash
git add \
  apps/web/src/components/account/assigned-number-card.tsx \
  apps/web/src/components/account/account-status-card.tsx \
  apps/web/tests/components/assigned-number-card.test.tsx \
  apps/web/tests/app/account-page.test.tsx
git commit -m "feat(web): add account service context"
```

---

### Task 4: Compose the Settings Page and Truthful Preference Sections

**Files:**

- Create: `apps/web/src/components/account/clerk-security-button.tsx`
- Modify: `apps/web/src/components/account/account-settings-preview.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/account/page.tsx`
- Modify: `apps/web/tests/app/account-page.test.tsx`
- Create: `apps/web/tests/components/clerk-security-button.test.tsx`

**Interfaces:**

- `ClerkSecurityButton` calls Clerk's `openUserProfile()` only after an
  explicit click.
- `AccountSettingsPreview` consumes:

```ts
type AccountSettingsPreviewProps = Readonly<{
  securityMode: "clerk" | "unavailable";
}>;
```

- The page consumes `getAccount()`, a locally caught
  `getActivationSnapshot()`, and `resolveAccountIdentity()`.

- [ ] **Step 1: Rewrite Account page hierarchy tests for the approved design**

Set default activation and identity mocks, then require:

```ts
expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
expect(screen.getByRole("region", { name: "Profile" })).toBeVisible();
expect(screen.getByRole("region", { name: "Assigned number" })).toBeVisible();
expect(screen.getByRole("region", { name: "Account status" })).toBeVisible();
expect(screen.getByRole("region", { name: "Notifications Preview" })).toBeVisible();
expect(screen.getByRole("region", { name: "Privacy & recordings Preview" })).toBeVisible();
expect(screen.getByRole("region", { name: "Security" })).toBeVisible();
expect(screen.getByRole("region", { name: "Danger zone" })).toBeVisible();
```

Verify DOM order is Profile/service context, Notifications, Privacy &
recordings, Security, then Danger zone. Explicitly assert the page contains no
headings or links named:

- `Receptionist profile`;
- `Billing and subscription`;
- `Theme and session`;
- `Manage receptionist`;
- `View billing`.

Add an activation lookup rejection test that still shows Account status,
Security, and the Danger zone while Profile and Assigned number present honest
unavailable states. The Profile unavailable region must include a
`Retry profile` link to `/dashboard/account` with prefetch disabled.

- [ ] **Step 2: Write failing Preview and Clerk security tests**

Update Preview assertions so each unsupported section has its own visible
Preview badge. Retain the existing local reset and zero-fetch checks.

For Clerk mode:

```ts
fireEvent.click(screen.getByRole("button", { name: "Manage password and sign-in" }));
expect(openUserProfileMock).toHaveBeenCalledOnce();
```

For unavailable mode, require the guidance:

```text
Password and sign-in methods are managed through Clerk in hosted accounts.
```

and no fake success message or Clerk button.

- [ ] **Step 3: Run composition tests to verify RED**

Run:

```bash
cd apps/web
npm run test:ci -- tests/app/account-page.test.tsx tests/components/clerk-security-button.test.tsx
```

Expected: FAIL on the old `Account` hierarchy and missing Clerk security
component.

- [ ] **Step 4: Re-compose AccountSettingsPreview**

Keep one isolated local preference state object and one reset action, but render
three sibling product surfaces:

1. `Notifications` with a Preview badge;
2. `Privacy & recordings` with a Preview badge;
3. `Security`, containing the live Clerk action when available and an inline
   Preview badge beside the local-only MFA control.

All Preview copy must continue to say that changes are local and reset on
reload. The Clerk button is not labeled Preview and does not update local
status text.

- [ ] **Step 5: Implement the Settings page composition**

Load:

```ts
const [account, activation, identity] = await Promise.all([
  getAccount(),
  getActivationSnapshot().catch(() => null),
  resolveAccountIdentity(),
]);
```

Render:

```tsx
<PageIntro
  description="Your profile, Opevo number, and account preferences."
  eyebrow="Account settings"
  title="Settings"
/>

<div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
  {/* Profile or honest unavailable region */}
  <aside className="grid content-start gap-5 pt-0 lg:pt-1">
    {/* Assigned number or honest unavailable region */}
    <CompactAccountStatusCard account={account} />
  </aside>
</div>
```

Pass `readOnly={account.status !== "active"}` to the Profile form. Use the
activation snapshot's four values and constraints, never the reference fixture.
Keep the current Danger zone conditional and dialog unchanged.

Map nullable backend profile fields explicitly:

```ts
const initialProfile: AccountProfileValues = {
  owner_name: activation.profile.owner_name ?? "",
  business_name: activation.profile.business_name ?? "",
  existing_phone_e164: activation.profile.existing_phone_e164 ?? "",
  timezone: activation.profile.timezone ?? "Europe/Paris",
};
```

- [ ] **Step 6: Run Task 4 verification**

Run:

```bash
cd apps/web
npm run test:ci -- \
  tests/app/account-page.test.tsx \
  tests/app/account-actions.test.ts \
  tests/components/account-profile-form.test.tsx \
  tests/components/assigned-number-card.test.tsx \
  tests/components/clerk-security-button.test.tsx
npm run typecheck
npm run check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit Task 4**

```bash
git add \
  apps/web/src/components/account/clerk-security-button.tsx \
  apps/web/src/components/account/account-settings-preview.tsx \
  'apps/web/src/app/(app)/dashboard/account/page.tsx' \
  apps/web/tests/app/account-page.test.tsx \
  apps/web/tests/components/clerk-security-button.test.tsx
git commit -m "feat(web): refocus account page on user settings"
```

---

### Task 5: Verify Live Profile Flow, Responsive Visuals, and Full Regression Safety

**Files:**

- Modify: `apps/web/tests/e2e/configuration-visual.spec.ts`
- Update: `apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/account-desktop-light.png`
- Update: `apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/account-mobile-dark.png`

**Interfaces:**

- Exercises the complete server page, Account profile server action, backend
  profile persistence, local Preview isolation, and existing deactivation
  dialog.

- [ ] **Step 1: Update the Account visual expectations**

Change both Account visual cases to expect the `Settings` page heading.
Before each screenshot, require visible Profile, Assigned number, Account
status, Notifications Preview, Privacy & recordings Preview, Security, and
Danger zone regions.

- [ ] **Step 2: Add the live Profile browser flow**

Add a serial Playwright test that:

1. reads the initial Full name;
2. edits it and uses Discard to prove the baseline is restored;
3. edits it to a temporary value and clicks Save changes;
4. waits for confirmed save feedback and the dirty bar to disappear;
5. reloads and proves the temporary value persisted;
6. restores the exact initial Full name through the same confirmed save path;
7. reloads and proves restoration.

Do not edit Personal phone in this browser flow because changing the forwarding
source number intentionally invalidates routing verification.

- [ ] **Step 3: Strengthen Preview and security browser assertions**

Update the existing Account Preview test to scope interactions to the
Notifications Preview, Privacy & recordings Preview, and Security regions.
Require the local account to show hosted-Clerk security guidance. Keep the
zero-backend-request assertion around only Preview and dialog-cancel
interactions; the live Profile save test is intentionally outside that
observer.

- [ ] **Step 4: Update only the two approved Account snapshots**

Run from the repository root:

```bash
E2E_FOCUS=configuration UPDATE_SNAPSHOTS=1 bash scripts/run-local-e2e.sh
```

Inspect:

- `account-desktop-light.png` at 1440 × 1100;
- `account-mobile-dark.png` at 390 × 844.

Confirm Profile dominates the desktop composition, service context stays in
the right column, all sections stack without clipping on mobile, Preview labels
are visible, and the sticky save bar does not appear in clean screenshots.
Revert any unrelated snapshot changes rather than staging them.

- [ ] **Step 5: Run the configuration browser suite without snapshot updates**

Run:

```bash
E2E_FOCUS=configuration bash scripts/run-local-e2e.sh
```

Expected: activation setup, configuration visuals, live Profile persistence,
Preview isolation, exact deactivation confirmation, and all four overflow
viewports pass without changing tracked files.

- [ ] **Step 6: Run the full web verification**

Run:

```bash
cd apps/web
npm run test:ci
npm run typecheck
npm run check
```

Expected: all Vitest files pass, TypeScript exits 0, and Biome reports no
errors.

- [ ] **Step 7: Review the complete change**

Run:

```bash
git diff --check
git status --short
git diff --stat
git diff
```

Confirm no `Opevo_frontend/` file, unrelated snapshot, local secret, provider
identifier, or main-worktree user edit is staged.

- [ ] **Step 8: Commit Task 5**

```bash
git add \
  apps/web/tests/e2e/configuration-visual.spec.ts \
  apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/account-desktop-light.png \
  apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/account-mobile-dark.png
git commit -m "test(web): cover account settings profile flow"
```

- [ ] **Step 9: Run committed-tree verification before integration**

Run:

```bash
cd apps/web
npm run test:ci
npm run typecheck
npm run check
cd ../..
git status --short
git log --oneline -6
```

Expected: the feature worktree is clean, every verification command exits 0,
and the five task commits are present.
