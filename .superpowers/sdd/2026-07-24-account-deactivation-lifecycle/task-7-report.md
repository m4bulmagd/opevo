# Task 7 Report: Subscription cancellation convergence and reactivation

## Status and commit

- Status: complete
- Starting HEAD: `9184822506dcedafa034a7c6a8a5fe882ec377f0`
- Commit: `feat: converge subscription lifecycle with account state` (the commit
  containing this report; final SHA is recorded in the parent handoff)
- Scope: Task 7 only. No Task 8 concurrency/preservation work was added.

## RED evidence

The exact nine-file command from the brief was started first with
`UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ...`. It displayed the
expected lifecycle failures but hit the known sandbox async hang and was
interrupted after approximately three minutes. Complete failure evidence was
then captured with direct `.venv/bin/python -m pytest` runs:

- Policy/repository/session/API seam run with `-x`: `1 failed, 18 passed`;
  checkout rejected the new account-state arguments.
- Focused webhook lifecycle run: `10 failed`; scheduled fields were unchanged,
  final cancellation left the user active, stale generations replaced/granted,
  and matching reactivation did not carry generation.
- Stale invoice run: `2 failed`; both stale cases granted minutes.
- Provider-free local lifecycle run: `1 failed`; the provisioning key lacked
  lifecycle generation.
- Self-review edge REDs: terminal `subscription.updated(status=canceled)` and
  staged `incomplete -> active` replacement both failed (`2 failed`).
- Additional boundary REDs proved inactive reactivation without an old
  subscription and malformed-generation rejection were initially absent.

Each production change followed its observed failing test.

## GREEN evidence

Final exact Task 7 command, using direct Python for the known sandbox behavior:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55434/ai_call \
TEST_REDIS_URL=redis://127.0.0.1:56381/0 \
UV_CACHE_DIR=/tmp/uv-cache .venv/bin/python -m pytest -q \
  tests/billing/test_billing_api.py \
  tests/billing/test_stripe_webhooks.py \
  tests/repositories/test_subscription_repository.py \
  tests/services/test_subscription_access_policy.py \
  tests/services/test_subscription_service_sessions.py \
  tests/services/test_billing_session_service.py \
  tests/integration/test_subscription_disable_intent.py \
  tests/integration/test_postgres_subscription_service_sessions.py \
  tests/integration/test_local_activation_to_number.py
```

Result: **165 passed, zero skipped, 14.70s**.

Disposable services used `COMPOSE_PROJECT_NAME=presvo-account-pg`, PostgreSQL
port `55434`, and Redis port `56381`. Both containers, the network, and both
volumes were stopped and removed after the run.

Targeted SQLite lifecycle regressions:

```bash
.venv/bin/python -m pytest -q \
  tests/activation/test_local_billing_service.py \
  tests/activation/test_activation_provisioning_service.py \
  tests/services/test_account_lifecycle_service.py \
  tests/workers/test_account_deactivation.py
```

Result: **55 passed, 6.01s**.

Static verification:

- Ruff check on all changed sources/tests: clean.
- Mypy on all changed sources/tests: `Success: no issues found in 18 source files`.
- `git diff --check`: clean before report creation and repeated before commit.

## Scheduled cancellation and reversal matrix

| Stripe state | Local projection | Account/phone effect |
|---|---|---|
| `updated`, active, `cancel_at_period_end=true` | flag true; effective time from `cancel_at`, else period end | account remains active; phone unchanged; no deactivation |
| later `updated`, `cancel_at_period_end=false` | flag false; effective time cleared | account remains active; phone unchanged |
| `updated` or `deleted`, terminal `canceled`, current ID/generation | subscription canceled; one current operation/outbox | account becomes deactivating; lifecycle worker owns routing cleanup |
| duplicate terminal event | watermark/webhook replay is idempotent | no second operation or outbox |

The Task 7 brief asked the webhook to set
`operation.subscription_canceled_at`, but starting HEAD deliberately assigns
phase timestamps to the Task 6 reconciler and the model constraint requires
routing disablement first. Per parent resolution, webhook tests assert both
timestamps remain null, then prove the reconciler sets
`routing_disabled_at`, `subscription_canceled_at`, and completion in order.

## Event freshness and generation matrix

| Event | Result |
|---|---|
| current ID, exact current generation | projection may advance |
| matching inactive replacement, `active`/`trialing` | account reactivates; no phone enable |
| matching replacement first `incomplete`, later same-ID `active` | staged projection advances and then reactivates |
| old generation, missing generation after generation 1, or malformed generation | ignored; no activation, grant, phone intent, or deactivation |
| missing generation on a never-advanced generation-1 account | accepted as legacy generation 1 |
| prior canceled ID after reactivation | ignored |
| exact subscription stored on an incomplete owner operation | terminal event converges on that operation, even for legacy missing metadata |
| older/replaced subscription or invoice | cannot replace, reactivate, grant, enable, or deactivate the current lifecycle |

## Checkout and reactivation eligibility matrix

| Account state | Other state | Checkout |
|---|---|---|
| active | no subscription or replaceable terminal subscription | allowed |
| active | nonterminal subscription | blocked |
| deactivating | any subscription/phone/operation state | blocked |
| inactive | incomplete operation exists | blocked |
| inactive | old phone row exists | blocked |
| inactive | old subscription is nonreplaceable | blocked |
| inactive | operation complete, no phone, old subscription absent/replaceable | allowed with locked lifecycle generation |

Portal creation remains available whenever the subscription projection has a
Stripe customer ID, independent of active/inactive account status.

## Transaction and provider-I/O proof

- Checkout eligibility locks user, incomplete operation, subscription, and
  phone; returns the captured lifecycle generation.
- The route ends the business transaction before calling the hosted Stripe
  boundary and passes that captured generation without re-reading request data.
- Exact four-key metadata is copied to checkout and subscription metadata:
  `clerk_user_id`, `user_id`, `plan_tier`, `lifecycle_generation`.
- Webhook handling and local fake billing use only database/repository work;
  no Stripe, Telnyx, Redis, or other provider I/O occurs while row locks are
  held.
- Payment grants usage only. It does not create `phone.enable`, set a phone
  active, or make the account call-ready.

## Fake identity and key proof

Generation 1 and generation 2 use distinct identities:

- `local_subscription_<user_id>_g1` / `_g2`
- `local_invoice_<user_id>_g1` / `_g2`
- `activation:provision:<activation_id>:g1` / `:g2`
- retry delivery keys extend the generation-bearing provider key

The provider-free two-cycle test deactivates generation 1, proves the old phone
is removed, reactivates generation 2, and provisions a different deterministic
fake number.

## Privacy, scope, and review

- Outbox payload remains reference-only: `{"operation_id": "<uuid>"}`.
- No provider credentials, raw provider responses, phone content, signed URLs,
  or real customer data were added to persistence, logs, tests, or this report.
- All tests use fakes/stubs; no real Stripe, Telnyx, or external provider was
  contacted.
- The standards review's introduced direct user-status writes were moved into
  `UserRepository`; duplicated invoice generation validation was consolidated.
- The existing hosted-session service already contained the Stripe SDK call;
  Task 7 only changed its metadata contract, so moving that whole boundary was
  treated as pre-existing architecture debt outside this task.
- No unresolved Critical or Important review findings remain.

## Files changed

Task 7 billing/repository/service/schema/router files, their focused tests, the
related local-billing/provisioning regression tests, and
`user_repository.py` for the standards-compliant reactivation write. No plan,
design, migration, ledger, documentation-status, provider, or Task 8 files
were changed.

## Fix round 1/5

Rejected base: `158dab525d993d10920c2f969b35db4b536ff14a`.

### Root causes and fixes

1. Current-generation invoices delivered while an account was inactive
   returned normally from invoice-context resolution. That committed the
   webhook identity even though no subscription state, grant, failure state,
   or watermark was applied. Premature exact-generation invoices now use the
   existing retryable lifecycle-conflict path: the transaction and webhook
   identity roll back with HTTP 503. Once an exact-owner subscription event
   safely reactivates the account, Stripe retry applies the invoice exactly
   once. Inactive delivery performs no grant, phone intent, service enablement,
   or provider I/O.
2. Subscription and invoice ingestion resolved `clerk_user_id` but ignored the
   internal `user_id` metadata. Present internal ownership metadata is now
   parsed as a UUID and must equal the Clerk-resolved locked user. Malformed or
   conflicting values are ignored safely. Missing ownership metadata remains
   compatible only when generation metadata is also absent for a legacy
   generation-1 event, or for the previously approved exact
   incomplete-operation terminal convergence. Partial metadata that declares
   generation 1 but omits `user_id` is rejected; missing metadata cannot
   attach, reactivate, or grant a later generation.
3. Same-ID legacy subscriptions deliberately preserved a null event watermark,
   and invoice watermark advancement was conditional on an existing non-null
   value. Every accepted non-null Stripe event timestamp now becomes the
   watermark. The existing routing/nonrouting stale rule still protects a
   legacy terminal projection before its first accepted timestamp.
4. Two required broader suites still expected the pre-fencing local identities.
   Their assertions and grant lookup now use
   `local_subscription_<user_id>_g1` and `local_invoice_<user_id>_g1`.
   Production fake identity generation was already correct and was not changed.

### RED evidence

- Invoice-before-subscription paid and payment-failed cases both failed because
  the premature response was HTTP 202 instead of retryable HTTP 503:
  **2 failed**.
- Subscription and invoice ownership cases for conflicting, malformed, and
  missing-current `user_id` all accepted unsafe state: subscriptions attached
  and invoices granted, for **6 failed**.
- The follow-up partial-metadata mutation (explicit generation 1 with no
  `user_id`) also attached/granted before the legacy boundary was narrowed:
  **2 failed**.
- Ordered legacy schedule delivery left the watermark null; out-of-order
  delivery allowed an older schedule to overwrite a newer reversal:
  **2 failed**.
- The broader-gate review identified the old literal subscription identity and
  old grant-source identity while `LocalBillingService` already emitted the
  generation-specific `_g1` values. This was a test-contract correction, not a
  production behavior change.

### GREEN and final verification

- Focused invoice ordering: **2 passed**.
- Expanded ownership matrix plus approved legacy compatibility:
  **11 passed**.
- Focused ordered/out-of-order legacy schedule matrix: **2 passed**.
- Final exact nine-file Task 7 PostgreSQL/Redis gate: **177 passed, zero
  skipped, 17.93s**.
- Adjacent lifecycle gate: **55 passed, 6.17s**.
- Required development API and PostgreSQL usage-concurrency gates:
  **22 passed, 5.84s**.
- Final combined regression gate after all behavior edits:
  **254 passed, zero skipped, 27.51s**.
- Ruff: all checks passed.
- Mypy: `Success: no issues found in 6 source files`.
- `git diff --check`: clean before report append and repeated before commit.
- Disposable PostgreSQL and Redis containers, network, and volumes were
  removed after verification.

No Task 8 behavior was added. Account-deactivation webhook timestamps remain
truthful: webhooks do not set reconciler-owned phase timestamps, and the
existing reconciler still advances routing disablement before subscription
cancellation and completion.
