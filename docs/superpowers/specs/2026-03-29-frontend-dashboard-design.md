# Frontend Dashboard Design

## Goal

Add the first customer-facing frontend for the AI Call Assistant MVP as a single authenticated dashboard app. The initial product should help end customers get their AI phone agent live quickly, then transition into day-to-day operation without feeling like onboarding and operations are separate products.

## Users

Primary users:

- end customers managing their own AI phone agent

Primary jobs:

- get the agent configured and live
- monitor recent calls, summaries, and recordings
- update agent behavior safely
- understand remaining usage and subscription state

## Product Direction

The frontend should feel:

- calm and trustworthy
- premium and sharp

The design should avoid generic SaaS dashboard patterns. The interface should read as an operational control surface with guided activation, not a wall of interchangeable cards.

## Scope

This slice adds:

- a new authenticated frontend app
- an adaptive home route that prioritizes setup before activation and operations after activation
- customer-facing screens for calls, agent configuration, and billing
- integration with the existing backend contracts for agent config, call history, and billing

This slice does not add:

- internal admin tooling
- a public marketing site as the primary deliverable
- frontend features that require backend contracts not already implemented

## Recommended Stack

- Next.js App Router
- TypeScript
- Tailwind CSS
- Clerk for auth in the frontend

## Product Shape

The frontend should be one authenticated app with a stable navigation model and a home route that changes by account state.

Primary routes:

- `/app`
- `/app/calls`
- `/app/agent`
- `/app/billing`

The home route should behave as the command center for both first-run activation and ongoing operations.

## Information Architecture

Primary navigation:

- `Home`
- `Calls`
- `Agent`
- `Billing`

### `/app` for first-run customers

The home screen should be dominated by a setup checklist until the agent is live.

Key modules:

- status hero showing readiness state
- guided setup sequence
- compact agent snapshot
- quiet empty-state placeholder for calls until history exists

The setup sequence should cover:

- naming the agent
- adding owner or business context
- choosing `pipeline_mode`
- enabling the agent

### `/app` for active customers

Once the agent is live, the same home route should shift to operations.

Key modules:

- current enabled or disabled state
- recent calls with summary snippets
- quick access to latest recordings when available
- shortcuts into agent settings and full call history
- compact usage and subscription summary

### `/app/calls`

This route should expose the implemented call-history surface:

- list view from `GET /api/calls`
- detail view for one call from `GET /api/calls/{call_id}`
- transcript display
- recording access when `recording_url` is available
- soft-delete action using `DELETE /api/calls/{call_id}`

### `/app/agent`

This route should expose the editable agent configuration:

- `agent_name`
- `owner_context`
- `system_prompt`
- `knowledge_base`
- `pipeline_mode`
- `is_enabled`

The enable toggle should be treated as a consequential operational action because the backend synchronously switches telephony routing.

### `/app/billing`

This route should expose:

- current subscription state
- usage snapshot
- recent usage ledger activity
- hosted Stripe checkout flow for unsubscribed users
- hosted Stripe billing portal flow for subscribed users

## Screen Composition

The visual layout should use a calm editorial structure rather than repetitive equal-sized cards.

Recommended composition for `/app`:

- top band with page identity and live-state indicator
- wide main column for the dominant workflow
- narrower supporting column for compact operational context

### First-run home composition

Main column:

- staged setup flow with one clear next action
- each completed setup step collapses into a short summary row
- final enable action with explicit pending, success, and failure states

Supporting column:

- agent snapshot
- runtime mode
- readiness summary

### Active home composition

Main column:

- recent call activity
- summary-first list items
- clear path into call detail

Supporting column:

- agent enabled state
- quick actions
- minutes remaining
- subscription summary

## Interaction Design

The app should feel operationally crisp and trustworthy.

Interaction rules:

- avoid modals for core call detail
- use dedicated pages or split views for transcript and recording review
- use guarded pending state for `is_enabled` changes instead of false optimism
- allow low-risk fields to autosave only if the UX remains legible
- prefer explicit save for prompt-heavy fields such as `system_prompt` and `knowledge_base`
- phrase delete as archival or removal from view because the backend soft-deletes calls

## Backend Alignment

The frontend should align to current implemented backend contracts rather than assumed future APIs.

### Agent config

Use the authenticated agent config API documented in:

- [`docs/architecture/agent-config-api.md`](/home/i933k/code/ai/bmad-opevo/docs/architecture/agent-config-api.md)

### Calls

Use the authenticated call-history API documented in:

- [`docs/architecture/call-history-api.md`](/home/i933k/code/ai/bmad-opevo/docs/architecture/call-history-api.md)

### Billing

Use the authenticated billing and usage API documented in:

- [`docs/architecture/billing-usage-api.md`](/home/i933k/code/ai/bmad-opevo/docs/architecture/billing-usage-api.md)

## State Handling

The frontend should treat missing backend state as normal first-run conditions where appropriate.

Expected behavior:

- auth gate all app routes
- treat missing or uninitialized config state as setup-needed, not app-failure
- render zero-usage billing states as valid starter states
- render empty calls as a teaching state, not a blank screen
- surface `409` enable failures as actionable setup issues
- surface `502` enable failures as temporary telephony problems
- handle `recording_url = null` as an expired-retention or unavailable-recording state

## Responsive Behavior

The app should remain fully usable on mobile widths.

Responsive expectations:

- home screen collapses from two columns into one without hiding critical actions
- calls list keeps summary and status readable on small screens
- agent config editing remains practical on touch devices
- navigation should adapt, not amputate

## Testing

The first frontend build should include coverage for:

- authenticated route protection
- adaptive home behavior for first-run and activated users
- agent config hydration, save flow, and enable-toggle failure states
- calls list empty and populated states
- call detail transcript rendering
- missing or expired recording state
- billing usage snapshot rendering
- hosted Stripe checkout and billing portal action handling
- mobile-width layout behavior on the primary screens

## Recommendation

Build the first frontend as a single authenticated customer dashboard with a setup-led home experience that evolves into an operating surface after activation. This best matches the product priority of making both “get live quickly” and “operate confidently” first-class outcomes without splitting the experience into separate applications or disjoint flows.
