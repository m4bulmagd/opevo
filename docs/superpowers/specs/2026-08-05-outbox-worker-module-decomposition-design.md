# Outbox Worker Module Decomposition Design

**Date:** 2026-08-05

**Status:** Owner-approved written contract

## Context

The API background worker has a durable PostgreSQL outbox, bounded delivery
failures, idempotent handlers, reconciliation, and extensive concurrency and
failure-path tests. Its worker-side module boundary has not kept pace with that
behavior.

`apps/api/app/workers/jobs/outbox_topics.py` is currently 1,625 lines and owns
phone provisioning and routing, customer-call LiveKit dispatch, forwarding-
verification LiveKit dispatch, summary generation, recording reconciliation
delivery, and the default topic registry. It imports repositories, providers,
settings, contracts, and services from several domains. Unrelated workflows
therefore collide in one file, module-global monkeypatches span domains, and
genuine repeated policy is difficult to separate from coincidentally similar
code.

The delivery engine and several outbox-only implementations are also spread
through `app/workers/jobs`:

- `outbox_delivery.py` owns claim, retry, completion, and failure behavior;
- `account_deactivation.py` and `provider_cleanup.py` are topic handlers;
- `phone_provisioning.py` is an internal operation invoked only by the phone
  provisioning topic, despite being named like an independently scheduled
  ARQ job; and
- `recording_reconciliation.py` is the deep operation behind the recording
  topic.

The 11-line legacy `summary_job` is not registered or called by production
code. Only three legacy tests exercise it. Durable summary generation now runs
through the `summary.generate` outbox topic.

The owner selected review Issue **5A** and approved a clean atomic migration:
the old god module will be deleted, not retained as a compatibility facade.
There is no deployed environment or important persisted database state that
requires an incremental compatibility period. The design still preserves
runtime behavior and database contracts; that freedom is used for code cleanup,
not for unrelated feature changes.

## Goals

1. Give worker-side outbox delivery one obvious package and dependency
   direction.
2. Split topic handling by cohesive business family without creating a generic
   workflow framework or a large collection of shallow layers.
3. Preserve transaction boundaries, provider behavior, idempotency, retry
   policy, crash recovery, and bounded error reporting.
4. Remove genuine duplication in the two LiveKit delivery workflows after a
   behavior-preserving relocation has independently passed all tests.
5. Keep one explicit registry and eliminate duplicate definitions of the
   supported topic set where the concepts are identical.
6. Remove proved-dead or misleading worker entry points instead of carrying
   aliases or deprecation scaffolding.
7. Increase architectural and edge-case test protection without using flaky
   live-provider tests.

## Non-goals

- No new outbox topic or change to an existing topic's payload or aggregate
  contract.
- No database schema migration, database reset, or stored-data migration.
- No queue topology, worker capacity, timeout, retry schedule, or healthcheck
  change.
- No provider configuration, environment, credential, or deployment change.
- No realtime work.
- No recording feature or recording-retention change.
- No internal decomposition of the cohesive account-deactivation state
  machine, even though that module is large.
- No broad constructor-injection or process composition-root migration. That
  remains review Issue 6A.
- No compatibility facade, deprecated import alias, or package-level handler
  re-export.

## Decisions

### 1. Establish one worker-side outbox package

The worker delivery subsystem moves to:

```text
apps/api/app/workers/outbox/
├── __init__.py
├── delivery.py
├── failures.py
├── registry.py
├── _account_lifecycle.py
├── phone.py
├── phone_provisioning.py
├── customer_dispatch.py
├── verification_dispatch.py
├── _livekit_delivery.py
├── post_call.py
├── recording_reconciliation.py
├── account_deactivation.py
└── provider_cleanup.py
```

`__init__.py` is intentionally empty. Callers import the concrete module that
owns an interface. The package does not recreate `outbox_topics.py` through
re-exports.

The modules own the following responsibilities:

- `delivery.py`: claim batches, validate payloads, invoke handlers, classify
  outcomes, durably complete or fail events, reconcile expired processing
  leases, and emit delivery-level observability.
- `failures.py`: the safe error-code allowlist, `OutboxDeliveryError`, provider
  failure mapping, retryability/exhaustibility policy, and bounded error-class
  mapping.
- `registry.py`: exactly one explicit `topic: handler` mapping.
- `_account_lifecycle.py`: lifecycle-generation validation and current-account
  enforcement genuinely shared by phone and dispatch workflows.
- `phone.py`: the `phone.provision`, `phone.enable`, and `phone.disable` topic
  adapters, admission, routing reconciliation, compensation, and phone
  projection behavior.
- `phone_provisioning.py`: the deep provider provisioning operation currently
  named `phone_provisioning_job`.
- `customer_dispatch.py`: customer-call payload validation, readiness snapshot,
  contract metadata, dispatch reconciliation, and call identity persistence.
- `verification_dispatch.py`: verification session/window validation, contract
  metadata, dispatch reconciliation, and activation identity persistence.
- `_livekit_delivery.py`: only the repeated provider list/create/ambiguous-
  recovery algorithm, introduced in the second implementation phase.
- `post_call.py`: the small summary and recording topic adapters.
- `recording_reconciliation.py`: the existing deep recording reconciliation
  operation.
- `account_deactivation.py` and `provider_cleanup.py`: the existing cohesive
  topic workflows, moved without internal redesign.

`OutboxService` remains in `app/services/outbox_service.py`. It participates in
producer-side application transactions and creates durable events. The new
package owns worker-side delivery, not all application code that mentions an
outbox.

The remaining `app/workers/jobs` namespace contains actual independently
scheduled worker jobs rather than the outbox subsystem.

### 2. Enforce one dependency direction

The intended dependency graph is:

```text
ARQ worker
    |
    v
delivery ----------> failures
    |
    | local default lookup
    v
registry ----------> topic handlers
                         |
                         v
              domain services, repositories,
                  contracts, and providers
```

Topic handlers do not import `delivery.py` or `registry.py`. They import bounded
delivery failures from `failures.py`. The registry is the only module importing
all handlers.

`delivery.py` retains a local default-registry lookup. This preserves the
current runtime and test contract in which a handler mapping may be injected
through the worker context, while callers without an injected mapping receive
the production registry. Keeping the import local prevents a module-import
cycle and avoids initializing provider SDK modules merely by importing the
delivery engine.

This design does not introduce reflection, dynamic handler discovery, a DI
container, a service locator, or registration decorators. Review Issue 6A may
later make handler construction explicit at the worker composition root without
changing the topic-family boundary established here.

### 3. Use one validation source of truth and one explicit registry

`REFERENCE_PAYLOAD_FIELDS` remains the source of truth for the accepted topic
names and their reference-only payload shapes. The supported set is derived:

```python
SUPPORTED_OUTBOX_TOPICS = frozenset(REFERENCE_PAYLOAD_FIELDS)
```

The registry remains a separately explicit mapping because payload acceptance
and executable routing are different responsibilities. A test requires exact
key equality between the registry and the supported set and requires every
registry value to be callable.

An enum or topic-definition framework is not introduced for nine static topic
strings. Deriving identical validation data removes genuine duplication;
keeping routing explicit favors inspectability over cleverness.

### 4. Preserve delivery and transaction semantics

The refactor preserves the current high-level delivery sequence:

1. The delivery engine claims an event in a short database transaction.
2. It validates the reference-only payload and resolves the explicit handler.
3. The claim transaction closes before the handler performs external work.
4. The handler snapshots durable state in short transactions.
5. Provider calls occur without an open database transaction.
6. Mutable eligibility or lifecycle state is revalidated at existing race
   boundaries.
7. Provider results are reconciled and persisted in a fresh locking
   transaction.
8. Delivery marks the event complete, retryable, or terminal in its own
   transaction.

Moving code must not merge transactions, carry ORM objects across provider
calls in new ways, weaken row locking, or remove a commit/rollback boundary.

Phone delivery retains:

- lifecycle-generation admission;
- recovery of an already-running provider operation;
- non-exhaustible pending-provider retries;
- routing target persistence;
- compensation after a stale or failed enable; and
- post-provider projection and current-account checks.

Summary delivery retains:

- transcript snapshotting outside the provider call;
- no provider call for an empty transcript;
- a fresh locked call lookup before persistence;
- durable maximum-sequence comparison; and
- retryable `summary_stale` behavior when messages changed concurrently.

Recording delivery retains its reconciler result validation, bounded error
codes, non-exhaustible unresolved retries, conflict telemetry, and provider
failure mapping. Account deactivation and provider cleanup are relocation-only.

### 5. Refactor LiveKit delivery only after relocation is green

The customer-call and verification handlers currently repeat the following
provider algorithm:

```text
list dispatches
    |
    v
revalidate account lifecycle
    |
    v
domain-specific reconciliation
    |
    v (none)
reject persisted-identity conflict
    |
    v
revalidate account lifecycle, then create
    |
    v (retryable or ambiguous create failure)
list again, revalidate, and reconcile
    |
    v
return one validated dispatch or a bounded delivery error
```

The second implementation phase extracts that exact repeated algorithm into
one private typed async function in `_livekit_delivery.py`. It receives explicit
common provider and lifecycle inputs plus one typed domain reconciliation
function. It returns one validated `LiveKitDispatch` or raises an existing
bounded delivery error.

The helper owns:

- provider listing and creation;
- provider configuration and failure mapping;
- the checks around retryable/ambiguous create recovery;
- repeated current-account lifecycle validation; and
- common persisted-identity/no-result conflict policy.

The helper does not own:

- customer-call or verification payload validation;
- domain contract parsing or identity rules;
- readiness or verification-window policy;
- customer or verification locks;
- domain snapshot construction; or
- call/activation persistence.

Customer and verification reconciliation remain separate named functions.
There is no strategy class, inheritance tree, general workflow engine, or
provider-agnostic orchestration framework.

### 6. Perform clean deletion and accurate renaming

`outbox_topics.py` is deleted after every production and test import has moved.
No compatibility facade remains because all consumers are internal and a
facade would make module-global monkeypatch behavior misleading.

The old outbox-only files under `workers/jobs` are deleted after their package
migration. Repository-wide searches must find no old import path.

`phone_provisioning_job` is renamed `provision_phone_number` when it moves to
`outbox/phone_provisioning.py`. It is an internal operation invoked by the
`phone.provision` adapter, not an independently registered ARQ job. Its inputs,
provider behavior, persistence, and return behavior do not change.

The unregistered legacy `workers/jobs/summary.py` and its three dedicated
legacy tests are deleted. Tests for `summary.generate` remain and continue to
cover the production path.

## Implementation sequence

### Phase one: behavior-preserving relocation

1. Record the current focused-test baseline.
2. Add registry completeness and worker-import characterization tests.
3. Establish `workers/outbox`, `failures.py`, and the explicit registry.
4. Move the delivery engine and existing outbox-only modules.
5. Split `outbox_topics.py` into the approved family modules without changing
   handler logic.
6. Move and rename the phone provisioning operation.
7. Update every production import, test import, and monkeypatch target.
8. Derive the supported topic set from the payload schema mapping.
9. Delete the god module, old relocated modules, legacy summary job, and its
   legacy-only tests.
10. Run the focused, integration, concurrency, startup, readiness, and complete
    API suites.

Phase two does not begin unless phase one is completely green.

### Phase two: tested LiveKit DRY extraction

1. Add focused tests for the common provider algorithm before extracting it.
2. Move only the proven repeated policy into `_livekit_delivery.py`.
3. Keep domain validation, reconciliation, locking, and persistence in their
   family modules.
4. Run both complete dispatch suites and relevant integration/concurrency
   suites.
5. Run the complete API quality gate again.

Movement and behavioral refactoring are separate reviewable checkpoints even
when implemented on one branch. A failure in the second phase must be
diagnosable against a known-green relocated baseline.

## Test design

All existing tests for delivery, topic handlers, concurrency, provider error
mapping, idempotency, transaction safety, and reconciliation are retained and
updated to explicit module paths. Private pure reconciliation helpers may
continue to receive focused unit tests; broad module-global monkeypatches are
retargeted to the module that actually owns the dependency.

Phase-one additions prove:

- registry keys exactly equal the payload-schema-derived supported topics;
- every registered handler is callable;
- importing worker settings and acquiring the default registry creates no
  circular import;
- injected handler mappings still override the default registry;
- importing the delivery engine alone does not eagerly import topic providers;
  and
- no legacy outbox module path remains.

Phase-two LiveKit tests cover both customer and verification use of the shared
provider policy:

- one existing valid dispatch;
- successful creation;
- retryable create failure recovered by relisting;
- retryable create failure with no recovered dispatch;
- terminal provider failure;
- provider configuration failure;
- a persisted identity that no longer resolves;
- persisted-identity mismatch;
- malformed, foreign, or wrong-contract metadata;
- multiple named dispatch conflict;
- an empty provider dispatch identifier; and
- account lifecycle invalidation before creation and during ambiguous recovery.

The domain suites continue independently proving customer readiness and
identity rules, verification window/session rules, lock selection, and correct
call/activation persistence.

The final quality gate includes formatting, linting, typing, focused tests, and
the complete API suite. Coverage is compared at the suite level because file
paths change. The reference pre-change API result is 2,731 passed, 130 skipped,
90.19% line coverage, and 77.81% branch coverage; environment-dependent skips
may vary, but total line and branch coverage must not materially regress.

Live Telnyx, LiveKit, Gemini, storage, or other external-provider tests are not
added for a structure-preserving refactor. Existing boundary fakes exercise
the required success, ambiguity, failure, and race semantics deterministically.

## Risks and controls

| Risk | Control |
| --- | --- |
| Import or monkeypatch regression | Atomic call-site migration, no facade, and repository-wide legacy-path searches. |
| Circular imports | Handler errors live in `failures.py`; only the registry imports all handlers; delivery uses a local default lookup. |
| Behavior drift hidden by file movement | Full green phase-one checkpoint before the LiveKit extraction. |
| Over-generalized shared helper | One private typed function limited to the exact repeated provider algorithm. |
| Eager provider initialization | Import-time test and local registry loading. |
| Topic accepted without a handler | Exact supported-topic/registry equality test. |
| Misleading dead interfaces survive | Delete the god module and legacy summary job; rename the internal provisioning operation. |
| Coverage appears changed because paths moved | Compare total suite line and branch coverage and retain domain behavior tests. |

## Acceptance criteria

The design is complete when:

1. The approved `app/workers/outbox` package is the only worker-side home for
   outbox delivery and outbox-only operations.
2. `outbox_topics.py` and the legacy summary job no longer exist.
3. There are no compatibility exports or legacy import paths.
4. `phone_provisioning_job` has become `provision_phone_number` with unchanged
   behavior.
5. Supported topics derive from reference payload schemas, and the explicit
   registry is exactly complete.
6. Every topic retains its prior validation, transaction, idempotency, retry,
   conflict, and provider behavior.
7. Customer and verification dispatch use the same tested provider-delivery
   primitive while retaining separate domain rules.
8. Worker import, focused, integration, concurrency, readiness, typing, linting,
   coverage, and full API checks pass.
9. No database, environment, queue, deployment, or live-provider change is
   required.
