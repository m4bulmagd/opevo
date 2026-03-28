# Backend Context

This document captures implementation notes and staging verification for the backend foundation MVP.

## Current State

- Chunk 1 foundation implemented: app scaffold, config, schema, repositories, and Clerk auth sync.
- Chunk 2 core API paths implemented: Stripe billing webhook, telephony service boundary, authenticated websocket gate, and LiveKit inbound dispatch webhook.
- Chunk 3 initial agent and post-call layers implemented: provider registry, prompt builder, worker entrypoint scaffold, and post-call lifecycle scaffolding.
- Agent config API now exposes authenticated read/update of the editable runtime fields through `GET /api/agent/config` and `PATCH /api/agent/config`.
- `PATCH /api/agent/config` now treats `is_enabled` as a synchronous telephony toggle, switching the assigned Telnyx number between `app-active` and `app-disabled` in the same request and rolling the config change back if the provider update fails.
- Contract details and usage examples for that surface are documented in [agent-config-api.md](/home/i933k/code/ai/bmad-opevo/docs/architecture/agent-config-api.md).
- Call history API now exposes `GET /api/calls`, `GET /api/calls/{call_id}`, and `DELETE /api/calls/{call_id}` for authenticated users.
- User-facing call delete is now a soft delete: deleted calls disappear from list/detail APIs, while transcript rows and recording objects remain available for admin/manual recovery later.

## Verified Locally

- API tests covering health, auth, billing, telephony, realtime, LiveKit dispatch, repository flow, and post-call lifecycle.
- Agent config API tests now cover full-config reads, normal field updates, enable toggles, missing-number conflicts, and rollback on telephony failure.
- Call history API tests now cover visible-call listing, transcript detail, fresh recording URL minting, and soft-delete behavior.
- Agent tests covering prompt building, pipeline config selection, Gemini STS runtime construction, and runtime event emission.
- API Docker image build.
- Agent Docker image build.
- Queue-backed call finalization is now covered locally so call persistence no longer depends on the LiveKit agent surviving shutdown long enough to wait on the full API response.
- Local infrastructure stack prepared in [compose.yaml](/home/i933k/code/ai/bmad-opevo/.worktrees/backend-foundation-mvp/compose.yaml) for PostgreSQL 17.8, Redis 7.4.7, and MinIO.
- LiveKit SIP participant field mapping reviewed against the official docs on 2026-03-15: `sip.phoneNumber` is the caller number for inbound trunks and `sip.trunkPhoneNumber` is the dialed trunk number.
- Agent runtime selection is now explicit per user via `agent_config.pipeline_mode`:
  - `stt_llm_tts` remains the default and keeps Speechmatics/Deepgram + Gemini + TTS composition
  - `sts` uses Gemini Live native audio with Gemini-managed turn detection and no external STT/TTS path

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
- A real forwarded call on `+33392091999` persisted a completed `calls` row, transcript rows, a `call_completed` usage ledger entry, and a non-blocking failed notification row when Firebase credentials were not configured.

Not yet fully verified in staging:

- A fresh `invoice.paid` event after the persisted subscription exists. Re-sending the same Stripe event id is deduplicated by `webhook_events`, so it does not create a second reset row.
- Real Telnyx purchase and `app-active` / `app-disabled` switching with `TELNYX_ORDERING_ENABLED=true`.
- The new queue-backed call finalization flow with the dedicated `worker` service in Compose.
- End-to-end call persistence, transcript capture, summary generation, and actual minute deduction for the Stripe-backed user.

Ready for manual execution once these external credentials and endpoints are available:
- Clerk issuer, JWKS URL, and webhook secret
- Stripe webhook secret and live test-mode subscription objects
- Telnyx API key and active/disabled connection IDs
- LiveKit URL, API key, and API secret
- Google Gemini API key for `pipeline_mode="sts"`
- Speechmatics, Deepgram, and ElevenLabs credentials as needed for `pipeline_mode="stt_llm_tts"`
- Reachable staging Postgres, Redis, and S3-compatible storage if not using the local Compose stack

## Remaining Manual Verification

- Fresh Stripe `invoice.paid` event for the persisted subscription to verify `invoice_paid_reset` in `usage_ledgers`.
- Real Telnyx purchase and active/disabled switching once you deliberately enable ordering.
- Real LiveKit webhook verification and agent dispatch against the purchased number `+33392091999`.
- Queue-backed finalization with the dedicated `worker` service during a real forwarded call.
- End-to-end forwarded phone call with transcript, summary, and minute deduction for the Stripe-backed user.
- One real inbound call for a user configured with `pipeline_mode="sts"` to compare latency and verify transcript/finalization behavior on the Gemini native-audio path.

## Blockers For Full Staging Smoke Path

- Telnyx ordering is intentionally disabled by default through `TELNYX_ORDERING_ENABLED=false` to prevent real purchases during verification.
- The purchased number currently belongs to the seeded `staging-local-user`, not the new Stripe-backed Clerk user.
- A new billing-cycle or equivalent fresh Stripe invoice event is required to verify the minute-reset path without bypassing webhook idempotency.
