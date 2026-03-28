# Call History API Design

## Goal

Add the user-facing backend API needed for MVP call history:

- list calls
- fetch one call with transcript
- soft-delete a call
- mint a fresh recording access URL on demand

This API is for authenticated end users only. Deleted calls disappear from normal user history, while the underlying transcript and recording remain available for admin/manual recovery later.

## Scope

### Included

- `GET /api/calls`
- `GET /api/calls/{call_id}`
- `DELETE /api/calls/{call_id}`
- soft-delete support on calls
- transcript retrieval for call detail
- fresh signed recording URL generation for call detail

### Excluded

- admin recovery endpoints
- permanent delete
- background retention cleanup
- recording object deletion
- pagination/filter/search beyond a basic newest-first history list

## API Contract

### `GET /api/calls`

Returns the authenticated user’s non-deleted calls, newest first.

Response shape:

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
      "summary_text": "Caller request: Opening hours.",
      "has_recording": true
    }
  ]
}
```

### `GET /api/calls/{call_id}`

Returns one non-deleted call for the authenticated user, with ordered transcript and a fresh recording URL if a recording exists.

Response shape:

```json
{
  "id": "uuid",
  "status": "completed",
  "caller_number": "+33123456789",
  "started_at": "2026-03-28T10:00:00Z",
  "ended_at": "2026-03-28T10:01:00Z",
  "duration_seconds": 60,
  "minutes_charged": 1,
  "summary_text": "Caller request: Opening hours.",
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

If the call has no stored recording, `recording_url` is `null`.

### `DELETE /api/calls/{call_id}`

Soft-deletes the call for the authenticated user.

Behavior:

- returns `204 No Content` on success
- later `GET /api/calls/{call_id}` returns `404`
- later `GET /api/calls` excludes the call
- transcript rows and recording object remain intact for later admin/manual recovery

## Data Model

Add a nullable `deleted_at` timestamp to `calls`.

This is enough for MVP soft delete. No archive table is needed yet.

User-facing queries must filter out rows where `deleted_at IS NOT NULL`.

The recording object and transcript rows are not deleted. Soft delete only changes user-facing visibility.

## Backend Structure

### Router

[`apps/api/app/routers/calls.py`](/home/i933k/code/ai/bmad-opevo/apps/api/app/routers/calls.py)

Expose:

- `GET /api/calls`
- `GET /api/calls/{call_id}`
- `DELETE /api/calls/{call_id}`

The router should stay thin and delegate business logic to a dedicated service.

### Service

Add `CallHistoryService`.

Responsibilities:

- resolve the authenticated local user from Clerk identity
- list visible calls
- fetch one visible call
- load ordered transcript rows
- mint a fresh recording URL
- soft-delete visible calls

### Repositories

[`call_repository.py`](/home/i933k/code/ai/bmad-opevo/apps/api/app/repositories/call_repository.py)

Add helpers for:

- list visible calls by `user_id`, newest first
- fetch one visible call by `call_id` + `user_id`
- mark `deleted_at`
- optionally fetch a call regardless of deletion when needed internally

[`message_repository.py`](/home/i933k/code/ai/bmad-opevo/apps/api/app/repositories/message_repository.py)

Add ordered transcript retrieval by `call_id`, sorted by `sequence_number`.

### Recording Access

[`recording_service.py`](/home/i933k/code/ai/bmad-opevo/apps/api/app/services/recording_service.py)

Extend recording support so the detail endpoint can mint a fresh access URL for an existing stored recording object.

The current persisted `recording_url` field should not be treated as the user-facing access contract going forward. The detail endpoint should derive or mint the access URL at read time.

## Response Models

Add separate schemas for:

- call history list item
- call history list response
- call transcript line
- call detail response

The existing minimal `CallResponse` is not sufficient for both list and detail use cases.

## Authorization Rules

All calls APIs require a valid Clerk bearer token for a user synced into the local database.

The user may only:

- list their own calls
- fetch their own non-deleted calls
- soft-delete their own non-deleted calls

Cross-user access returns `404`, not `403`.

## Error Handling

### `GET /api/calls`

- always returns `200`
- returns an empty list when the user has no visible calls

### `GET /api/calls/{call_id}`

Return `404` when:

- the call does not exist
- the call belongs to another user
- the call is soft-deleted

### `DELETE /api/calls/{call_id}`

Return `404` when:

- the call does not exist
- the call belongs to another user
- the call is already soft-deleted

Return `204` when soft delete succeeds.

## Testing Plan

Add API coverage for:

- list returns visible calls newest first
- list excludes deleted calls
- detail returns transcript ordered by `sequence_number`
- detail returns a freshly minted recording URL when recording exists
- detail returns `recording_url = null` when no recording exists
- delete sets `deleted_at`
- deleted calls disappear from list
- deleted calls return `404` on detail
- deleting another user’s call returns `404`

## Migration

Add an Alembic migration to add `calls.deleted_at`.

No backfill is required.

## Recommendation

Implement this as a focused backend slice before moving to frontend history views.

It completes the user-facing call-history contract without prematurely adding search, pagination, or admin recovery APIs.
