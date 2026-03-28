# Billing And Usage API Design

## Goal

Add the missing product-facing billing backend surface for MVP while keeping Stripe as the source of truth for subscription actions. The backend should expose read APIs for subscription and usage state, plus hosted Stripe action endpoints for starting a subscription and managing an existing one.

## Scope

This slice adds:

- `GET /api/billing/subscription`
- `GET /api/billing/usage`
- `GET /api/billing/usage-ledger`
- `POST /api/billing/checkout-session`
- `POST /api/billing/portal-session`

This slice does not add:

- direct subscription mutation endpoints
- cancellation logic implemented in the app backend
- frontend UI

## API Shape

### Read APIs

#### `GET /api/billing/subscription`

Returns the authenticated user's current subscription row or `null` if no subscription exists.

Fields should include:

- `plan_tier`
- `status`
- `allocated_minutes`
- `current_period_start`
- `current_period_end`
- `stripe_customer_id`
- `stripe_subscription_id`

#### `GET /api/billing/usage`

Returns a compact usage summary for the authenticated user.

Fields should include:

- `minutes_remaining`
- `allocated_minutes`
- `plan_tier`
- `subscription_status`
- `current_period_start`
- `current_period_end`

If the user has no subscription or ledger yet, the response still returns `200` with zeroed usage fields.

#### `GET /api/billing/usage-ledger`

Returns recent ledger entries for the authenticated user, newest first.

Fields should include:

- `id`
- `event_type`
- `minutes_delta`
- `balance_after`
- `call_id`
- `created_at`

The response should be capped by a simple limit parameter with a conservative default.

### Action APIs

#### `POST /api/billing/checkout-session`

Creates a Stripe Checkout session for an unsubscribed user and returns the hosted Stripe URL.

Request body:

- `plan_tier`: `starter | standard`

The backend maps `plan_tier` to a configured Stripe price id and creates the session with the authenticated user bound through metadata.

#### `POST /api/billing/portal-session`

Creates a Stripe Billing Portal session for a subscribed user and returns the hosted Stripe URL.

Request body:

- `return_url`

The backend uses the current subscription's `stripe_customer_id`.

## Architecture

### Router

[`apps/api/app/routers/billing.py`](/home/i933k/code/ai/bmad-opevo/apps/api/app/routers/billing.py) will expose all five endpoints and handle auth.

### Read Service

A `BillingQueryService` will assemble:

- current subscription
- current usage snapshot
- ledger history

This keeps read aggregation out of the router and separate from Stripe webhook processing.

### Session Service

A `BillingSessionService` will create:

- Stripe Checkout sessions
- Stripe Billing Portal sessions

This keeps product-facing billing actions separate from [`BillingService`](/home/i933k/code/ai/bmad-opevo/apps/api/app/services/billing_service.py), which should remain focused on webhook ingestion and local billing state synchronization.

### Repository Changes

Extend existing repositories rather than introducing a new storage layer:

- [`subscription_repository.py`](/home/i933k/code/ai/bmad-opevo/apps/api/app/repositories/subscription_repository.py)
  - add lookup by `user_id`
- [`usage_repository.py`](/home/i933k/code/ai/bmad-opevo/apps/api/app/repositories/usage_repository.py)
  - add recent ledger listing by `user_id`

## Stripe Config

Add these API-side settings:

- `STRIPE_SECRET_KEY`
- `STRIPE_PRICE_STARTER`
- `STRIPE_PRICE_STANDARD`
- `STRIPE_CHECKOUT_SUCCESS_URL`
- `STRIPE_CHECKOUT_CANCEL_URL`

The public API contract stays stable on `plan_tier`, while the backend maps plan tiers to Stripe price ids.

## Error Handling

### Read APIs

- `GET /api/billing/subscription`
  - returns `200` with `null` when no subscription exists
- `GET /api/billing/usage`
  - returns `200` with zeroed fields when no subscription exists
- `GET /api/billing/usage-ledger`
  - returns `200` with an empty list when no ledger exists

### Action APIs

- `POST /api/billing/checkout-session`
  - `409` if the user already has an active subscription
  - `422` for invalid `plan_tier`
  - `500` or `502` for Stripe misconfiguration or upstream failure

- `POST /api/billing/portal-session`
  - `409` if the user has no Stripe customer context yet
  - `500` or `502` for Stripe misconfiguration or upstream failure

## Testing

Add tests for:

- subscription read with and without an existing subscription
- usage summary using latest `usage_ledgers`
- usage-ledger ordering and limit behavior
- checkout session creation for valid plan tiers
- checkout rejection for already subscribed users
- portal session creation for subscribed users
- portal rejection when no Stripe customer exists
- auth protection on all endpoints

## Recommendation

Use hosted Stripe flows for all user-initiated billing actions in MVP. This gives the product full backend billing capability while keeping payment complexity, proration rules, and payment method edge cases inside Stripe where they belong.
