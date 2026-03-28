# Call History API

This document describes the authenticated call-history endpoints exposed by the API.

## Auth

All endpoints require:

- `Authorization: Bearer <clerk-session-token>`

The token must resolve to a synced local user.

## Endpoints

### `GET /api/calls`

Returns the authenticated user's non-deleted calls, newest first.

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
  ]
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
- returns `404` when the call was soft-deleted
- mints a fresh recording access URL at read time instead of reusing a long-lived stored URL

### `DELETE /api/calls/{call_id}`

Soft-deletes the call for the authenticated user.

Response:

- `204 No Content` on success

Behavior:

- later `GET /api/calls` will no longer include the deleted call
- later `GET /api/calls/{call_id}` will return `404`
- transcript rows and recording objects are not destroyed by this user-facing delete path

## Notes

- User-facing call delete is archival, not destructive.
- Soft-deleted calls stay available in storage/database for admin or recovery workflows later.
- Recording access is intentionally short-lived and generated on demand.
