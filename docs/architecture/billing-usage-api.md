# Billing And Usage API

This document describes the authenticated billing endpoints exposed by the API.

## Endpoints

### `GET /api/billing/subscription`

Returns the current local subscription row for the authenticated user or `null` when no subscription exists yet.

Response fields:

- `plan_tier`
- `status`
- `allocated_minutes`
- `current_period_start`
- `current_period_end`
- `stripe_customer_id`
- `stripe_subscription_id`

### `GET /api/billing/usage`

Returns the current usage snapshot for the authenticated user.

Response fields:

- `minutes_remaining`
- `allocated_minutes`
- `plan_tier`
- `subscription_status`
- `current_period_start`
- `current_period_end`

If the user has no subscription or usage ledger yet, the API still returns `200` with zeroed usage fields.

### `GET /api/billing/usage-ledger`

Returns recent usage ledger rows for the authenticated user, newest first.

Query parameters:

- `limit`
  - integer
  - default `20`
  - min `1`
  - max `100`

Response fields per entry:

- `id`
- `event_type`
- `minutes_delta`
- `balance_after`
- `call_id`
- `created_at`

### `POST /api/billing/checkout-session`

Creates a hosted Stripe Checkout session for a user who does not already have an active subscription.

Request body:

```json
{
  "plan_tier": "starter"
}
```

Allowed `plan_tier` values:

- `starter`

Response:

```json
{
  "url": "https://checkout.stripe.com/..."
}
```

Behavior:

- maps the plan tier to the configured Stripe price id
- includes `user_id`, `clerk_user_id`, and `plan_tier` in Stripe metadata
- returns `409` when the user already has an active subscription

### `POST /api/billing/portal-session`

Creates a hosted Stripe Billing Portal session for a user with an existing Stripe customer.

Request body:

```json
{
  "return_url": "https://your-app.example.com/dashboard/billing"
}
```

`return_url` is optional for compatibility. When supplied, its scheme, host, and port must match the configured server URL. Its path is ignored.

Response:

```json
{
  "url": "https://billing.stripe.com/..."
}
```

Behavior:

- uses the current local subscription's `stripe_customer_id`
- always sends the server-owned `STRIPE_BILLING_PORTAL_RETURN_URL` to Stripe
- returns `400` for malformed or off-origin caller return URLs
- returns `409` when no Stripe customer is available yet

## Required API Config

Create local `apps/api/.env` from the tracked
[`apps/api/.env.example`](../../apps/api/.env.example), then add these values:

```dotenv
STRIPE_SECRET_KEY=replace-me
STRIPE_WEBHOOK_SECRET=replace-me
STRIPE_PRICE_STARTER=price_replace_me
STRIPE_CHECKOUT_SUCCESS_URL=https://your-app.example.com/billing/success
STRIPE_CHECKOUT_CANCEL_URL=https://your-app.example.com/billing/cancel
STRIPE_BILLING_PORTAL_RETURN_URL=https://your-app.example.com/dashboard/billing
```

## Notes

- Stripe remains the source of truth for payment state.
- User-initiated changes go through hosted Stripe flows.
- Local subscription and usage state are still synchronized through Stripe webhooks.
