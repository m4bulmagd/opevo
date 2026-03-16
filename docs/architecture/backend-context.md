# Backend Context

This document captures implementation notes and staging verification for the backend foundation MVP.

## Current State

- Chunk 1 foundation implemented: app scaffold, config, schema, repositories, and Clerk auth sync.
- Chunk 2 core API paths implemented: Stripe billing webhook, telephony service boundary, authenticated websocket gate, and LiveKit inbound dispatch webhook.
- Chunk 3 initial agent and post-call layers implemented: provider registry, prompt builder, worker entrypoint scaffold, and post-call lifecycle scaffolding.

## Verified Locally

- API tests covering health, auth, billing, telephony, realtime, LiveKit dispatch, repository flow, and post-call lifecycle.
- Agent tests covering prompt building, pipeline config selection, and runtime event emission.
- API Docker image build.
- Agent Docker image build.
- Local infrastructure stack prepared in [compose.yaml](/home/i933k/code/ai/bmad-opevo/.worktrees/backend-foundation-mvp/compose.yaml) for PostgreSQL 17.8, Redis 7.4.7, and MinIO.
- LiveKit SIP participant field mapping reviewed against the official docs on 2026-03-15: `sip.phoneNumber` is the caller number for inbound trunks and `sip.trunkPhoneNumber` is the dialed trunk number.

## Staging Smoke Status

Partially executed on 2026-03-16.

Verified in staging:

- API boot and `/healthz` response against the local Compose Postgres/Redis/MinIO stack.
- Agent boot against the configured LiveKit project.
- Clerk `user.created` webhook delivery and local user sync.
- Stripe `customer.subscription.created` webhook delivery with a real test subscription containing `metadata.clerk_user_id`.
- Subscription persistence and starter-minute allocation after Stripe activation.
- Safe Telnyx candidate selection with the following enforced rules:
  - search `national` first, then `local`
  - `filter[reservable]=true`
  - `filter[exclude_held_numbers]=true`
  - only accept USD numbers where `upfront_cost + monthly_cost <= 2.00`
  - inspect at most 3 candidates
- Real candidate found on 2026-03-16 without purchasing:
  - `+33974065674`
  - `phone_number_type = national`
  - `upfront_cost = 1.00000 USD`
  - `monthly_cost = 0.50000 USD`
- Subscription activation now persists even when number purchase is intentionally disabled, with a pending `phone_number_provisioning_review_required` notification instead of a `500`.

Not yet fully verified in staging:

- A fresh `invoice.paid` event after the persisted subscription exists. Re-sending the same Stripe event id is deduplicated by `webhook_events`, so it does not create a second reset row.
- Real Telnyx purchase and `app-active` / `app-disabled` switching with `TELNYX_ORDERING_ENABLED=true`.
- Real LiveKit inbound webhook and agent dispatch from a forwarded phone call.
- End-to-end call persistence, transcript capture, summary generation, and actual minute deduction for the Stripe-backed user.

Ready for manual execution once these external credentials and endpoints are available:
- Clerk issuer, JWKS URL, and webhook secret
- Stripe webhook secret and live test-mode subscription objects
- Telnyx API key and active/disabled connection IDs
- LiveKit URL, API key, and API secret
- Gemini, Speechmatics, Deepgram, and ElevenLabs credentials as needed
- Reachable staging Postgres, Redis, and S3-compatible storage if not using the local Compose stack

## Remaining Manual Verification

- Fresh Stripe `invoice.paid` event for the persisted subscription to verify `invoice_paid_reset` in `usage_ledgers`.
- Real Telnyx purchase and active/disabled switching once you deliberately enable ordering.
- Real LiveKit webhook verification and agent dispatch against the purchased number `+33392091999`.
- End-to-end forwarded phone call with transcript, summary, and minute deduction.

## Blockers For Full Staging Smoke Path

- Telnyx ordering is intentionally disabled by default through `TELNYX_ORDERING_ENABLED=false` to prevent real purchases during verification.
- The purchased number currently belongs to the seeded `staging-local-user`, not the new Stripe-backed Clerk user.
- A new billing-cycle or equivalent fresh Stripe invoice event is required to verify the minute-reset path without bypassing webhook idempotency.
