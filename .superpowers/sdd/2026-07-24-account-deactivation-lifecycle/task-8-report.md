# Task 8 Report: PostgreSQL concurrency, drainage, and preservation

## Status and commit

- Status: complete
- Starting HEAD: `265ee1802de410591b8df26c4eecb58b6ce63c4f`
- Commit: included in `test: prove account deactivation concurrency`
- Scope: Task 8 API/worker evidence and the concrete drainage correction only;
  no Task 9 web work

## Files

- Created
  `apps/api/tests/integration/test_account_deactivation_concurrency.py`.
- Modified the four planned integration/call-history evidence files:
  `test_livekit_dispatch_concurrency.py`,
  `test_forwarding_verification_privacy.py`,
  `test_integrity_constraints.py`, and `test_call_history_api.py`.
- Corrected `app/workers/jobs/account_deactivation.py` only where the new
  PostgreSQL drainage test exposed premature release/reset risk.
- Reordered the lifecycle locks in `account_lifecycle_service.py`,
  `billing_service.py`, and the worker reset boundary to the approved
  user -> subscription -> phone -> operation ordering.
- Updated one stale generation-key expectation in
  `test_outbox_delivery.py`. Starting HEAD produced the generation-bound key
  `activation:provision:{activation_id}:g1`, while this one test still expected
  the superseded pre-generation key.

## RED evidence

Command:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call \
TEST_REDIS_URL=redis://127.0.0.1:56381/0 \
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
tests/integration/test_account_deactivation_concurrency.py
```

Result before the product correction: `4 failed`. For each of `pending`,
`connected`, `ending`, and `finalizing`, delivery reached the fake Telnyx
`release_number` method even though an active call row remained. The failure
was the intended `AssertionError: an active call must prevent number release`.

An earlier sandboxed attempt could not open a local socket and produced four
fixture errors. It was rerun with authorized local-container access; the
`4 failed` result above is the authoritative product RED.

The complete integration suite later exposed one independent starting-HEAD
compatibility-test failure:
`test_concurrent_provisioning_confirmation_creates_one_durable_intent`
expected the obsolete provisioning key. The isolated case reproduced as
`1 failed`; after the literal-only correction it passed as `1 passed`.

## GREEN evidence

- First drainage rerun: `4 passed in 1.46s`.
- Expanded new PostgreSQL file: `10 passed`, then `11 passed` after making both
  Stripe/owner commit orders explicit.
- Planned four-file Task 8 integration run: `32 passed in 10.19s` before the
  final two-order expansion.
- Final focused lifecycle, Stripe, Task 8, call-history, and worker regression
  command after lock-order review: `154 passed in 25.90s`.
- Final entire `apps/api/tests/integration` command:
  `103 passed in 32.07s`; output contained no skipped tests.
- Dedicated call-history run before final grouping:
  `29 passed in 4.30s`.
- Dedicated account-deactivation worker regression run:
  `26 passed in 3.68s`.
- Changed-scope Ruff: `All checks passed!`.
- Changed production-scope mypy:
  `Success: no issues found in 3 source files`.
- `git diff --check`: exit `0`.

All PostgreSQL runs used isolated Compose projects on PostgreSQL port `55434`
and Redis port `56381`.

## Barrier and race ordering matrix

| Race | Explicit ordering/barrier | Durable result |
|---|---|---|
| owner / owner | two independent `AsyncSession`s and `asyncio.Barrier(2)` | both returned the same operation ID; one operation row, one generation increment (`1 -> 2`), one reference-only outbox row |
| owner / final Stripe, owner commits first | first session holds owner row; `asyncio.Event` proves the competing session started | one owner-triggered operation and one reference-only event; Stripe subscription converges to canceled |
| owner / final Stripe, Stripe commits first | same explicit lock/event ordering in reverse | one subscription-ended operation and one reference-only event; later owner request returns it |
| admission / deactivation, admission commits first | two independent sessions; signaling repository/event holds the competing lock attempt | one stable call row and one dispatch event; the admitted call may finish |
| admission / deactivation, deactivation commits first | same ordering in reverse | no call row and no dispatch event; admission is denied |
| incomplete generation / generation | two sessions and `asyncio.Barrier(2)` | exactly one incomplete operation commits; the partial unique constraint rejects the other |

Every convergence assertion uses stored row counts and stable IDs. The
deactivation event payload is exactly `{"operation_id": "<stable UUID>"}`.

## Active-call drainage matrix

| Call status | release calls | operation result |
|---|---:|---|
| `pending` | 0 | retryable `account_call_draining`; release/reset/completion remain null |
| `connected` | 0 | retryable `account_call_draining`; release/reset/completion remain null |
| `ending` | 0 | retryable `account_call_draining`; release/reset/completion remain null |
| `finalizing` | 0 | retryable `account_call_draining`; release/reset/completion remain null |

The stable call ID and status are re-read after each failed delivery.

## Stale-work/provider-call matrix

| Work | Authoritative rejection evidence | Provider calls |
|---|---|---:|
| stale `phone.enable` / go-live | `dispatch_ineligible`; account remains `deactivating`, generation 2 | 0 Telnyx |
| stale invoice | subscription remains active locally and no usage-grant row is created | 0 external |
| stale `phone.provision` | `dispatch_ineligible`; no provisioning row or number mutation | 0 Telnyx |
| stale verification dispatch | `dispatch_ineligible`; claimed activation ID/state remains unchanged | 0 LiveKit |

Only fakes were used; no provider credentials or network provider calls were
available to the tests.

## Generation blocking

With generation 2 deactivation incomplete and the old phone projection still
present:

- checkout eligibility is false and reports lifecycle generation 2;
- local checkout cannot create a generation-2 subscription;
- provisioning raises the stable `account_deactivating` blocker;
- the incomplete operation, old phone ID, user status, generation, and zero
  provisioning-row count remain unchanged.

## Cleanup and owner-readable preservation

Successful worker completion proves:

- only the active phone assignment and `PhoneNumberProvisioning` row are
  deleted;
- the call's phone FK is detached;
- provisioning consent/key, verification window/session/result, forwarding
  verification, go-live approval, activation time, and number-specific carrier
  fields are cleared;
- profile confirmation and activation row IDs remain;
- user/profile, agent content, call, message/transcript, structured/legacy
  summary, recording metadata/operation, notification, usage-ledger,
  summary-event, and canceled subscription IDs remain;
- agent content remains with `is_enabled = false`;
- subscription history remains with `status = canceled`;
- the operation retains its private provider identity/completion evidence.

The inactive owner can list the stable call ID, fetch its detail, and obtain a
fake signed recording URL. A different authenticated owner receives `404` for
that same stable call ID.

## Lock order and provider-I/O proof

- Call finalization remains unchanged and call-first.
- Deactivation first makes the account non-serving, then checks active-call
  existence in `_has_active_call` using a standalone transaction that locks
  neither the user nor call row.
- Active-call existence is rechecked separately before number release and
  again before projection reset.
- Lifecycle entry and Stripe convergence lock user -> subscription -> phone ->
  operation; outbox creation follows the operation.
- Projection reset locks user -> phone -> operation, the canonical subset
  needed by that boundary, and never co-locks a call row.
- Fake Stripe/Telnyx providers inspect every session created by the worker and
  assert that none has an active ORM transaction at provider invocation.
- Observed provider order for the successful path is Telnyx disable, Stripe
  cancel, Telnyx release. No provider I/O occurs while row locks are held.

## Teardown

All disposable projects (`presvo-task8-red`, `presvo-task8-final`, and
`presvo-task8-lockorder`) were torn down with
`down --volumes --remove-orphans`. PostgreSQL and Redis
containers, networks, and named volumes were all reported removed.

## Privacy, scope, and concerns

- Assertions and outbox payloads use stable references, not customer content
  or provider credentials.
- No real Stripe, Telnyx, LiveKit, Redis provider, or credential was used.
- Historical data remains owner-scoped; the tests do not weaken authorization.
- No plan, design, ledger, migration, documentation outside this report, or
  web file was changed.
- Concern resolved: one full-suite test retained a pre-generation operation-key
  literal from before the starting HEAD production behavior. Parent approval
  authorized the minimal literal correction; production behavior was not
  changed for it.
- Remaining concerns: none.
