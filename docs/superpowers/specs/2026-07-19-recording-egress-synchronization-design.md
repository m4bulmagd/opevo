# Recording Egress Synchronization Design

**Date:** 2026-07-19

**Status:** Implemented and locally verified through `e911143`; not
production-certified.

## Decision summary

Opevo now creates one private, durable recording egress operation per normal
customer call before LiveKit recording-start I/O and keeps it separate from the
customer-facing recording playback projection on `calls`.

When an owner removes a terminal call, Opevo immediately purges customer call
content and hides the call. Provider stop and object deletion continue
asynchronously from the private operation and a reference-only transactional
outbox event. A provider or storage outage does not keep the call visible and
does not require a customer retry.

An ambiguous recording start is reconciled but never started a second time.
This prefers a rare missing recording over duplicate egresses or orphaned audio.

This is a local implementation and verification slice. It does not authorize a
cloud deployment, provider-account mutation, real credential use, or external
publishing.

## Why this design exists

Before this implementation, the runtime committed `pending -> connected` before
provider I/O, then started LiveKit Room Composite Egress and persisted only the
returned egress ID on the call.

That left this race:

1. Agent join commits the connected call.
2. Recording start enters provider I/O before an egress ID is durable.
3. The call completes while start remains in flight; completion sees no egress
   ID and therefore creates no stop intent.
4. The owner removes the terminal call; current deletion sees no recording
   references, purges the call, and commits its tombstone.
5. Recording start returns an egress ID after deletion.
6. Immediate best-effort stop fails or is uncertain.
7. The fallback call lookup excludes tombstones, so Opevo cannot persist the
   provider ID or a pending stop.

The provider recording could then outlive the customer-visible call without a
durable retry identity. The implemented private operation closes that race
without attaching cleanup authority to `calls`, whose recording fields are
intentionally cleared on removal and protected from delayed writers.

LiveKit exposes egress lifecycle events and supports listing egresses by room,
but current API documentation only promises active egress discovery. Those are
reconciliation signals, not a replacement for Opevo's durable intent:

- [LiveKit Egress API](https://docs.livekit.io/reference/other/egress/api/)
- [LiveKit webhook events](https://docs.livekit.io/intro/basics/rooms-participants-tracks/webhooks-events/)

## Goals

- Make recording start, stop, and deletion intent durable across call removal.
- Remove customer call content immediately after an authorized terminal-call
  removal command.
- Preserve original audio for visible calls until owner removal or a separately
  approved future retention policy.
- Recover late provider results, process crashes, webhook reordering, and
  transient provider or storage outages without customer intervention.
- Keep provider calls outside SQL transactions and row locks.
- Avoid duplicate recording starts after an ambiguous result.
- Keep outbox payloads, logs, metrics, and retained cleanup metadata free of
  customer content and raw provider payloads.
- Preserve the current call state machine, usage accounting, finalization, and
  delayed-writer tombstone protections.

## Non-goals

- Account-wide export or deletion orchestration.
- Automatic 30-day recording retention.
- Backup-erasure claims or policies.
- Legal approval of recording disclosure or privacy wording.
- A second recording provider, separate participant tracks, transcoding, or
  audio post-processing.
- Retrying recording start after an ambiguous provider outcome.
- Cloud monitoring, infrastructure provisioning, provider certification, or
  deployment.

## Product contract

### Visible calls

A visible completed call keeps its original-audio behavior. The API uses the
recording playback projection to report availability and mint a short-lived
access URL. The private operation is not returned to the owner.

### Call removal

`DELETE /api/calls/{call_id}` keeps the current authorization and terminal-state
rules:

- unknown, cross-tenant, or otherwise unavailable calls return `404`;
- active calls return `409` with `call_delete_active`;
- the first successful terminal-call removal returns `204`;
- repeated owner removal returns `204` without duplicating cleanup work.

The successful local transaction:

1. locks the owner-scoped call, including an existing tombstone;
2. creates or locks its recording egress operation when cleanup metadata exists;
3. when an operation exists, sets both stop and deletion intent on it;
4. when an operation exists, adds its idempotent `recording.reconcile` event;
5. deletes transcript messages;
6. clears caller number, summary fields, and the recording playback projection;
7. sets `deleted_at` and commits.

No LiveKit or storage call occurs in this request. Provider and storage outages
therefore no longer produce `call_delete_retryable` or a customer-facing `503`.
If the local transaction itself fails, all mutations roll back and the call
remains visible rather than becoming partially removed.

The web UI continues to say **Call removed**. It must not claim that provider,
backup, or historical-copy erasure completed synchronously.

## Domain boundary

The project language for this slice is maintained in [`CONTEXT.md`](../../../CONTEXT.md).
The central distinction is:

- the **recording playback projection** belongs to the visible call and is
  purged on call removal;
- the **recording egress operation** belongs to provider coordination and can
  survive the call tombstone until cleanup is proven.

### `RecordingLifecycleService`

`RecordingLifecycleService` owns durable SQL intent and the visible-call
playback projection. Dispatch, call lifecycle, call history, and webhooks use it
instead of manipulating operation state or reconciliation events independently.
It performs no LiveKit or storage inspection.

Its conceptual commands are:

- `prepare_start(call)`
- `begin_start(operation_id)`
- `record_start_success(operation_id, result)`
- `record_start_error(operation_id, outcome)`
- `request_stop(call_id)`
- `request_deletion(call)`
- `accept_egress_event(event)`

These names describe the interface, not a required one-method-per-command file
layout. Repository mechanics, outbox identity, and projection guards remain
hidden behind the service.

### `RecordingReconciler`

`RecordingReconciler` owns provider and storage inspection for one operation.
It loads a short durable snapshot, closes the database transaction, performs
LiveKit or storage I/O, then opens a new transaction to revalidate and persist
the result. Unknown and conflicting identities remain observable and retryable
without issuing a second recording start.

### Existing consumers

- `LiveKitDispatchService` connects the call, prepares and commits the operation,
  then performs recording-start provider I/O and records its result through the
  lifecycle service.
- `CallLifecycleService` requests stop for every terminal transition, even when
  no provider egress ID is known.
- `CallHistoryService` atomically requests deletion and purges customer content;
  it no longer performs provider I/O.
- The LiveKit webhook boundary passes sanitized egress lifecycle facts to the
  lifecycle service.
- The outbox worker asks `RecordingReconciler` to inspect and reconcile one
  operation.

## Database operation model

Alembic revision `0014` creates `recording_egress_operations`. Here, “model”
means the private database/SQLAlchemy operation record, not an AI model.

| Field | Contract |
| --- | --- |
| `id` | UUID primary key and outbox aggregate identity |
| `call_id` | Unique foreign key to `calls.id`; one operation per normal call |
| `room_name` | Exact Opevo-owned LiveKit room identity; nullable only for incomplete legacy backfill |
| `legacy_incomplete` | Migration-only marker for a backfilled row whose room identity was absent |
| `expected_object_key` | Deterministic private object path committed before provider I/O |
| `provider_egress_id` | Nullable, unique LiveKit egress identity when known |
| `start_state` | `prepared`, `starting`, `started`, `not_started`, or `uncertain` |
| `start_attempted_at` | Time the operation durably moved to `starting` |
| `stop_requested_at` | Durable latest intent to make the provider non-running |
| `delete_requested_at` | Durable owner-removal intent; implies stop intent |
| `provider_terminal_at` | Time Opevo positively confirmed the known egress was non-running |
| `object_deleted_at` | Time object deletion succeeded or confirmed absence |
| `last_reconciled_at` | Last completed reconciliation inspection |
| `last_error_code` | Nullable bounded safe error code; never a raw exception message |
| timestamps | Normal creation and update timestamps |

The table does not store caller numbers, transcripts, summaries, business
details, customer-facing URLs, credentials, raw webhook bodies, or raw SDK
objects.

Required database invariants include:

- one operation per call;
- one operation per known provider egress ID;
- only the five approved start states;
- `started` requires a provider egress ID;
- an egress ID requires `started`;
- room identity is required unless `legacy_incomplete` is true;
- `legacy_incomplete` is allowed only for backfilled `started` or `uncertain`
  operations;
- `prepared` has no `start_attempted_at`;
- deletion intent requires stop intent;
- object deletion cannot be recorded without deletion intent.

The call foreign key intentionally prevents future hard deletion while provider
cleanup is outstanding. Account-wide deletion must orchestrate operation cleanup
before removing call tombstones and remains a separate phase.

### Playback projection compatibility

Keep `calls.recording_object_key`, `calls.recording_egress_id`, and
`calls.recording_url` for this slice. They remain a compatibility projection for
visible call history, not provider-cleanup authority.

When a start result arrives:

- always persist it on the private operation when the operation identity and
  expected object key match;
- project it to the call only when `deleted_at IS NULL`;
- never repopulate any recording field on a tombstoned call;
- never change call status, end facts, accounting, or finalization generation.

A late result may therefore make valid audio available on an already-terminal
but still-visible call. It can never restore content after owner removal.

## Start lifecycle

### States

`prepared` means Opevo durably intends one recording but has not allowed the
provider request to begin. `starting` means the provider request may have been
sent. `started` means the exact egress ID is known. `not_started` means a typed
local or provider result conclusively proves no egress was created. `uncertain`
means the outcome cannot be proven.

### Normal sequence

1. Agent join locks and atomically connects the expected pending call.
2. In the same transaction, Opevo inserts the `prepared` operation and its
   `recording.reconcile:{operation_id}:start` outbox intent.
3. Opevo commits the connected call, operation, and outbox event.
4. A short transaction compare-and-sets `prepared -> starting` and commits
   `start_attempted_at`.
5. Opevo starts LiveKit Room Composite Egress with no SQL transaction open.
6. A short transaction records `started` and the returned egress ID, or records
   the classified start error.
7. The customer playback projection is updated only if the call is not
   tombstoned.

The object key is constructed once before step 3 and passed into the recording
provider. The provider must not independently construct a different path.

### Crash and error classification

The start outbox event is initially delayed by a bounded start-result lease so
the synchronous path can finish. A recommended initial lease is two minutes and
must be represented as one tested code constant with an injected clock.

A direct start result makes the start event immediately due so it can be marked
delivered. A new stop or deletion intent also makes the earliest unfinished
event for that operation immediately due. It must not wait for the original
start lease, because that would extend the recording window for a short call.
This acceleration respects an event already holding a processing lease; the
late result or new intent still wakes the outbox worker best effort.

After the lease:

- stale `prepared` is changed to `not_started`; the compare-and-set prevents the
  original request path from starting afterward;
- stale `starting` is changed to `uncertain` and reconciled;
- explicit local/preflight or provider rejection known to create no egress is
  `not_started`;
- timeout, connection loss, process interruption, or ambiguous provider failure
  is `uncertain`.

The provider adapter must expose the difference between **known not started**
and **unknown outcome**. Existing retryable/terminal error categories are not a
sufficient start-outcome contract by themselves.

### No second start

Neither the API nor a worker may invoke recording start again after the operation
has entered `starting`, including from `uncertain`. This invariant prevents a
recovery attempt from creating two provider recordings for one call.

If recovery cannot identify the original result, the call continues without a
recording projection and the unresolved private operation remains observable.

## Stop and deletion lifecycle

Stop and deletion are durable timestamps rather than extra combined start
states. This avoids a state explosion when deletion races `prepared`,
`starting`, `started`, or `uncertain`.

- Call end sets `stop_requested_at` and creates the stop reconciliation intent.
- Owner removal sets `delete_requested_at` and `stop_requested_at` in the purge
  transaction and creates the deletion reconciliation intent.
- Repeated commands preserve the first intent timestamps and reuse idempotent
  events.

For a known egress ID, reconciliation calls `ensure_not_running` outside the
database transaction. Successful, failed, aborted, and limit-reached provider
terminal states all satisfy the privacy requirement that the egress is no longer
running. The operation records `provider_terminal_at` in a new transaction.

For a visible call with stop intent only, the object remains private and the
operation remains available for a later owner-removal command.

For deletion intent, storage deletion happens only after either:

- the known egress is positively terminal; or
- `start_state = not_started` proves no egress exists.

Storage deletion by the exact expected object key is idempotent; an already
missing object is success. After provider terminality or definite non-start and
object absence are both proven, the worker removes the private operation.

If the worker crashes after removing the operation but before marking its outbox
event delivered, the retry treats a missing operation as already complete. The
call tombstone continues to block delayed customer-content writers.

## Reconciliation

### Known provider identity

When `provider_egress_id` exists, the provider adapter lists and, when necessary,
stops that exact egress. Provider I/O occurs after a short snapshot transaction
has closed. The result is persisted under a fresh operation lock.

### Unknown provider identity

For `starting` or `uncertain`, reconciliation combines:

- signed `egress_started`, `egress_updated`, and `egress_ended` webhooks;
- egress listing by exact room name;
- matching the expected file output path when the provider supplies it;
- the deterministic storage object key;
- durable call stop/deletion intent.

Provider events are accepted only when their room, egress identity, and expected
output are consistent with the operation. Missing required identifiers, a
mismatched object path, or a conflicting egress ID must not mutate the operation.

An empty active-egress list is not proof of `not_started` and does not complete
cleanup. Unknown operations retry without exhaustion. When deletion is pending,
reconciliation may idempotently remove an already-visible expected object to
reduce exposure, but it must continue until the start outcome is resolved and no
known egress can still write that object.

Multiple exact matches violate the one-start invariant. Opevo fails closed,
records a safe conflict, emits an internal signal, and does not expose any of the
matches as customer playback. Reconciliation attempts to make every exact match
returned by LiveKit non-running, but does not touch room egresses whose expected
object path does not match. An unknown operation remains `uncertain`; an
operation with a known provider ID retains that identity. Both remain retryable
with the conflict recorded, and an empty later active listing alone does not
erase the conflict or prove cleanup.

### Signed webhook boundary

Extend the existing verified LiveKit webhook converter with a sanitized egress
projection containing only:

- external event ID and event type;
- egress ID;
- room name;
- provider status;
- expected file output path when present.

Continue storing `{}` in generic webhook-event payloads. Duplicate provider
event IDs remain idempotent. Webhook handling performs only database mutation and
outbox wakeup; it performs no provider or storage I/O.

## Transactional outbox

Use one topic, `recording.reconcile`, with aggregate type
`recording-egress-operation` and aggregate ID equal to the operation UUID. The
payload is only:

```json
{"operation_id":"<internal-operation-uuid>"}
```

Use separate idempotency keys for the three durable intent phases:

- `recording.reconcile:{operation_id}:start`
- `recording.reconcile:{operation_id}:stop`
- `recording.reconcile:{operation_id}:delete`

Existing aggregate ordering makes an unfinished earlier event block later events
for the same operation. Every handler reads the latest operation state, so an
earlier start event can satisfy a stop or deletion intent that arrived while it
was retrying. Later events then become safe no-ops.

Whenever a direct start result, stop command, or deletion command changes the
latest intent, the lifecycle service advances the earliest pending event's due
time to the current time. This preserves aggregate ordering without allowing the
two-minute start-result lease to delay stop or deletion.

Unknown start, stop, and deletion cleanup errors are retryable and
non-exhaustible. Definitive `not_started` and completed normal-stop states deliver
their applicable event. A missing operation is delivered as an idempotent
success.

Wakeup enqueue remains best effort because the SQL outbox row is authoritative.
Backoff remains bounded through the existing outbox retry schedule; after the
last listed delay, a non-exhaustible event continues using that maximum delay.

## Provider interfaces

Deepen the recording provider boundary rather than exposing LiveKit SDK objects
to orchestration code.

Required capabilities are:

- start one room recording using a caller-supplied expected object key;
- classify start errors as known-not-started or unknown-outcome;
- list sanitized egress snapshots by room;
- ensure a known egress is not running;
- expose only safe status and identity fields needed for reconciliation.

The storage boundary keeps its current idempotent delete-by-object-key behavior.
No provider method receives caller content, transcript text, summary data, or a
customer-facing URL.

## Migration and compatibility

Revision `0014` creates the table and backfills calls that have any recording
metadata.

- A known `recording_egress_id` becomes `started`.
- Recording object metadata without an egress ID becomes `uncertain`.
- The expected object key is copied when present and otherwise derived from the
  existing user and call UUIDs.
- A missing legacy room identity is retained as an explicitly incomplete
  operation with `legacy_incomplete = true`; new runtime operations may not set
  that marker or omit the room identity.
- A terminal call receives stop intent.
- A deleted legacy call receives both stop and deletion intent.
- The migration inserts reference-only reconciliation outbox rows for backfilled
  operations that require work.

Backfill and event creation occur in the migration transaction and perform no
provider I/O. Existing call recording fields are not dropped, so downgrade can
remove the new table and its new pending events without destroying the prior
playback projection.

The implementation must preserve SQLite development behavior and PostgreSQL
constraints. PostgreSQL remains authoritative for concurrency acceptance.

## Failure behavior

| Failure | Required behavior |
| --- | --- |
| Database failure during prepare | Roll back call connection, operation, and outbox together; no provider start |
| Failure after `prepared` commit but before start claim | Lease recovery marks `not_started`; no provider start retry |
| Start timeout or connection loss | Record `uncertain`; call continues; reconcile without a second start |
| Late success after call completion | Persist operation, project only if visible, honor stop intent |
| Late success after removal | Persist operation only; never restore call content; honor deletion intent |
| Provider stop/list outage | Keep non-exhaustible event and safe error code; customer call stays removed |
| Storage deletion outage | Keep non-exhaustible event; retry exact object key; customer call stays removed |
| Missing storage object | Treat deletion as successful after provider safety precondition |
| Duplicate webhook | Commit duplicate acknowledgement without repeating business mutation |
| Mismatched provider event | Ignore business mutation, record safe signal, never expose provider-controlled values |
| Local removal transaction failure | Roll back purge and cleanup intent; return an ordinary server error |

## Observability and security

The implementation exposes low-cardinality measurements for:

- operation counts by start state;
- oldest unresolved operation age;
- pending stop and pending deletion counts and age;
- reconciliation results by safe category;
- provider-event mismatch and conflict counts.

Do not use room names, object keys, egress IDs, user IDs, or call IDs as metric
labels. Structured logs may include internal operation and call UUIDs plus a safe
error class, but must not include room names, object keys, URLs, credentials, raw
provider responses, or customer call content.

Deployment readiness must continue to fail closed when LiveKit credentials or
private storage configuration are invalid. This slice exposes signals but does
not configure a cloud alerting destination.

## Verification strategy

### Model and migration

- Model and database checks enforce every state and timestamp invariant.
- One call cannot have two operations.
- One known egress cannot belong to two operations.
- Migration upgrade and downgrade tests cover SQLite-compatible structure.
- PostgreSQL migration and concurrency tests prove the authoritative constraints.
- Backfill preserves visible recording projection and creates only reference-only
  operation/outbox data.
- Incomplete legacy room identity remains safely unresolved and observable
  instead of being guessed or silently declared clean.

### Start and race behavior

- Connected call, prepared operation, and start outbox commit before provider I/O.
- The provider receives the already-committed expected object key.
- Start success persists operation and visible-call projection atomically.
- Completion racing success persists the operation and honors stop intent without
  changing terminal call facts.
- Deletion racing in-flight start returns `204`, purges customer content, and
  leaves durable cleanup intent.
- Late success after deletion updates only the operation.
- Failed immediate reconciliation remains durable and retryable.
- Stale `prepared` and `starting` recover correctly after a simulated restart.
- No recovery path invokes recording start a second time.

### Stop, delete, and outbox

- Call completion creates stop intent even without an egress ID.
- Provider calls occur with no ORM transaction or call/operation row lock open.
- Known active, starting, ending, complete, failed, aborted, missing, and
  uncertain provider states follow the specified result classes.
- Deleted-call cleanup stops the provider before final object deletion.
- Storage absence is idempotent success; transient storage failure is
  non-exhaustible.
- Worker crash after operation removal is idempotent on retry.
- Repeated owner removal does not duplicate provider work.
- Aggregate ordering covers start, stop, and delete events that overlap.
- A call ending before the start-result lease expires makes reconciliation due
  immediately and does not extend the recording window.

### API and customer projection

- Terminal removal returns `204` without synchronous provider or storage I/O.
- Provider/storage outages do not return `call_delete_retryable`.
- Active removal remains `409`; cross-tenant and unknown calls remain `404`.
- List, detail, transcript, and signed playback become inaccessible immediately
  after removal.
- Delayed transcript, summary, lifecycle, and recording-projection writers cannot
  restore tombstoned content.
- A visible terminal call can receive a valid late recording projection.

### Webhooks, privacy, and observability

- Signed egress events attach only exact matching identities.
- Duplicate, missing-identity, mismatched-path, and conflicting-ID events are
  safe and idempotent.
- Generic webhook persistence still stores no raw payload.
- Outbox payload scans prove operation ID is the only value.
- Log and metric tests prove forbidden provider/customer values are absent.
- Deployment-readiness tests include the new topic, database operation model,
  provider capability, and fail-closed configuration.

### Regression gates

The recording implementation is checked with:

- API Ruff and mypy;
- complete SQLite API tests;
- complete isolated PostgreSQL/Redis API tests with zero skips;
- agent Ruff, mypy, and deterministic tests;
- web formatting, TypeScript, Vitest, and production build;
- provider-free browser activation and full-service restart/resume acceptance;
- migration, shell-syntax, safety-scan, and `git diff --check` gates.

Credentialed LiveKit behavior evaluations and real-provider certification
remain separate explicit evidence and are not silently substituted by local
tests.

## Acceptance criteria

This Phase 0 unit was accepted against these criteria:

1. every attempted recording has durable identity before provider start I/O;
2. terminal-call removal never waits on LiveKit or storage;
3. customer call content is inaccessible immediately after successful removal;
4. a late or uncertain start cannot lose the stop/deletion intent;
5. no ambiguous start path can issue a second start request;
6. provider cleanup is non-exhaustible and observable without customer data;
7. deleted-call cleanup removes its private operation only after safe terminality
   or definite non-start plus object absence;
8. normal visible-call playback and call finalization remain correct;
9. PostgreSQL race tests and all regression gates pass;
10. documentation continues to state that Opevo is production-oriented but not
    production-certified.

## Completion evidence

The inclusive implementation range is `c6bf2bb^..e911143`; its final race,
privacy, migration, and observability hardening spans `a774dad..e911143`.
Fresh local evidence is:

- API Ruff and mypy clean;
- focused recording/readiness gate: 475 passed, 33 skipped, 1 known upstream
  warning;
- authoritative Task 7 PostgreSQL/Redis infrastructure gate: 30 passed, 0
  skipped;
- provider-free full API suite: 1,718 passed, 87 skipped, 1 known upstream
  warning;
- complete isolated PostgreSQL/Redis API suite: 1,805 passed, 0 skipped, 1
  known upstream warning;
- agent Ruff and mypy clean; 250 deterministic tests passed with 4 credentialed
  evaluations deselected;
- web Biome (145 files), TypeScript, and 228 Vitest tests passed; the exact
  default Turbopack production build generated 9/9 static pages from an
  identical tracked web tree in the clean normal checkout;
- provider-free browser activation and full-service restart/resume each passed;
- shell syntax, Playwright discovery, stale-contract scans, cleanup checks, and
  `git diff --check` passed.

This implementation evidence does not certify cloud deployment, provider
behavior, legal readiness, retention policy, monitoring, or production
operation.

## Relationship to earlier designs

This document supersedes only the conflicting recording synchronization and
call-removal portions of:

- `2026-03-28-recording-lifecycle-design.md`, which attaches late cleanup
  references back to the call;
- `2026-03-28-recording-window-design.md`, where start/stop intent depends only
  on current call recording fields;
- `2026-03-28-call-history-api-design.md`, whose original soft-delete contract
  retained transcript and recording content.

Those documents remain useful history. The one mixed Room Composite Egress path,
agent-join start timing, SIP/call-end stop timing, private signed playback, and
provider-free call-finalization boundary remain authoritative where they do not
conflict with this design.

The later local-first activation design remains authoritative that automatic
30-day retention is planned, disabled, and outside this slice.
