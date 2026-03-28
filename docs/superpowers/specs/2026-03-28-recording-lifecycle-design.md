# Recording Lifecycle Design

**Date:** 2026-03-28

**Goal:** Finish the backend recording lifecycle for MVP by replacing the unused API-side recording blob path with LiveKit-managed mixed-call recording, while keeping retention entirely in the S3 bucket lifecycle and returning `recording_url = null` when recordings have expired.

## Problem

The backend can currently store recording bytes sent through `POST /api/agent/calls/{call_id}/complete`, but the live agent path never sends any recording bytes. As a result:

- real calls persist transcripts and summaries but not audio recordings
- the bucket remains empty for old calls
- the current recording path is only exercised in tests with synthetic `recording_bytes`

For MVP, we want one mixed recording per call, not per-speaker tracks, and we do not want the app to own retention cleanup.

## Requirements

- Record one full mixed call audio file per LiveKit room.
- Do not capture or persist separate caller/agent tracks.
- Do not relay large base64 audio blobs through the agent completion API for live calls.
- Store recordings in the existing S3-compatible bucket.
- Treat the bucket lifecycle policy as the only 30-day retention mechanism.
- Keep calls and transcripts after recording expiry.
- Return `recording_url = null` if the object no longer exists or access cannot be minted.

## Recommended Approach

Use LiveKit Room Composite Egress with `audio_only=true` and direct file output to the recordings bucket.

Why this approach:

- It matches the desired output: one mixed call recording.
- It avoids moving large audio payloads through the agent/API boundary.
- It aligns naturally with room lifecycle and existing LiveKit infrastructure.
- It keeps retention where you want it: on the bucket, not in app jobs.

## Architecture

### Call Start

When the call session starts, the backend starts a LiveKit Room Composite Egress for the room:

- egress type: `RoomComposite`
- mode: `audio_only=true`
- output: direct file output to the configured S3-compatible recordings bucket

The backend persists enough metadata on the call row to find the recording later:

- `recording_url`
- `recording_object_key`
- `recording_egress_id`

`recording_url` remains useful as a stored reference, but user-facing access still comes from fresh signed URLs minted at read time.

### Call End

No recording bytes are sent from the agent to the API for normal live calls.

The existing queue-backed call finalization flow continues to own:

- transcript persistence
- summary generation
- usage deduction
- notification creation

Recording persistence for live calls is handled through egress metadata rather than uploaded call-completion payload bytes.

### Call Detail Read

`GET /api/calls/{call_id}` continues to mint a fresh signed URL from the object key.

If the object is gone because the bucket lifecycle has expired it, or the storage provider cannot mint access for an object-not-found style reason:

- the API returns `recording_url = null`
- the call and transcript still remain available

## Data Model

Extend `calls` with:

- `recording_object_key: nullable string`
- `recording_egress_id: nullable string`

Keep existing:

- `recording_url: nullable string`

No recording status column is needed for MVP.

## Backend Structure

- `LiveKitRecordingService`
  - starts room composite egress
  - returns egress id and object key/url metadata
- `CallLifecycleService`
  - no longer depends on live-call `recording_bytes`
  - remains responsible for non-recording finalization concerns
- `RecordingService`
  - mints fresh signed URLs from `recording_object_key`
  - returns `None` when the object is expired/missing
- call creation / dispatch flow
  - starts recording egress when a call session is created or dispatched

## API Contract

User-facing call history API stays the same:

- `GET /api/calls`
- `GET /api/calls/{call_id}`
- `DELETE /api/calls/{call_id}`

Behavior change:

- `recording_url` is available only while the object still exists in storage
- expired recordings appear as `recording_url = null`

Internal change:

- `POST /api/agent/calls/{call_id}/complete` no longer needs live-call recording bytes for the main recording flow

## Error Handling

- If egress start fails when a call begins, the call should still proceed.
- Recording failure must not block the live conversation.
- Missing or expired recording objects must not break call detail responses.
- Signed URL mint failures caused by missing objects should degrade to `recording_url = null`.

## Testing

Add tests for:

- recording egress metadata is persisted when call/dispatch recording setup succeeds
- call flow remains usable when egress start fails
- call detail mints fresh signed URL from `recording_object_key`
- call detail returns `recording_url = null` when the object has expired or the provider reports it missing
- existing soft-delete and transcript behavior remains unchanged

## Non-Goals

- App-managed 30-day deletion jobs
- deleting call rows when recordings expire
- separate-channel or per-participant recordings
- admin recovery APIs in this slice

## Manual Verification

- place one real call and confirm one mixed audio object lands in the recordings bucket
- verify `calls.recording_object_key` and `calls.recording_egress_id` are populated
- verify `GET /api/calls/{call_id}` returns a fresh signed URL
- verify bucket lifecycle expiry later results in `recording_url = null` without breaking call detail
