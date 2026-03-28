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

## Internal Agent Completion

### `POST /api/agent/calls/{call_id}/complete`

Internal endpoint used by the voice agent to hand call completion off to the API worker queue.

Headers:

- `X-Agent-Token: <shared AGENT_INTERNAL_API_TOKEN>`

Request body:

```json
{
  "user_id": "uuid",
  "duration_seconds": 42,
  "minutes_remaining": 120,
  "caller_number": "+33123456789",
  "transcript": [
    {
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
- enqueues queue-backed finalization instead of doing full call persistence inline
- requires the configured internal shared secret

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
- on `participant_joined`, attempts inbound dispatch for the matched local phone number
- returns `202 Accepted`

## Notes

- These endpoints are operational/integration surfaces, not frontend product APIs.
- User-facing API docs live separately in:
  - [agent-config-api.md](/home/i933k/code/ai/bmad-opevo/docs/architecture/agent-config-api.md)
  - [billing-usage-api.md](/home/i933k/code/ai/bmad-opevo/docs/architecture/billing-usage-api.md)
  - [call-history-api.md](/home/i933k/code/ai/bmad-opevo/docs/architecture/call-history-api.md)
