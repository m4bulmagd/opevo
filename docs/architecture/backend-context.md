# Backend Context

This document captures implementation notes and staging verification for the backend foundation MVP.

## Current State

- Self-serve France MVP implementation is now in place locally: the launch contract is France-only, `starter`-only, `stt_llm_tts`-only, and onboarding-first.
- Chunk 1 foundation implemented: app scaffold, config, schema, repositories, and Clerk auth sync.
- Chunk 2 core API paths implemented: Stripe billing webhook, telephony service boundary, authenticated websocket gate, and LiveKit inbound dispatch webhook.
- Chunk 3 initial agent and post-call layers implemented: provider registry, prompt builder, worker entrypoint scaffold, and post-call lifecycle scaffolding.
- Stripe activation for self-serve onboarding now happens on the first fresh `invoice.paid` instead of `customer.subscription.created`, so subscription activation, minute allocation, and provisioning are tied to a paid invoice.
- Billing checkout is narrowed to the `starter` plan at the API and web layers.
- Phone provisioning now has a durable `phone_number_provisionings` record with retryability, failure reason, linked number, and France-only enforcement.
- Onboarding status now exposes `subscription_status`, `plan_tier`, `minutes_remaining`, `phone_number`, `phone_number_status`, `routing_enabled`, `agent_setup_complete`, `overall_status`, and `can_retry_provisioning` through `GET /api/onboarding`, plus `POST /api/onboarding/retry-provisioning`.
- Agent config API now exposes authenticated read/update of the editable runtime fields through `GET /api/agent/config` and `PATCH /api/agent/config`.
- `PATCH /api/agent/config` now treats `is_enabled` as a synchronous telephony toggle, switching the assigned Telnyx number between `app-active` and `app-disabled` in the same request and rolling the config change back if the provider update fails.
- New Clerk users now get a persisted default `agent_configs` row automatically, and `GET /api/agent/config` no longer depends on a frontend-synthesized fallback object.
- `PATCH /api/agent/config` now enforces self-serve readiness gates before enabling routing: active paid subscription, successful France provisioning, assigned number readiness, and complete agent setup.
- Contract details and usage examples for that surface are documented in [agent-config-api.md](agent-config-api.md).
- Call history API now exposes `GET /api/calls`, `GET /api/calls/{call_id}`, and `DELETE /api/calls/{call_id}` for authenticated users.
- User-facing call delete is now a soft delete: deleted calls disappear from list/detail APIs, while transcript rows and recording objects remain available for admin/manual recovery later.
- Live call recordings now use LiveKit room composite egress with `audio_only=true`, writing one mixed recording directly to the recordings bucket instead of relaying audio bytes through the agent completion API.
- Recording timing is now tightened around the actual conversation window: SIP caller join creates and dispatches the call, agent join starts egress, and SIP caller leave attempts to stop egress early.
- Recording retention is bucket-managed: when the recording object expires from storage lifecycle, call detail now degrades to `recording_url = null` while keeping the call and transcript.
- Contract details and usage examples for call history are documented in [call-history-api.md](call-history-api.md).
- Call summaries are now generated through a provider-agnostic summary layer, with Gemini configured as the default provider.
- Completed calls now persist both `summary_text` and structured `summary_data` on the `calls` row.
- Summary generation is non-blocking: if the provider fails or returns invalid output, call completion still succeeds and summary fields stay `null`.
- Billing and usage API now exposes authenticated read endpoints for subscription state, usage balance, and usage ledger history.
- Billing and usage API now exposes hosted Stripe action endpoints for checkout and billing portal sessions instead of mutating subscriptions directly in the app backend.
- Contract details and usage examples for that surface are documented in [billing-usage-api.md](billing-usage-api.md).
- Internal and integration surfaces are documented in [integration-endpoints.md](integration-endpoints.md).
- Realtime delivery is an optional observer and is disabled by default. With `REALTIME_ENABLED=false`, the API does not register `/ws`, construct the realtime Redis event bus, or start its fanout task. Dashboard correctness continues to rely on authenticated PostgreSQL-backed API reads and explicit route revalidation after successful mutations.

## Known Contract Drift

- Product-facing docs may still describe a broader planned backend surface than what is currently implemented in `apps/api`. Treat this file and the focused docs under `docs/architecture/` as the current source for implemented backend contracts.
- User-facing call delete is currently a soft delete, not a destructive delete. The current call-history contract is documented in [call-history-api.md](call-history-api.md).
- The optional realtime endpoint is `/ws` with first-message auth, but it exists only when `REALTIME_ENABLED=true`; the normal production default returns `404`. Older product docs may still describe it as always available or use a more specific path shape.
- Realtime must not be enabled for customer use until its identity key is made consistent. Current API and agent publishers address Redis channels with the local internal user UUID, while WebSocket authentication subscribes the connection with the Clerk subject ID. Those keys do not match, so delivery is not reliable even though both sides work independently.

## Verified Locally

- API tests covering health, auth, billing, telephony, realtime, LiveKit dispatch, repository flow, and post-call lifecycle.
- Realtime tests now cover disabled-by-default configuration, absence of the `/ws` route and Redis/fanout resources while disabled, the explicit authenticated enabled path, and durable call acceptance when realtime is absent or publishing fails.
- Agent config API tests now cover bootstrapped first-run config reads, readiness-gated enables, successful telephony toggles, and rollback on telephony failure.
- Call history API tests now cover visible-call listing, transcript detail, fresh recording URL minting, and soft-delete behavior.
- LiveKit recording provider tests now cover audio-only room composite egress request shaping, explicit stop behavior, and provider-failure wrapping.
- LiveKit dispatch service tests now cover recording metadata persistence, delayed start on agent join, early stop on SIP leave, and non-blocking recording start/stop failure behavior.
- Summary service tests now cover structured-summary success, malformed provider output, and non-blocking provider failure.
- Billing query service tests now cover subscription lookup, usage snapshot assembly, and usage-ledger ordering.
- Billing session service tests now cover hosted Stripe checkout price mapping, starter-only enforcement, and portal precondition validation.
- Billing router tests now cover read-side contract behavior plus checkout/portal session state handling.
- Stripe webhook tests now cover subscription-shell creation on `customer.subscription.created`, paid-invoice activation, invoice metadata bootstrap, and no double-crediting/provisioning on later invoices.
- Phone provisioning worker tests now cover durable provisioning state persistence, retryable failures, linked-number success, and France-only defaults.
- Onboarding service and router tests now cover status precedence, assigned-number visibility, retryability, and retry enqueue behavior.
- Agent tests covering prompt building, pipeline config selection, Gemini STS runtime construction, and runtime event emission.
- API Docker image build.
- Agent Docker image build.
- Queue-backed call finalization is now covered locally so call persistence no longer depends on the LiveKit agent surviving shutdown long enough to wait on the full API response.
- The standalone local infrastructure and application stack is defined in
  [compose.dev.yaml](../../compose.dev.yaml) with PostgreSQL 17.8, Redis
  7.4.7, and MinIO.
- LiveKit SIP participant field mapping reviewed against the official docs on 2026-03-15: `sip.phoneNumber` is the caller number for inbound trunks and `sip.trunkPhoneNumber` is the dialed trunk number.
- Agent runtime selection is now explicit per user via `agent_config.pipeline_mode`:
  - `stt_llm_tts` remains the default and keeps Speechmatics/Deepgram + Gemini + TTS composition
  - `sts` uses Gemini Live native audio with Gemini-managed turn detection and no external STT/TTS path

## Staging Smoke Status

Partially executed on 2026-03-16.

Self-serve France MVP code path updated locally on 2026-04-12. The real-provider staging path below still needs to be rerun against the new onboarding flow before launch.

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
  - `+33******74`
  - `phone_number_type = national`
  - `upfront_cost = 1.00000 USD`
  - `monthly_cost = 0.50000 USD`
- Subscription activation now persists even when number purchase is intentionally disabled, with a pending `phone_number_provisioning_review_required` notification instead of a `500`.
- A real forwarded call on `+33******99` persisted a completed `calls` row, transcript rows, a `call_completed` usage ledger entry, and a non-blocking failed notification row when Firebase credentials were not configured.

Not yet fully verified in staging:

- The updated self-serve path where the first fresh `invoice.paid` activates the subscription, allocates minutes, and enqueues provisioning for a brand-new customer.
- A fresh later `invoice.paid` event after the persisted subscription exists. Re-sending the same Stripe event id is deduplicated by `webhook_events`, so it does not create a second reset row.
- Real Telnyx purchase and `app-active` / `app-disabled` switching with `TELNYX_ORDERING_ENABLED=true` for the self-serve France flow.
- The new queue-backed call finalization flow with the dedicated `worker` service in Compose.
- LiveKit room composite egress writing one mixed recording directly into the configured bucket.
- Fresh signed recording access from `GET /api/calls/{call_id}` after a real egress-created recording exists.
- End-to-end self-serve onboarding state transitions in the dashboard: `subscription_active`, `provisioning_number`, `setup_required`, `ready_to_enable`, and `live`.
- End-to-end call persistence, transcript capture, summary generation, and actual minute deduction for the Stripe-backed user.
- Hosted Stripe Checkout and Billing Portal session creation against real API credentials and configured price ids.

Ready for manual execution once these external credentials and endpoints are available:
- Clerk issuer, JWKS URL, and webhook secret
- Stripe webhook secret and live test-mode subscription objects
- Telnyx API key and active/disabled connection IDs
- LiveKit URL, API key, and API secret
- Stripe secret key, price ids, and checkout redirect URLs
- Speechmatics, Deepgram, and ElevenLabs credentials as needed for `pipeline_mode="stt_llm_tts"`
- Reachable staging Postgres, Redis, and S3-compatible storage if not using the local Compose stack

## Remaining Manual Verification

- Fresh Stripe `invoice.paid` event for the persisted subscription to verify `invoice_paid_reset` in `usage_ledgers`.
- Real Telnyx purchase and active/disabled switching once you deliberately enable ordering.
- Real LiveKit webhook verification and agent dispatch against the purchased number `+33******99`.
- Queue-backed finalization with the dedicated `worker` service during a real forwarded call.
- One real forwarded call that confirms `recording_object_key` and `recording_egress_id` are persisted and that the bucket contains the mixed audio file.
- End-to-end forwarded phone call with transcript, summary, and minute deduction for the Stripe-backed user.
- Hosted Stripe Checkout and Billing Portal session creation with the configured success/cancel URLs.

## Blockers For Full Staging Smoke Path

- Telnyx ordering is intentionally disabled by default through `TELNYX_ORDERING_ENABLED=false` to prevent real purchases during verification.
- The purchased number currently belongs to the seeded `staging-local-user`, not the new Stripe-backed Clerk user.
- A new billing-cycle or equivalent fresh Stripe invoice event is required to verify the minute-reset path without bypassing webhook idempotency.
