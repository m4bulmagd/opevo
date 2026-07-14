# Recording Lifecycle Design

**Date:** 2026-03-28
**Updated:** 2026-07-13

**Goal:** Use LiveKit-managed mixed-call recording as the only launch recording path, keep raw media out of the agent/API/Redis boundary, and make stop delivery recoverable through durable reference-only intent.

## Decision

Use LiveKit Room Composite Egress with `audio_only=true` and direct S3-compatible file output.

The application does not upload audio from the agent. There is no recording blob in the completion request, no raw recording payload in Redis or the outbox, and no direct recording-upload worker. The API stores only provider references and mints short-lived customer access URLs at read time.

This produces one mixed recording per call, avoids large application payloads, and leaves object retention with the bucket lifecycle policy.

## Lifecycle

### Start

After the expected dispatched agent participant connects, the API first commits `pending -> connected`. With no business transaction open, it starts Room Composite Egress and requests direct output to the recordings bucket.

On provider success, a fresh locked lookup persists metadata only if the call is still `connected` and has no recording attached:

- `recording_object_key`
- `recording_egress_id`
- `recording_url` when supplied by the provider

If the call became non-connected while start was in flight, the returned egress is reconciled immediately through `ensure_stopped(egress_id)`. When that succeeds, recording metadata may remain absent. When immediate cleanup is uncertain, the backend attaches the late egress/object references only for cleanup and commits a versioned `recording.stop` intent. This never regresses or rewrites terminal state, end facts, usage accounting, or finalization generation. Start and cleanup failures are reported with safe metadata and do not block the conversation.

### End facts and stop intent

Agent completion and SIP leave share the durable end invariant:

- the first accepted `ended_at` and `duration_seconds` win
- the call commits `ending` before any Redis wakeup
- if an egress ID is known, the same transaction creates a reference-only `recording.stop` event on aggregate `call-recording`

Finalization phase B creates the same idempotent stop intent as a backstop. The event payload is only:

```json
{"call_id":"<internal-call-uuid>"}
```

It never contains an egress credential, object URL, transcript, customer data, or audio bytes.

### Stop delivery

The `recording.stop` handler:

1. validates that the event topic, aggregate type, aggregate ID, and payload call ID are identical;
2. loads the current egress ID in a short PostgreSQL snapshot and closes the transaction;
3. calls `ensure_stopped(egress_id)` with no ORM transaction open.

`ensure_stopped` lists the egress and treats missing or terminal state as success. For starting, active, or ending state it requests stop and rechecks. Uncertain provider outcomes remain retryable through the transactional outbox.

Summary and recording stop use separate aggregate namespaces (`call-summary` and `call-recording`), so a retrying summary cannot delay recording cleanup.

## Call finalization boundary

`CallLifecycleService` is provider-free. Its two phases are:

1. commit `ending -> finalizing` with a new attempt generation;
2. for that generation, atomically commit debit, normalized end facts, one opaque dashboard notification, required outbox intents, and `completed`.

It does not call LiveKit, Gemini, storage, Firebase, Telnyx, or Redis inside the transaction. `completed` therefore means the database facts and provider intents are durable, not that provider delivery has already finished.

The one-minute call reconciler recovers stale ending/finalizing rows using committed attempt leases. A charged row is repaired to `completed` rather than failed because its post-call attempt budget was exhausted.

## Customer read path and retention

`GET /api/calls/{call_id}` mints a fresh signed URL from `recording_object_key`. Stored `recording_url` is provider metadata, not the customer authorization mechanism.

If the object has expired or is missing, the API returns `recording_url = null`. The call row, transcript, usage facts, and summary remain available according to their own retention rules.

The recordings bucket owns the approved 30-day object lifecycle. The rule expires
only objects under the `calls/` prefix, and the bucket remains private so customer
playback requires a short-lived signed URL. Local Compose provisions the reference
deployment from `infra/minio/recording-lifecycle.json` in the `minio-init` boundary,
then explicitly applies private anonymous access. Production storage remains generic
S3-compatible infrastructure: operators must provision the configured bucket, the
same `calls/`/30-day lifecycle rule, and private access before starting the API. The
application verifies the bucket and object but never creates buckets or runs a
duplicate recording-expiry deletion job.

## Failure behavior

- Egress start failure does not block the live call.
- A start/completion race first invokes `ensure_stopped` for the newly returned egress. If cleanup is uncertain, late provider references and a retry intent may attach to the terminal row, but state, accounting, end facts, and generation remain unchanged.
- SIP leave and completion commit stop intent before queue wakeup.
- Missing/terminal egress is an idempotent stop success.
- Uncertain stop and list failures are safe retryable outbox failures.
- Missing recording objects degrade call detail to `recording_url = null`.
- Raw recording input is schema-invalid and cannot enter Redis or the worker system.

## Verification

Automated coverage proves:

- connected state commits before recording provider I/O
- only the expected agent identity can start recording
- successful metadata persistence is revalidated under a fresh lock
- a terminal start race calls `ensure_stopped`; failed immediate cleanup durably retains only cleanup references/intent without regressing call state or facts
- recording stop performs no provider I/O with an ORM transaction open
- active/missing/terminal egress states are handled idempotently
- uncertain provider results are retried
- summary retries do not block the recording aggregate
- completion and worker payloads contain internal references only
- the legacy blob worker is not registered and no raw media contract remains

Manual staging verification should place one real call, confirm one mixed object and persisted egress metadata, confirm a durable `recording.stop` event is delivered, verify signed playback, and verify lifecycle expiry later returns `recording_url = null` without breaking call detail.

## Non-goals

- separate-channel or per-participant recordings
- raw agent-side audio upload compatibility
- app-managed 30-day recording expiry jobs
- claiming recording delivery is complete merely because call state is `completed`
