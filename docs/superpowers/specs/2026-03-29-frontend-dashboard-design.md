# Frontend Dashboard Design

## Goal

Rebuild the customer-facing frontend as a new `apps/web` Next.js dashboard that uses the `dashboard-temp` template as its structural and visual foundation while serving this product's actual customer workflows.

The result should feel like the template was adapted into the product, not like a separate custom dashboard was designed from scratch.

## Users

Primary users:

- end customers managing their own AI phone agent

Primary jobs:

- get the agent configured and live
- monitor recent calls, summaries, and recordings
- update agent behavior safely
- understand usage and subscription state

## Product Direction

The frontend should keep the template's strengths:

- persistent sidebar application shell
- clean shadcn-based surfaces
- preset-driven theme system
- light and dark mode support
- responsive dashboard behavior
- crisp spacing and card rhythm

The frontend should not introduce a second competing design language. Product-specific screens should inherit the template's layout grammar, color tokens, typography defaults, and interaction patterns.

## Template Alignment

The rebuild should explicitly align to `dashboard-temp` in these ways:

- keep the frontend rooted at `apps/web`
- use a `src/` app structure inside `apps/web`
- keep Next.js 16 App Router conventions used by the template
- keep Biome as the primary formatter and linter
- keep shadcn/ui primitives and the template's sidebar/header composition
- keep the built-in preference model for theme mode, theme preset, layout width, navbar style, and sidebar behavior
- keep the bundled theme presets available

The rebuild should explicitly differ from `dashboard-temp` in these ways:

- remove demo dashboards, placeholder pages, and unrelated sample data
- replace template navigation with product navigation
- replace template demo content with product data from the backend
- keep auth and app routes focused on this product only

## Scope

This slice adds:

- a fresh `apps/web` frontend app
- the template shell adapted to customer product routes
- an authenticated `/dashboard` experience for `Home`, `Calls`, `Agent`, and `Billing`
- Clerk-based auth integration in the frontend
- backend integration for agent config, call history, and billing
- template-preserving theme and layout controls inside the dashboard

This slice does not add:

- a public marketing site as the primary deliverable
- internal admin tooling
- product-specific theme token redesign beyond template presets
- features that need backend APIs not already implemented

## App Location And Stack

Frontend app location:

- `apps/web`

Recommended stack:

- Next.js App Router
- TypeScript
- Tailwind CSS v4
- shadcn/ui
- Biome
- Clerk
- Vitest and React Testing Library

## Route Shape

Primary product routes:

- `/dashboard`
- `/dashboard/calls`
- `/dashboard/calls/[callId]`
- `/dashboard/agent`
- `/dashboard/billing`

Supporting routes:

- `/sign-in`
- `/sign-up`
- `/unauthorized`

The template shell should wrap the authenticated `/dashboard` area, but the user-facing route prefix should remain `/dashboard`.

## Information Architecture

Primary navigation:

- `Home`
- `Calls`
- `Agent`
- `Billing`

Sidebar behavior:

- use the template's persistent sidebar system
- preserve inset sidebar behavior and collapsible states
- map sidebar items directly to product routes
- avoid filler groups or coming-soon sections in the first release

Header behavior:

- preserve the template-style top bar
- keep theme and layout controls
- keep a user/account control tied to Clerk
- remove demo-only controls that do not support product use

## Theme And Visual Rules

The product should respect the template style, colors, and theme system.

Required rules:

- keep the template `default` preset as the initial default
- keep `brutalist`, `soft-pop`, and `tangerine` available as selectable presets
- keep the template light, dark, and system theme modes
- keep the template's spacing, radii, shadows, and card treatment unless a product requirement forces a deviation
- reuse template CSS variables instead of inventing a new token layer for this slice
- reuse template typography defaults before introducing custom font changes

This means product screens can be adapted, but they should still look native beside the template shell and controls.

## Screen Composition

The product should use the template's dashboard composition style: sidebar shell, compact top bar, and content areas built from cards, lists, and split-density sections.

### `/dashboard`

The home route should remain the command center, but it should be built with template-native composition:

- top summary row for current operational state
- primary activity column for setup or recent call activity
- secondary column for compact context such as agent status and usage

#### First-run state

Key modules:

- readiness summary card
- staged setup checklist
- agent snapshot card
- quiet empty state for calls

#### Active state

Key modules:

- recent calls list
- concise status and readiness cards
- quick links into agent settings and billing
- compact usage summary

### `/dashboard/calls`

This route should use template-native list/detail presentation:

- call list built from the existing backend API
- clear status chips and timestamps
- call detail page for transcript and recording review
- archive action framed as removal from the active view

Avoid modal-heavy review flows. Dedicated page transitions are the better match here.

### `/dashboard/agent`

This route should use a form-driven settings page with shadcn fields and grouped cards.

Editable fields:

- `agent_name`
- `owner_context`
- `system_prompt`
- `knowledge_base`
- `pipeline_mode`
- `is_enabled`

The enable toggle is a consequential action and should have guarded pending, success, and failure states.

### `/dashboard/billing`

This route should use compact finance-style cards and lists:

- subscription state
- usage snapshot
- recent usage ledger items
- checkout action for unsubscribed users
- billing portal action for subscribed users

## Backend Alignment

The frontend should align to current backend contracts.

### Agent config

Use:

- [`docs/architecture/agent-config-api.md`](/home/i933k/code/ai/bmad-opevo/docs/architecture/agent-config-api.md)

### Calls

Use:

- [`docs/architecture/call-history-api.md`](/home/i933k/code/ai/bmad-opevo/docs/architecture/call-history-api.md)

### Billing

Use:

- [`docs/architecture/billing-usage-api.md`](/home/i933k/code/ai/bmad-opevo/docs/architecture/billing-usage-api.md)

## State Handling

Expected behavior:

- auth gate all `/dashboard` routes
- treat missing config as first-run setup state
- treat empty calls as a valid teaching state
- treat zero usage as a valid starter billing state
- handle `recording_url = null` as unavailable or expired recording state
- surface agent enable failures clearly, especially `409` and `502` style failures

## Responsive Behavior

The rebuild should preserve the template's responsive dashboard behavior.

Responsive expectations:

- sidebar collapses cleanly on smaller widths
- top bar controls remain usable on touch devices
- `/dashboard` home layout collapses to one column without hiding key actions
- calls list remains readable on mobile
- agent forms remain practical on smaller screens
- billing cards stack without losing action clarity

## Testing

The first frontend rebuild should include coverage for:

- authenticated route protection
- template shell rendering on product routes
- theme and preference controls persisting expected state
- adaptive `/dashboard` behavior for first-run and active users
- agent config hydration, save flow, and enable-toggle failure states
- calls list empty and populated states
- call detail transcript and recording rendering
- billing usage snapshot rendering
- hosted checkout and billing portal actions

## Recommendation

Rebuild the frontend as a fresh `apps/web` app that keeps the product IA on `/dashboard` while adopting the `dashboard-temp` template's shell, theme system, and visual language as the actual foundation. This gives the product a stronger starting point, avoids repeating the discarded custom dashboard direction, and keeps future work aligned with a stable template architecture.
