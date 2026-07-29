# Account Settings Profile Design

**Date:** 2026-07-29  
**Status:** Approved

## 1. Purpose

Refocus `/dashboard/account` on the signed-in user and account-level settings.
The page should follow the compact, two-column settings hierarchy established
by the Presvo reference while continuing to present only backend-authoritative
data as live.

The current `Receptionist profile` and `Billing and subscription` destination
sections do not belong on this page because Agent and Billing already have
dedicated navigation destinations. `Theme and session` is also removed because
those controls already live in the workspace header.

## 2. Page Hierarchy

The page title becomes `Settings`, with the description:

> Your profile, Presvo number, and account preferences.

At desktop sizes, the leading content uses a two-column layout:

- a wide primary column containing the Profile card;
- a narrower service-context column containing the Assigned number card and a
  compact Account status card.

On smaller screens, the service-context cards stack immediately below the
Profile card. The remaining settings cards follow in this order:

1. Notifications;
2. Privacy & recordings;
3. Security;
4. Danger zone.

The existing prominent lifecycle surface is replaced by the compact Account
status card. Lifecycle truth, actions, and customer-safe wording remain intact.
The Danger zone stays visually separate at the bottom of the page.

## 3. Profile Card

The Profile card is the primary account surface. It contains:

- **Full name** — editable, backed by `BusinessProfile.owner_name`;
- **Email** — read-only, sourced from Clerk identity;
- **Personal phone** — editable, backed by
  `BusinessProfile.existing_phone_e164`;
- **Business name** — editable, backed by `BusinessProfile.business_name`;
- **Timezone** — editable, backed by `BusinessProfile.timezone`.

Email is never copied into the business-profile payload. In Clerk mode, the
page shows the authenticated user's primary email. If an authenticated email
cannot be resolved, including local development without a supported identity
source, the field presents an honest unavailable/development state rather than
inventing an address.

Profile changes use an explicit Save/Discard bar. The form:

- initializes from the server snapshot;
- becomes dirty only when a supported editable field differs from its baseline;
- validates the existing business-profile constraints;
- disables conflicting actions while a save is pending;
- saves only the supported profile fields through the existing
  business-profile mutation boundary;
- clears dirty state only after the backend confirms the saved result;
- keeps the draft and exposes a retryable inline error after a failed save.

Unrelated business-profile fields are preserved and must not be cleared by an
account-profile save.

## 4. Assigned Number and Account Status

### Assigned number

The Assigned number card shows the real provisioned Presvo number from
`ActivationSnapshot.number.assigned_e164`.

When a number is available, the card provides:

- locale-aware phone formatting;
- a copy action with accessible feedback;
- forwarding guidance derived from the existing activation/forwarding
  snapshot when that guidance is available.

When no number is provisioned, the card shows a truthful empty state and a
relevant next step. It does not display reference-template fixture data.

### Account status

The compact Account status card reuses the existing bounded lifecycle
presentation for Active, Action needed, Deactivating, Attention required, and
Inactive states. It retains any required lifecycle next action, including
reactivation or overview guidance, without exposing provider names, internal
state codes, or identifiers.

## 5. Preferences and Security

Notifications and Privacy & recordings remain unsupported account-setting
extensions. They keep their existing local-only behavior and are visibly
marked `Preview`.

The Notifications card contains:

- Call summaries;
- Missed calls;
- Usage alerts;
- Product updates.

The Privacy & recordings card contains:

- Record calls;
- Recording retention.

Preview changes:

- stay in isolated client state;
- never call profile, account, billing, telephony, or lifecycle APIs;
- never claim that a production setting was saved or enabled;
- reset on reload and through an explicit local reset action.

The Security card provides a real Clerk-owned action for managing password and
sign-in methods when Clerk is available. An inline MFA toggle remains Preview
until Presvo has an approved production integration. Local development uses
honest guidance instead of pretending to open a hosted security flow.

## 6. Data and Component Boundaries

The server page resolves these independent sources:

- `getActivationSnapshot()` for the business profile, assigned number,
  forwarding context, and profile constraints;
- `getAccount()` for lifecycle state;
- a small server-only identity resolver for Clerk primary email and hosted
  account-management availability.

The page maps those sources into focused presentation props. Client components
must not depend directly on the complete activation or account wire formats.

Recommended component boundaries:

- `AccountProfileForm` — editable profile fields and Save/Discard behavior;
- `AssignedNumberCard` — provisioned-number presentation and copy feedback;
- existing `AccountStatusCard`, extended with a compact presentation;
- `AccountSettingsPreview` — re-composed into the approved Notifications,
  Privacy & recordings, and mixed live/Preview Security sections;
- existing `DeactivateAccountDialog` — unchanged lifecycle mutation behavior.

The server page may load independent data concurrently, but a non-lifecycle
lookup failure must not hide lifecycle state or deactivation controls.

## 7. Failure and Empty States

- If profile loading fails, show the Profile card with a retryable unavailable
  message; keep account status and the Danger zone available.
- If assigned-number data is absent, show a bounded no-number state.
- If identity resolution fails, keep Email read-only and explicitly
  unavailable; do not fail the page.
- If a profile save fails, retain the user's draft and show a customer-safe
  inline error with retry.
- If lifecycle loading fails, use the page's existing protected-data error
  boundary; do not infer an account state.
- Preview interactions remain available only when their local component can
  render safely and never affect the live profile form.

## 8. Accessibility and Responsive Behavior

- `Settings` remains the only page-level `h1`.
- Every card is a named region with a logical heading order.
- Inputs have persistent labels, appropriate autocomplete values, and
  field-associated validation messages.
- Save, discard, copy, Clerk, and lifecycle actions meet the existing minimum
  target size and expose pending or result feedback to assistive technology.
- Keyboard focus returns predictably after hosted-account or dialog flows.
- The two-column layout collapses without horizontal overflow at the project's
  supported mobile widths.
- Preview badges are exposed in both visible text and semantic structure.

## 9. Testing

Component and page tests cover:

- the new Settings title and approved section order;
- the absence of Receptionist, Billing, and Theme destination sections;
- correct profile-field initialization and email read-only behavior;
- dirty, discard, pending, success, failure, and retry profile states;
- preservation of unrelated business-profile fields in save payloads;
- assigned-number formatting, copy feedback, and no-number presentation;
- compact lifecycle variants and unchanged deactivation behavior;
- Clerk and local-development security boundaries;
- strict Preview isolation with no production mutations;
- landmarks, heading hierarchy, labels, autocomplete, focus, and status
  announcements.

Playwright coverage updates the account desktop-light and mobile-dark visual
snapshots and checks overflow at the existing supported viewport set.

## 10. Out of Scope

- Moving or duplicating Agent configuration on the Account page;
- moving or duplicating Billing controls on the Account page;
- a new notification-preferences backend;
- a new recording-retention backend;
- a Presvo-owned password or MFA system;
- changing Clerk identity data through the business-profile API;
- changing account deactivation semantics;
- changing provisioning or forwarding workflows;
- copying US fixture data or unsupported behaviors from `Presvo_frontend`.
