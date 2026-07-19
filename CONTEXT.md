# Presvo Domain Language

Presvo answers calls forwarded from a customer's existing business number. This
glossary fixes the product-specific language used across the API, agent, web app,
tests, and design documents.

## Calls and recordings

**Call removal**:
The owner's command to hide a terminal call and purge its customer call content
immediately while any required provider cleanup continues internally.
_Avoid_: Soft delete, synchronous recording deletion

**Customer call content**:
Caller-derived or owner-visible content for a normal call, including the caller
number, transcript, summary, outcome, follow-up details, and recording playback
projection. Operational and accounting facts on the tombstoned call are not
customer call content.
_Avoid_: All call data, call row

**Call tombstone**:
The minimal call row retained after removal to preserve identity, authorization,
idempotency, accounting, and delayed-writer rejection without retaining customer
call content.
_Avoid_: Deleted call, archived call

**Recording playback projection**:
The recording references on a visible call that allow Presvo to report
availability and mint short-lived customer playback access.
_Avoid_: Recording operation, stored audio

**Recording egress operation**:
The private durable record of Presvo's attempt to start, stop, reconcile, and,
when requested, delete one LiveKit recording and its expected storage object.
_Avoid_: Recording playback projection, call recording row

**Uncertain recording start**:
A recording start whose provider outcome is not known because the request timed
out, its connection failed, or the process ended before persisting the result.
_Avoid_: Failed start, retryable start

**Recording reconciliation**:
The idempotent internal process that combines durable operation state, signed
LiveKit events, provider queries, and storage checks to move a recording toward
the latest stop or deletion intent.
_Avoid_: Start retry, customer retry
