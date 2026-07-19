# Missing recording operation recovery design

## Context

Task 4 reconciles recording egress without holding SQL transactions across
provider or storage calls. An outbox claim can expire while one worker is
listing an uncertain recording. A reclaimed worker can then accept a direct
`not_started` result, delete the object, remove the private operation, and
finish the outbox work. If the first worker subsequently observes an exact live
egress, its current revalidation sees the missing operation and treats that as
success. The live writer can recreate the deleted object after all durable
cleanup authority has disappeared.

## Decision

Exact room-and-object-key evidence observed after operation removal restores
durable cleanup authority before any provider stop attempt.

The stale worker opens a new short transaction and locks the customer call by
the snapshotted call ID. It then rechecks the operation by its original ID:

- If the operation is still absent, recreate that same operation ID for the
  same call, room, and expected object key. Record the exact provider ID as a
  trusted `started` identity, copy the original stop and deletion intents, set
  `recording_identity_conflict`, leave object and terminal proof unset, and
  hide every customer-visible recording projection.
- If another worker already restored the operation, revalidate its immutable
  call, room, legacy, and object-key identity. Preserve a compatible known
  identity; otherwise retain the operation as an unknown sticky conflict. Hide
  the projection in either case.
- If the call is missing or its immutable identity cannot be recovered safely,
  do not perform provider or storage I/O. Return a bounded non-exhausting retry.

In the same transaction, create an idempotent `recording.reconcile` outbox event
dedicated to missing-operation conflict recovery. Commit the operation,
projection purge, and fresh retry authority together. Only after that commit
may the worker call `ensure_not_running` for the distinct stored and observed
provider IDs. The reconciliation result remains the existing non-exhausting
`recording_identity_conflict` retry whether the immediate stop succeeds or
fails; ordinary conflict reconciliation handles every later attempt.

The restored operation is intentionally sticky. Automatic deletion remains
blocked because missing state and exact live-provider evidence contradict each
other. This matches Task 4's existing fail-closed conflict semantics and avoids
inventing a new error code or auto-resolution rule.

## Transaction and I/O sequence

1. Worker W1 snapshots an unknown operation and closes its SQL transaction.
2. W1 lists provider egresses.
3. A reclaimed W2 may commit `not_started`, delete storage, remove the
   operation, and deliver its stale work.
4. W1 receives one exact live egress and opens a fresh SQL transaction.
5. W1 locks the call, rechecks the missing operation, restores sticky conflict
   state plus a fresh reconciliation event, hides projection, and commits.
6. With no SQL session open, W1 attempts to stop every distinct safe identity.
7. Any subsequent delivery finds durable conflict authority and can retry the
   provider stop. Storage deletion and operation removal stay blocked.

No provider or storage call occurs inside a SQL session. Every mutation keeps
the established call-before-operation lock order.

## Alternatives rejected

### Stop without restoring state

This is smaller, but a stop failure would be attached only to W1's expired
outbox claim. W2 may already have delivered the final event, leaving no durable
retry. It does not close the production-safety gap.

### Add reconciliation lease or fencing columns

Leases reduce overlap but cannot prevent a paused worker from resuming after a
newer worker has cleaned up. Fencing still needs a durable recovery path for
provider evidence returned by the stale worker, while adding a schema migration
and more states. That is unnecessary for this bounded repair.

## Verification

Test-first coverage must reproduce the full interleaving, not only a direct
error during one worker's provider listing:

- W1 blocks in `list_room_egresses`.
- A late direct result commits `not_started`.
- W2 completes storage deletion, object proof, operation removal, and outbox
  delivery.
- W1 returns a singleton exact active egress.
- RED proves the current code returns complete, performs no stop, and leaves no
  operation.
- GREEN proves W1 commits a restored sticky conflict and fresh reconcile event
  before the first stop call, hides all projection, performs no additional
  storage deletion, and attempts the exact stop outside SQL.
- A stop failure followed by a new reconciliation delivery proves durable retry
  authority remains and the operation cannot be removed.
- Two stale workers racing to restore authority remain idempotent: one operation
  and one recovery event exist, all exact identities remain covered, and no
  uniqueness failure escapes.

Run the complete Task 4 focused suites, prescribed worker regressions, Ruff,
mypy, and the provider-free API suite before rereview.

## Scope

This repair does not change the six bounded reconciliation codes, add provider
webhooks, cut over Task 5 runtime producers, deploy infrastructure, or contact
LiveKit, storage, PostgreSQL, Redis, or credentialed services.
