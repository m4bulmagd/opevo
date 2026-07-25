# Call History API

This document describes the authenticated call-history endpoints exposed by the API.

## Auth

All endpoints require:

- `Authorization: Bearer <clerk-session-token>`

The token must resolve to a synced local user.

## Endpoints

### `GET /api/calls`

Returns the authenticated user's non-deleted calls, newest first.

Query parameters:

- `q`: optional trimmed search text, maximum 100 characters
- `limit`: page size from 1 through 100, default 20
- `offset`: zero-based row offset, default 0

Search is owner-scoped and excludes removed calls. It matches `summary_text`
and structured `caller_intent` case-insensitively. A fully phone-shaped query
with at least three digits also matches the stored caller number after query
punctuation is removed. When those digits start with a domestic trunk `0`, the
search also tries the digits with exactly that leading zero removed, so `01 87`
matches an E.164 number containing `33187`. Transcripts and arbitrary summary
metadata are not searched.

Rows use `started_at DESC NULLS LAST`, `created_at DESC`, and `id DESC`.

Response:

```json
{
  "calls": [
    {
      "id": "uuid",
      "status": "completed",
      "caller_number": "+33123456789",
      "started_at": "2026-03-28T10:00:00Z",
      "ended_at": "2026-03-28T10:01:00Z",
      "duration_seconds": 60,
      "minutes_charged": 1,
      "summary_text": "Caller asked about opening hours.",
      "has_recording": true
    }
  ],
  "total": 47,
  "limit": 20,
  "offset": 20,
  "has_more": true
}
```

### `GET /api/calls/{call_id}`

Returns one non-deleted call plus transcript and a fresh recording access URL when a recording exists.

Response:

```json
{
  "id": "uuid",
  "status": "completed",
  "caller_number": "+33123456789",
  "started_at": "2026-03-28T10:00:00Z",
  "ended_at": "2026-03-28T10:01:00Z",
  "duration_seconds": 60,
  "minutes_charged": 1,
  "summary_text": "Caller asked about opening hours.",
  "recording_url": "https://signed.example.com/...",
  "transcript": [
    {
      "speaker": "CALLER",
      "text": "What are your opening hours?",
      "sequence_number": 1,
      "created_at": "2026-03-28T10:00:10Z"
    }
  ]
}
```

Behavior:

- returns `404` when the call does not exist
- returns `404` when the call belongs to another user
- returns `404` when the call was removed
- mints a fresh recording access URL at read time instead of reusing a long-lived stored URL
- returns `recording_url = null` when no private original audio is available or
  the playback reference can no longer be signed

### `DELETE /api/calls/{call_id}`

Removes a terminal call and its customer-visible content for the authenticated
owner.

Response:

- `204 No Content` on the first or repeated successful owner removal
- `409 Conflict` with `call_delete_active` while the call is active
- `404 Not Found` for an unknown or cross-tenant call

Behavior:

- the successful local transaction deletes transcript rows, clears caller,
  summary, and playback fields, records the tombstone, and immediately hides
  the call from list, detail, transcript, and playback access
- when a private recording operation or legacy recording metadata exists, the
  same transaction records stop/deletion intent and reference-only
  `recording.reconcile` work
- no LiveKit or storage I/O occurs in the request; provider stop and exact-object
  deletion continue asynchronously without retry exhaustion
- repeated owner removal remains idempotent and does not duplicate cleanup work

## Notes

- Recording access is intentionally short-lived and generated on demand.
- Original audio remains private and available for a visible call until owner
  removal or a separately approved future retention policy.
- Automatic 30-day retention is not enabled. Removal makes no claim that
  provider cleanup, backups, or historical copies are erased synchronously.
