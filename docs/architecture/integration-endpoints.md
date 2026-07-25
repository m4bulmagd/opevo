# Integration Endpoints

This document describes account-lifecycle APIs plus the non-product-facing
backend endpoints used by local fixtures, internal workers, provider webhooks,
health checks, and realtime clients.

## Account lifecycle

### `GET /api/account`

Authenticated owner read returning only customer-safe lifecycle state:

```json
{
  "status": "deactivating",
  "serving": false,
  "deactivation": {
    "state": "draining_call",
    "requested_at": "2026-07-25T00:00:00Z"
  },
  "reactivation_allowed": false,
  "blocker": "account_deactivating"
}
```

`status` is `active`, `deactivating`, or `inactive`. Active accounts derive
`serving` from central readiness. Deactivating and inactive accounts are always
non-serving. Progress is bounded to `requested`, `disabling_routing`,
`canceling_subscription`, `draining_call`, `releasing_number`, `finalizing`, or
`attention_required`. The response never contains Stripe/Telnyx IDs, retry
counts, raw errors, provider bodies, or credentials.

### `POST /api/account/deactivate`

Authenticated owner command, rate-limited to five requests per minute:

```json
{"confirmation":"DEACTIVATE"}
```

The exact confirmation is schema-enforced. A valid first or repeated request
returns `202 Accepted` with the same safe account shape as `GET /api/account`.
The short entry transaction increments the lifecycle generation, makes the
account and local phone non-serving, disables the agent, and records a single
reference-only `account.deactivate` event. It does not wait for Stripe, Telnyx,
Redis, or an admitted call.

Owner deactivation requests immediate Stripe cancellation with no automatic
proration or refund. The worker disables Telnyx routing, verifies or performs
subscription cancellation, waits for every admitted call to become terminal,
then releases the exact stored number and resets number-cycle activation state.
Only the reconciler sets a phase timestamp after the corresponding provider
success or authoritative verification; request and webhook transactions do not
pre-populate those timestamps.

Identity, confirmed profile/carrier, receptionist content, calls, recordings,
usage, notifications, and billing are retained. Inactive owners keep
authenticated read-only historical access. Reactivation needs a new
generation-matched subscription and resumes at fresh provisioning consent for a
new number, forwarding verification, and explicit go-live.

The private outbox contract is:

```json
{"operation_id":"<deactivation-operation-uuid>"}
```

Its topic is `account.deactivate`, aggregate type is
`account-deactivation-operation`, and aggregate ID equals `operation_id`.
Authentication, provider-contract, or identity-conflict failures leave the
account `deactivating`, expose only bounded attention progress, and require the
operator recovery procedure in `docs/runbooks/deploy.md`.

There is no `DELETE /api/account`: permanent deletion and export are not part
of this lifecycle.

## Development-only call-drain fixtures

These routes are registered only when `APP_ENV=development`. They also require
`AUTH_MODE=local`, `TELEPHONY_MODE=fake`, and the authenticated local owner.
They return the same bounded `local_telephony_disabled` conflict for a
Clerk-authenticated development configuration or non-fake telephony. They are
absent outside development.

### `POST /api/development/call-drain-fixture/start`

Creates and connects one owner-scoped call through the real call repository,
without LiveKit dispatch or Telnyx I/O. The response contains only:

```json
{"call_id":"<call-uuid>"}
```

### `POST /api/development/call-drain-fixture/finish`

Request:

```json
{"call_id":"<call-uuid>"}
```

The route hides foreign call IDs with `404`, then exercises the real agent-end,
finalization-claim, usage, notification, summary-intent, and completion path.
It does not mutate account lifecycle state directly and returns only `call_id`.
These fixtures exist solely for provider-free local acceptance.

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
- returns `503` when the queue is unavailable, after recovery rows, end facts,
  and recording reconciliation intent are durable
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
- `recording.reconcile` uses aggregate type `recording-egress-operation`, an
  aggregate ID equal to the private operation UUID, and a payload containing
  only `{ "operation_id": "..." }`; the worker snapshots durable intent, then
  inspects and reconciles LiveKit and storage outside database transactions
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
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

Behavior:

- verifies the Stripe signature
- persists subscription and usage state through the billing service
- records period-end cancellation and its effective date without stopping
  service; reversal clears that scheduled state
- starts account deactivation only when the current, generation-matched
  subscription reaches final cancellation; the webhook entry transaction leaves
  routing/subscription phase timestamps for the reconciler to set truthfully
- returns `202 Accepted`

### `POST /webhooks/livekit`

Consumes LiveKit room and participant events.

Behavior:

- verifies the LiveKit webhook authorization
- on SIP `participant_joined`, atomically records the matched call and a durable LiveKit dispatch outbox intent after subscription, balance, phone, configuration, and exact-trunk eligibility checks
- on agent `participant_joined`, commits the normal call's private recording
  operation and delayed `recording.reconcile` start intent before attempting one
  mixed LiveKit room-composite egress with `audio_only=true`; provider I/O runs
  only after that database commit
- on SIP `participant_left`, commits the durable end transition and requests
  recording reconciliation even when no provider egress ID is known, before a
  best-effort finalization wakeup
- on signed egress lifecycle events, stores only sanitized identity, room,
  status, and output-location facts, then wakes reconciliation after commit;
  webhook handling performs no LiveKit or storage I/O
- recording start failure remains non-blocking; a late success is persisted on
  the private operation, projected only to a still-visible call, and reconciled
  against any stop or deletion intent without changing terminal state, end
  facts, or accounting
- returns `202 Accepted`

## Notes

- Except for the authenticated account lifecycle routes, these endpoints are
  operational/integration surfaces rather than frontend product APIs.
- Live call recordings are started through LiveKit egress and written directly
  to the recordings bucket. Raw audio blobs are rejected by the completion
  schema, are never placed in Redis, and have no legacy recording-upload worker.
- Owner removal of a terminal call atomically purges and hides local customer
  content and returns `204` without provider or storage I/O. When a private
  recording operation or legacy recording metadata exists, the transaction
  also records stop/delete intent plus reference-only reconciliation work.
  Provider and original-audio cleanup continues asynchronously without retry
  exhaustion; repeated owner removal is idempotent and active calls reject it.
- Firebase push delivery is intentionally absent from the launch post-call path. The authenticated dashboard reads the opaque local notification; private device-token delivery belongs to a later workstream.
- User-facing API docs live separately in:
  - [agent-config-api.md](agent-config-api.md)
  - [billing-usage-api.md](billing-usage-api.md)
  - [call-history-api.md](call-history-api.md)
