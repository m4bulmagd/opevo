# Terminal Readiness Follow-ups Design

**Date:** 2026-08-04

**Status:** Owner-approved written contract

## Context

The final whole-branch review of the canonical activation-mode number-readiness
change found one remaining presentation defect and two smaller contract/test
follow-ups.

The canonical number fact is already fail-closed: a provisioning row is usable
only when it succeeded, points to the exact assigned phone, and that phone has a
nonblank provider identifier. Runtime readiness therefore rejects a terminal
`succeeded` row whose assignment is missing or inconsistent. Activation policy,
however, currently treats every false number fact other than an explicit
provider `failed` status as active provisioning. When provisioning consent is
already present, the API can consequently return an indefinitely refreshable
`provisioning` stage even though the stored `succeeded` status is terminal and
retry is disabled.

The same full-suite repair also made one outbox test fixture always create a
successful provisioning row. Most tests in that module intentionally run with
activation disabled, so the fixture weakens direct proof that the legacy outbox
path remains independent of activation workflow records.

Finally, canonical number readiness adds a new public blocker and changes
routing decisions while the returned readiness policy version remains
`runtime-v4`. Earlier semantic readiness changes advanced this version.

The owner selected Issues **38A**, **39A**, and **40A**.

## Decisions

### 1. Classify terminal assignment inconsistency as a non-retryable failure

Keep the existing `ActivationStage.PROVISIONING_FAILED`; do not introduce a new
workflow stage. Add one explicit activation blocker value:

```text
number_assignment_inconsistent
```

After the existing profile, billing, and provisioning-consent gates, activation
policy applies these number rules in order:

| Provisioning state | Canonical number fact | Activation result |
| --- | --- | --- |
| `failed` | false | Existing retryable/non-retryable provider failure path |
| `succeeded` | false | `provisioning_failed`, blocker `number_assignment_inconsistent`, no next action |
| absent, `queued`, or `running` | false | Existing refreshable `provisioning` stage |
| `succeeded` | true | Continue to forwarding and later activation gates |

The pre-existing no-consent behavior remains unchanged. In particular, a
legacy or corrupt terminal row without provisioning consent continues to stop
at `provisioning_consent_required`; this follow-up does not reinterpret or
fabricate historical consent.

The inconsistency branch does not change the stored provisioning row to
`failed`, enqueue another order, repair database links, or mutate the provider.
The provider operation succeeded; the application cannot prove that its result
belongs to the assigned phone. The response reports that distinction honestly.

### 2. Give the terminal inconsistency explicit UI guidance

The number milestone continues using the existing `provisioning_failed`
component boundary. Before its ordinary retryable and business-details failure
branches, it recognizes `number_assignment_inconsistent` in the snapshot
blockers and renders a deterministic non-retryable state.

The state explains that Presvo recorded provisioning as complete but could not
verify the assigned number. It must not:

- render the active-provisioning spinner;
- offer the retry action;
- claim that another number will be ordered;
- direct the user to correct unrelated business details; or
- expose provider identifiers, phone numbers, or internal row identities.

It includes the stable reference `number_assignment_inconsistent` so support or
operators can distinguish this bounded data-consistency failure from an
ordinary provider failure. No support system, new route, or new workflow action
is introduced by this change.

### 3. Restore provisioning-free legacy outbox coverage

Change the test-local `_seed_dispatch` helper to accept an explicit
`with_provisioning: bool = False` option. Its default recreates the historical
legacy fixture with no `PhoneNumberProvisioning` row.

Only activation-enabled tests whose intended starting state is a fully ready
customer pass `with_provisioning=True`. At least one activation-disabled
outbox-delivery integration test asserts that the provisioning-row count is
zero before it proves dispatch delivery succeeds.

This is test-only fixture design. It does not change outbox, dispatch, or
readiness production behavior. The explicit option is preferable to a second
duplicated seed helper because the phone, subscription, call, usage, and event
setup are otherwise identical.

### 4. Advance the readiness policy contract to `runtime-v5`

Change `CustomerReadinessPolicy.POLICY_VERSION` from `runtime-v4` to
`runtime-v5`. The version identifies the semantic policy that produced public
readiness decisions, not merely the response schema.

Update exact API/service assertions that own this contract. Web consumers keep
treating the value as an opaque string; no client branch or compatibility shim
is required. Old stored data is unaffected because the policy version is
computed in responses rather than persisted.

## Data flow

```text
stored provisioning + assigned phone
                 |
                 v
canonical number_is_provisioned fact
                 |
          false + succeeded
                 |
                 v
ActivationPolicy: provisioning_failed
blocker: number_assignment_inconsistent
                 |
                 v
number milestone: explicit non-retryable guidance
```

Runtime readiness continues to report `number_not_provisioned` and the existing
`number_provisioning_failed` readiness stage for the same terminal invalid
assignment. Activation and runtime vocabulary are intentionally different at
their API boundaries, but both classify the condition as terminal and
non-routable.

## Error and edge-case behavior

- Missing, queued, and running provisioning remain pending and refreshable.
- An explicit provider `failed` status retains the existing retry behavior and
  `number_provisioning_failed` activation blocker.
- A succeeded exact link with a nonblank provider identity remains ready.
- A succeeded mismatched link, missing assigned phone, missing provider
  identity, or whitespace-only provider identity is a terminal assignment
  inconsistency after consent.
- The inconsistency is non-retryable because repeating number acquisition could
  buy or assign another number without resolving ownership of the first result.
- Activation-disabled routing continues to work without any provisioning row.
- No raw provider identity, phone number, credential, transcript, or `.env`
  content appears in UI copy, tests, logs, or documentation evidence.

## Testing strategy

Implementation follows strict red-green-refactor.

1. Add an activation-policy unit case for consented `succeeded` provisioning
   with `number_provisioned=False`; observe the current `provisioning` result.
2. Add an activation-snapshot case with consent preserved and an exact-link or
   provider-identity mismatch; require `provisioning_failed`, the explicit
   inconsistency blocker, no next action, non-retryable number state, and
   fail-closed runtime readiness.
3. Add a number-milestone UI case that requires the explicit inconsistency copy
   and reference while rejecting the spinner, retry control, and business-profile
   correction link.
4. Add the zero-provisioning assertion to an activation-disabled outbox test;
   observe it fail against the over-provisioned shared fixture.
5. Change exact policy-version expectations from `runtime-v4` to `runtime-v5`
   and observe them fail before the constant changes.
6. Implement the smallest production and fixture corrections, then run focused
   API/web tests, realistic mutations of each new branch, full API/web/agent
   suites, static checks, and the existing isolated PostgreSQL concurrency
   proof.

Tests assert public decisions and rendered behavior rather than source text.
Mutation checks must demonstrate that restoring the generic pending branch,
adding provisioning back to the legacy fixture, or keeping `runtime-v4` fails
at least one intended test.

## Performance and maintenance

The activation-policy change adds one constant-time status comparison over
already-loaded facts. The UI checks one short blocker list. There is no query,
provider call, cache, allocation of consequence, or new dependency.

Reusing the existing failure stage avoids expanding every stage router and
navigation mapping. The explicit blocker preserves diagnostic precision.
Parameterizing the shared test fixture removes duplication while keeping each
call site's activation assumptions visible.

## Non-goals

- Database repair, migration, or deletion.
- Retrying or compensating a succeeded provider operation.
- Telnyx, LiveKit, Clerk, Stripe, recording, storage, or realtime changes.
- New support infrastructure, workflow stage, route, or provisioning command.
- Recreating or reconfiguring the live local stack.
- Resolving deferred documentation Issues 33–35 in these implementation
  commits.
