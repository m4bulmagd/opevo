# Backend Staging Smoke Runbook

This runbook is the manual verification path for the provider-backed staging
deployment.

## Scope

Use this to verify:

- API boot with real Postgres and Redis
- `worker-lifecycle` and `worker-background` boot with real Postgres, Redis,
  and storage credentials
- agent boot with real LiveKit credentials
- Clerk webhook delivery
- Stripe subscription activation and invoice reset handling
- Telnyx number provisioning and active/disabled switching
- LiveKit dispatch on inbound SIP participant join
- one end-to-end forwarded call with persisted call data

## Prerequisites

- A public HTTPS URL for the API if provider webhooks are not hitting a deployed staging host
- LiveKit Cloud project with SIP trunk configured
- Telnyx account with:
  - one active connection id pointing to the LiveKit SIP trunk
  - one disabled connection id pointing to the unavailable app/message
- Clerk app with webhook support
- Stripe test-mode account
- Google Gemini API key for `pipeline_mode=sts`
- Speechmatics, Deepgram, and ElevenLabs credentials as needed for `pipeline_mode=stt_llm_tts`

## Env Files

Fill these local files before starting:

- `apps/web/.env`
- `apps/api/.env`
- `apps/agent/.env`

For the standard local Clerk stack, keep web publishable/secret keys in
`apps/web/.env`, and API verifier/webhook values in `apps/api/.env`. Compose
defaults `AUTH_MODE` to Clerk, but each application still validates its own
required credentials.

Minimum Clerk values from `apps/web/.env`:

```dotenv
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_replace_me
CLERK_SECRET_KEY=sk_test_replace_me
```

Minimum required values from `apps/api/.env`:

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_call
REDIS_URL=redis://localhost:6379/0
AGENT_DISPATCH_JWT_SECRET=<long-random-api-and-worker-secret>
AUTH_MODE=clerk
CLERK_ISSUER=https://your-instance.clerk.accounts.dev
CLERK_AUTHORIZED_PARTIES=http://127.0.0.1:3000,http://localhost:3000
CLERK_JWKS_URL=https://your-instance.clerk.accounts.dev/.well-known/jwks.json
CLERK_WEBHOOK_SECRET=whsec_...
STRIPE_WEBHOOK_SECRET=<your-stripe-webhook-secret>
STRIPE_SECRET_KEY=<your-stripe-secret-key>
STRIPE_PRICE_STARTER=<your-starter-price-id>
STRIPE_CHECKOUT_SUCCESS_URL=<your-checkout-success-url>
STRIPE_CHECKOUT_CANCEL_URL=<your-checkout-cancel-url>
STRIPE_BILLING_PORTAL_RETURN_URL=<your-server-owned-portal-return-url>
LIVEKIT_URL=<your-livekit-url>
LIVEKIT_API_KEY=<your-livekit-api-key>
LIVEKIT_API_SECRET=<your-livekit-api-secret>
LIVEKIT_AGENT_NAME=ai-call-agent
TELNYX_API_KEY=<your-telnyx-api-key>
TELNYX_ACTIVE_CONNECTION_ID=<your-telnyx-livekit-connection-id>
TELNYX_DISABLED_CONNECTION_ID=<your-telnyx-disabled-connection-id>
TELNYX_ORDERING_ENABLED=false
STORAGE_BUCKET_NAME=recordings
S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_REGION=us-east-1
FIREBASE_CREDENTIALS_JSON=
```

`CLERK_JWT_KEY` is still supported by the code as a fallback, but `CLERK_JWKS_URL` is the easier option for this repo because it avoids multiline PEM handling in `.env`.

Minimum required values from `apps/agent/.env`:

```dotenv
LIVEKIT_URL=<same-livekit-url>
LIVEKIT_API_KEY=<same-livekit-api-key>
LIVEKIT_API_SECRET=<same-livekit-api-secret>
LIVEKIT_AGENT_NAME=ai-call-agent
API_BASE_URL=http://api:8000
REDIS_URL=redis://redis:6379/0
AGENT_DEBUG_STREAMS=true
AGENT_MIN_ENDPOINTING_DELAY=0.25
AGENT_MAX_ENDPOINTING_DELAY=1.5
LIVEKIT_SILERO_VAD_ENABLED=true
LIVEKIT_TURN_DETECTOR_ENABLED=true
SPEECHMATICS_API_KEY=<your-speechmatics-key>
SPEECHMATICS_TURN_DETECTION_MODE=adaptive
GEMINI_API_KEY=<your-google-gemini-key>
# Optional compatibility fallback if your local env already uses it:
# GEMINI_API_KEY=<your-google-gemini-key>
MISTRAL_API_KEY=<optional-or-placeholder>
ELEVENLABS_API_KEY=<your-elevenlabs-key>
ELEVENLABS_VOICE_ID=<your-elevenlabs-voice-id>
DEEPGRAM_API_KEY=<your-deepgram-key>
```

## Boot Commands

From the repository root, start the core stack first:

```bash
docker compose -f compose.dev.yaml up -d --build
```

After the core services are healthy, start the provider-backed voice worker:

```bash
docker compose -f compose.dev.yaml --profile voice up -d --build agent
docker compose -f compose.dev.yaml --profile voice logs -f api worker-lifecycle worker-background agent
```

Expected signals:

- `migrate` exits successfully before the API and both workers start
- `api` starts `uvicorn` on port `8000`
- `worker-lifecycle` and `worker-background` start `arq` without import or
  Redis connection failures; their health keys are
  `opevo:worker:call-lifecycle:health` and
  `opevo:worker:background:health`
- `agent` registers with LiveKit without credential or import failures

## Basic Boot Verification

### Health

```bash
curl -s http://localhost:8000/healthz
```

Expected:

```json
{"status":"ok"}
```

### Tables Exist

```bash
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "\dt"
```

Expected tables include:

- `users`
- `subscriptions`
- `usage_ledgers`
- `phone_numbers`
- `calls`
- `call_messages`
- `notifications`
- `webhook_events`

## Public Webhook Targets

Set these provider webhook destinations to your public API base URL:

- Clerk: `POST <PUBLIC_API_URL>/webhooks/clerk`
- Stripe: `POST <PUBLIC_API_URL>/webhooks/stripe`
- LiveKit: `POST <PUBLIC_API_URL>/webhooks/livekit`

If you are tunneling locally, define:

```bash
export PUBLIC_API_URL=https://<your-public-host>
```

## Step 1: Clerk Webhook Smoke

Trigger a real `user.created` event from Clerk by creating a new test user or using Clerk's webhook test tool.

Then verify the user exists:

```bash
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select clerk_user_id, email, status from users order by created_at desc limit 5;"
```

Expected:

- the new Clerk user appears in `users`
- API logs show `202` on `/webhooks/clerk`

## Step 2: Stripe Subscription Activation Smoke

Create a test-mode Stripe subscription event that includes:

- `metadata.clerk_user_id=<the Clerk user id from Step 1>`
- a price whose `lookup_key` is `starter`

The safest path is to create a real test subscription in Stripe rather than relying on a generic trigger event.

Then verify:

```bash
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select stripe_subscription_id, plan_tier, status, allocated_minutes from subscriptions order by created_at desc limit 5;"
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select e164, provider_connection_name, is_active from phone_numbers order by created_at desc limit 5;"
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select event_type, minutes_delta, balance_after from usage_ledgers order by created_at desc limit 10;"
```

Expected:

- one `subscriptions` row for the user
- one `usage_ledgers` row with `event_type = subscription_activated`
- when `TELNYX_ORDERING_ENABLED=false`, one `notifications` row with `notification_type = phone_number_provisioning_review_required`
- API logs show `202` on `/webhooks/stripe`

Current verified staging result from 2026-03-16:

- Stripe activation persisted the subscription and starter-minute allocation
- Telnyx selection stayed in non-buying mode and found a valid national candidate:
  - `+33******74`
  - `upfront_cost = 1.00000 USD`
  - `monthly_cost = 0.50000 USD`
- no real number order was placed while `TELNYX_ORDERING_ENABLED=false`

## Step 3: Stripe Invoice Reset Smoke

Trigger an `invoice.paid` event for the same Stripe subscription.

Then verify:

```bash
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select event_type, minutes_delta, balance_after from usage_ledgers order by created_at desc limit 10;"
```

Expected:

- a new `usage_ledgers` row with `event_type = invoice_paid_reset`
- `balance_after` resets to the plan minutes

Important:

- replaying the same Stripe `invoice.paid` event id will be ignored by the webhook idempotency store
- use a fresh invoice event if you want to verify the reset path in staging

## Step 3A: Hosted Billing Session Smoke

Verify the new billing action endpoints with a real authenticated user token.

Checkout session:

```bash
curl -s http://localhost:8000/api/billing/checkout-session \
  -H "Authorization: Bearer <clerk-session-token>" \
  -H "Content-Type: application/json" \
  -d '{"plan_tier":"starter"}'
```

Expected:

- response contains a Stripe Checkout URL
- response is `409` instead when the user already has an active subscription

Portal session:

```bash
curl -s http://localhost:8000/api/billing/portal-session \
  -H "Authorization: Bearer <clerk-session-token>" \
  -H "Content-Type: application/json" \
  -d '{"return_url":"https://your-app.example.com/dashboard/billing"}'
```

Expected:

- response contains a Stripe Billing Portal URL for subscribed users
- response is `409` when no Stripe customer exists yet
- the optional caller `return_url` must share the configured URL's exact origin; Stripe always receives `STRIPE_BILLING_PORTAL_RETURN_URL`

## Step 4: Telnyx Active / Disabled Switch Smoke

Only run this after you intentionally enable real purchases:

```dotenv
TELNYX_ORDERING_ENABLED=true
```

Then replay the subscription activation event or create a new qualifying subscription, and confirm the provisioned number is connected to the active Telnyx application.

Then force a disable path by driving the user balance to zero via a real completed call or by using the existing call completion path.

Verify in the database:

```bash
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select e164, provider_connection_name, is_active from phone_numbers order by created_at desc limit 5;"
```

Expected:

- before exhaustion: `provider_connection_name = app-active`
- after exhaustion: `provider_connection_name = app-disabled` and `is_active = f`

## Step 5: LiveKit Dispatch Smoke

Confirm LiveKit is configured to send webhooks to:

- `POST <PUBLIC_API_URL>/webhooks/livekit`

Then place one real inbound call through the forwarded Telnyx number.

Existing purchased number available for the manual smoke path:

- `+33******99`

Current caveat:

   - this number belongs to the seeded `staging-local-user`, not the new Stripe-backed user created during the 2026-03-16 Stripe smoke

Watch logs:

```bash
docker compose -f compose.dev.yaml --profile voice logs -f api worker-lifecycle worker-background agent
```

Expected API log signals:

- `livekit webhook received event=participant_joined`
- `livekit dispatch created room=...`

Expected agent log signals:

- worker connects successfully
- session starts
- transcript/debug lines appear if `AGENT_DEBUG_STREAMS=true`

## Step 6: End-To-End Call Persistence Smoke

Complete one real forwarded call and then verify persistence:

```bash
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select livekit_room_id, caller_number, status, duration_seconds, minutes_charged, summary_text from calls order by created_at desc limit 5;"
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select summary_data from calls order by created_at desc limit 5;"
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select speaker, text, sequence_number from call_messages order by created_at desc limit 20;"
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select notification_type, status from notifications order by created_at desc limit 10;"
docker compose -f compose.dev.yaml exec -T postgres psql -U postgres -d ai_call -c "select event_type, minutes_delta, balance_after from usage_ledgers order by created_at desc limit 10;"
```

Expected:

- one completed `calls` row
- persisted transcript rows in `call_messages`
- one notification row for call completion, even if Firebase delivery itself fails
- one usage ledger row for `call_completed`
- `summary_text` is populated with the generated summary result
- `summary_data` is populated when summary generation succeeds

Expected worker log signals:

- `worker-lifecycle` runs `call_finalization_job` after the agent receives `202 Accepted` from `/api/agent/calls/{call_id}/complete`
- no duplicate finalization occurs if the same completion payload is retried
- `worker-background` processes its separate queue without delaying the
  lifecycle check; inspect `opevo.worker.queue.depth{queue_class}` and
  `opevo.worker.queue.oldest_due.age{queue_class}` as bounded diagnostics.

Optional storage verification:

```bash
curl -s http://localhost:9001
```

Then check the `recordings` bucket in MinIO console.

## Step 7: Call History API Smoke

After at least one completed call exists for the test user, verify the user-facing history API:

```bash
curl -s http://localhost:8000/api/calls \
  -H "Authorization: Bearer <clerk-session-token>"
```

Expected:

- the completed call appears in the `calls` list
- deleted calls do not appear

Then verify detail:

```bash
curl -s http://localhost:8000/api/calls/<CALL_ID> \
  -H "Authorization: Bearer <clerk-session-token>"
```

Expected:

- transcript lines are present and ordered
- `recording_url` is present when a recording exists

Then verify terminal-call removal:

```bash
curl -i -X DELETE http://localhost:8000/api/calls/<CALL_ID> \
  -H "Authorization: Bearer <clerk-session-token>"
curl -i http://localhost:8000/api/calls/<CALL_ID> \
  -H "Authorization: Bearer <clerk-session-token>"
```

Expected:

- delete returns `204 No Content`
- subsequent detail returns `404 Not Found`
- the call disappears from `GET /api/calls`
- transcript, caller, summary, and playback content is inaccessible immediately
- when recording cleanup metadata exists, provider stop and exact-object deletion
  continue asynchronously and do not change the `204` response

## Step 8: Gemini STS Smoke

Configure one test user with:

- `agent_config.pipeline_mode = sts`
- `sts_provider = gemini`
- a valid `GEMINI_API_KEY` in `apps/agent/.env`

Then place one real inbound call for that user and watch:

```bash
docker compose -f compose.dev.yaml --profile voice logs -f api worker-lifecycle worker-background agent
```

Expected evidence:

- API logs still show `livekit dispatch created`
- agent joins the room without trying to construct external STT, TTS, Silero VAD, or the LiveKit turn detector for that call
- transcript rows are still persisted in `call_messages`
- `worker-lifecycle` still completes `call_finalization_job`
- `calls.summary_text`, `usage_ledgers`, and `notifications` continue to populate through the existing backend flow

Recommended comparison:

- place one short call on `stt_llm_tts`
- place one short call on `sts`
- compare time-to-first-agent-response from logs or subjective call feel before deciding whether to expand STS beyond opt-in use

## Success Criteria

The staging smoke is successful when all of these are true:

- API stays healthy during provider callbacks
- `worker-lifecycle` stays healthy and drains queued call-finalization jobs;
  `worker-background` stays healthy on its separate queue
- agent starts and handles a real LiveKit dispatch
- Clerk creates a local `users` row
- Stripe activation creates subscription, number, and minutes ledger entries
- Stripe renewal resets minutes
- Telnyx number activation and disablement match balance state
- one real call persists call, transcript, notification, and usage rows
- call history list/detail/delete behave correctly for the authenticated user
- one real `pipeline_mode=sts` call persists transcript and finalization state without breaking the backend lifecycle

## Record Results

After the run, update `docs/architecture/backend-context.md` with:

- date of the smoke run
- which steps passed
- which steps failed
- exact blocker for any failed step
- whether the staging path is ready for the next release decision
