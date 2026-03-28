# Recording Window Timing Design

**Date:** 2026-03-28

**Goal:** Reduce leading and trailing silence in mixed call recordings by moving recording start later and stopping it earlier, without adding post-processing or breaking the call flow.

## Problem

The current recording flow starts LiveKit room composite egress as soon as the SIP caller joins the room. That starts recording before the agent has actually picked up the call. The egress is then left to follow the room lifecycle, which means recording can continue after the human caller disconnects.

Observed effects:

- long silence at the beginning of recordings
- long silence at the end of recordings

## Requirements

- Keep one mixed room recording per call.
- Do not add audio post-processing or trimming jobs.
- Keep recording best-effort and non-blocking.
- Make the start/stop timing tighter around the actual conversational window.
- Preserve concurrency safety for multiple calls and multiple workers.

## Recommended Approach

Use LiveKit webhook timing to control the recording window:

- create and dispatch on SIP caller join
- start egress on agent participant join
- stop egress on SIP caller leave

Why this approach:

- It removes most of the silence without media re-encoding.
- It uses events the system already receives.
- It keeps the implementation inside the existing backend architecture.
- It is safe for concurrent calls because the lookup key is `livekit_room_id`.

## Event Flow

### SIP `participant_joined`

- Normalize and match the called number.
- Create the pending call row.
- Dispatch the agent.
- Do **not** start recording yet.

### Agent `participant_joined`

When a participant join event indicates the agent has entered the room:

- identify the unique pending call for the room where:
  - `livekit_room_id == room_name`
  - `status == "pending"`
  - `recording_egress_id IS NULL`
- start room composite audio-only egress
- persist:
  - `recording_object_key`
  - `recording_egress_id`
  - `recording_url`

### SIP `participant_left`

When the SIP participant leaves:

- look up the room’s active call that has a `recording_egress_id`
- stop the egress immediately

Room-finished and egress-ended events remain informational and should not control the intended user-facing recording window.

## Concurrency Model

This design is safe for concurrent calls because call lookup is by `livekit_room_id`, not by timestamp or global “latest call”.

Rules:

- only one pending call per room should exist in the intended flow
- recording start only happens when `recording_egress_id IS NULL`
- if multiple rows somehow match, log and skip rather than guessing

## Backend Structure

- `LiveKitDispatchService`
  - handle SIP join: create/disaptch only
  - handle agent join: start recording
  - handle SIP leave: stop recording
- `LiveKitRecordingService`
  - `start_room_recording(...)`
  - `stop_room_recording(egress_id)`
- recording provider
  - wrap LiveKit egress start/stop
- `CallRepository`
  - add room-scoped lookup helpers for pending/no-egress and active/with-egress
- `/webhooks/livekit`
  - route both `participant_joined` and `participant_left` events into the service

## Error Handling

- If recording start fails on agent join, the call still proceeds.
- If recording stop fails on SIP leave, the call still completes.
- Repeated agent joins must not start duplicate egress for the same call.
- Repeated SIP leave events must not fail if the egress is already stopped or absent.

## Testing

Add tests for:

- SIP join creates/disaptches without starting recording
- agent join starts recording for the room’s pending call
- agent join does nothing if recording already started
- SIP leave stops recording for the active room call
- start/stop failures are logged and non-blocking

## Non-Goals

- audio trimming/transcoding after the fact
- waveform or VAD-based silence detection
- changing the one-mixed-recording output model
- changing retention behavior

## Manual Verification

- place one real call
- confirm recording starts after the agent joins rather than at initial ring
- confirm recording ends close to caller disconnect rather than room teardown
- compare the new file against the previous behavior to confirm reduced start/end silence
