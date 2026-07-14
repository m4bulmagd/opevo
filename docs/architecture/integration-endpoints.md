# Integration Endpoints

This document describes the non-product-facing backend endpoints used by internal workers, provider webhooks, health checks, and realtime clients.

## Health

### `GET /healthz`

Returns basic API liveness.

Response:

```json
{"status":"ok"}
```

## Realtime WebSocket

### `GET /ws`

Optional WebSocket endpoint for authenticated per-user observer events. It is not part of the launch-critical path and is not registered unless `REALTIME_ENABLED=true`; with the default `REALTIME_ENABLED=false`, HTTP requests to `/ws` return `404` and WebSocket upgrades are not accepted.

The dashboard does not consume this endpoint. Its authoritative state comes from authenticated PostgreSQL-backed API reads, with affected routes revalidated after successful mutations. Missing, delayed, duplicated, or failed realtime delivery must not change call acceptance, finalization, billing, provisioning, recording, onboarding, or dashboard correctness.

Do not enable this capability for customer use yet. Current API and agent publishers use the local internal user UUID as the Redis channel key, while WebSocket authentication registers connections by Clerk subject ID. The publisher and subscriber identity key must be unified before realtime is re-enabled.

Expected first client message:

```json
{
  "type": "auth",
  "token": "<clerk-session-token>"
}
```

Behavior:

- if auth is missing or malformed, the server sends:

```json
{"type":"error","detail":"auth_required"}
```

- then closes with code `1008`
- after auth, the client may send:

```json
{"type":"ping"}
```

- and receives:

```json
{"type":"pong"}
```

The observer event contract currently defines:

- `call_started`
- `call_ended`

`call_started` is published only as a best-effort notification after durable SIP-call acceptance commits. Publisher failure is safely reported and cannot change the accepted result. The API realtime service has no production callsite for `publish_call_ended`; no launch behavior depends on it.

## Internal Agent Transcript Append

### `POST /api/agent/calls/{call_id}/transcript`

Internal endpoint used by the dispatched voice agent to persist one final transcript segment without blocking the audio callback.

Headers:

- `X-Agent-Token: <call-scoped dispatch JWT>`

Request body:

```json
{
  "sequence_number": 1,
  "speaker": "CALLER",
  "text": "Hello"
}
```

Response:

```json
{
  "status": "stored",
  "sequence_number": 1
}
```

Behavior:

- requires a JWT whose call, user, and agent-configuration claims match locked database state
- accepts sequence numbers starting at `1`, `CALLER` or `AGENT`, and normalized nonblank text of at most 4,000 Unicode code points
- returns `stored` for the first insert and `duplicate` only for an identical replay
- returns `409 sequence_conflict` when the same sequence already has different normalized content
- never overwrites an existing segment
- an acknowledged segment is durable across an agent-process crash; an unacknowledged segment remains only in the bounded in-memory recovery tail and survives graceful finalization, not a hard process crash

## Internal Agent Completion

### `POST /api/agent/calls/{call_id}/complete`

Internal endpoint used by the voice agent to hand call completion off to the API worker queue.

Headers:

- `X-Agent-Token: <call-scoped dispatch JWT>`

Request body:

```json
{
  "duration_seconds": 42,
  "transcript": [
    {
      "sequence_number": 17,
      "speaker": "CALLER",
      "text": "Hello"
    }
  ]
}
```

Response:

```json
{
  "status": "accepted",
  "queued": true,
  "job_id": "call-finalization:..."
}
```

Behavior:

- returns `202 Accepted`
- idempotently commits the sequence-bearing recovery tail and the first durable end facts before touching Redis
- freezes the first accepted `ended_at` and `duration_seconds`; duplicate completion cannot overwrite them
- transitions an accepted call to `ending`, then enqueues only `{ "call_id": "..." }`
- returns `503` when the queue is unavailable, after recovery rows, end facts, and any recording-stop intent are durable
- rejects raw recording fields and agent-supplied accounting or ownership fields
- requires the same call-scoped JWT ownership checks as transcript append
- rejects static shared tokens in every environment

### Durable finalization and reconciliation

Call state is constrained to:

```text
pending -> connected -> ending -> finalizing -> completed
   |                                      |
   +---------------> failed <------------+
```

Generic transitions cannot skip graph edges and terminal states never regress. A scoped completion may repair a missing agent-join webhook while establishing bounded start/end facts, but this is not exposed as a generic `pending -> ending` transition.

Finalization uses two short PostgreSQL transactions:

1. claim `ending -> finalizing`, increment and commit an attempt generation;
2. for that exact generation, atomically commit the usage debit, call facts, one opaque dashboard notification, reference-only outbox intents, and `completed`.

The second transaction performs no Gemini, LiveKit, Firebase, Telnyx, storage, or Redis provider I/O. `completed` means the durable local state and required intents committed; provider work may still be pending.

Post-call provider work is delivered from reference-only outbox events:

- `summary.generate` uses aggregate type `call-summary`, snapshots the ordered PostgreSQL transcript and maximum sequence, and persists structured summary data plus its coverage watermark only after revalidating that maximum under a fresh lock; terminal recovery stores a new versioned intent when it adds a sequence
- `recording.stop` uses aggregate type `call-recording` and reconciles LiveKit egress through `ensure_stopped(egress_id)`
- `phone.disable` re-derives current routing eligibility before changing provider state

The worker runs call reconciliation every minute. It recovers stale pending, connected, ending, and finalizing calls in bounded batches with PostgreSQL locking, shared LiveKit dispatch advisory locks for pending timeouts, and committed attempt-generation leases. Charged calls are repaired to `completed`; they are never failed solely because retry attempts were exhausted.

## Provider Webhooks

### `POST /webhooks/clerk`

Consumes Clerk user-sync events.

Requirements:

- Svix/Clerk webhook signature headers
- currently used for `user.created`

Behavior:

- verifies the Svix signature
- syncs the local user row
- returns `202 Accepted`

### `POST /webhooks/stripe`

Consumes Stripe billing events.

Requirements:

- valid `Stripe-Signature` header

Currently handled events:

- `customer.subscription.created`
- `invoice.paid`

Behavior:

- verifies the Stripe signature
- persists subscription and usage state through the billing service
- returns `202 Accepted`

### `POST /webhooks/livekit`

Consumes LiveKit room and participant events.

Behavior:

- verifies the LiveKit webhook authorization
- on SIP `participant_joined`, atomically records the matched call and a durable LiveKit dispatch outbox intent after subscription, balance, phone, configuration, and exact-trunk eligibility checks
- on agent `participant_joined`, attempts to start one mixed room recording through LiveKit room composite egress with `audio_only=true`
- on SIP `participant_left`, commits the durable end transition and recording-stop intent before a best-effort finalization wakeup
- recording start failure remains non-blocking; if start succeeds after the call already became terminal, the new egress is immediately reconciled through `ensure_stopped`; an uncertain cleanup durably attaches only provider references and a retry intent without changing terminal state, end facts, or accounting
- returns `202 Accepted`

## Notes

- These endpoints are operational/integration surfaces, not frontend product APIs.
- Live call recordings are started through LiveKit egress and written directly to the recordings bucket. Raw audio blobs are rejected by the completion schema, are never placed in Redis, and have no legacy recording-upload worker.
- Firebase push delivery is intentionally absent from the launch post-call path. The authenticated dashboard reads the opaque local notification; private device-token delivery belongs to a later workstream.
- User-facing API docs live separately in:
  - [agent-config-api.md](agent-config-api.md)
  - [billing-usage-api.md](billing-usage-api.md)
  - [call-history-api.md](call-history-api.md)
