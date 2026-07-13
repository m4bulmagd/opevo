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

WebSocket endpoint for authenticated per-user realtime events.

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

Current pushed event types include:

- `call_started`
- `call_ended`

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
  ],
  "recording_bytes_base64": null
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
- idempotently commits the sequence-bearing recovery tail before touching Redis
- enqueues queue-backed finalization instead of doing full call persistence inline
- returns `503` when the queue is unavailable, after any valid recovery rows are durable
- finalization reconstructs the complete ordered transcript from PostgreSQL for summary generation
- requires the same call-scoped JWT ownership checks as transcript append
- the static `AGENT_INTERNAL_API_TOKEN` fallback exists only in development

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
- on SIP `participant_left`, attempts to stop the active room recording early so the mixed file better matches the conversation window
- recording start/stop failures remain non-blocking
- returns `202 Accepted`

## Notes

- These endpoints are operational/integration surfaces, not frontend product APIs.
- Live call recordings are started through LiveKit egress and written directly to the recordings bucket; the internal completion endpoint no longer needs recording bytes for the primary live-call recording path.
- User-facing API docs live separately in:
  - [agent-config-api.md](agent-config-api.md)
  - [billing-usage-api.md](billing-usage-api.md)
  - [call-history-api.md](call-history-api.md)
