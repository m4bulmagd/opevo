# Readiness and Clerk Port Consistency Design

**Date:** 2026-08-04

**Status:** Owner-approved direction; written contract awaiting owner review

## Context

The final Clerk-first/Telnyx branch review found two remaining consistency
defects.

First, activation now requires a succeeded phone-number provisioning record
that points to the exact assigned phone and an assigned phone with a nonblank
provider identifier. Runtime readiness still evaluates the provisioning row
and assigned phone independently. A corrupt or legacy mismatch can therefore
produce one activation response whose workflow stage requires provisioning
while `runtime_readiness.can_route` is `true`.

Second, development Compose derives the web URL, published port, API URL, and
CORS origins from `WEB_PORT`, but its default `CLERK_AUTHORIZED_PARTIES` remains
fixed to port 3000. A developer choosing another web port gets a valid web
server and an invalid exact Clerk `azp` allowlist unless they discover and set
an undocumented second variable.

## Decisions

### 1. One canonical activation-mode number-provisioning fact

Add one pure service-level predicate with this contract:

```python
def number_is_provisioned(
    *,
    provisioning: PhoneNumberProvisioning | None,
    phone_number: PhoneNumber | None,
) -> bool:
    ...
```

It returns `True` only when all of these conditions hold:

1. a provisioning record exists;
2. its status is exactly `succeeded`;
3. an assigned phone exists;
4. the provisioning record's `phone_number_id` equals that phone's ID; and
5. the phone's provider identifier is nonblank after trimming whitespace.

The predicate belongs in a focused service module rather than either consumer.
`build_customer_readiness_snapshot` computes the fact once and stores it on
`CustomerReadinessSnapshot`. `ActivationSnapshotService` reads that stored fact
for `ActivationFacts.number_provisioned` and `number.provider_ready`; it must not
restate the predicate.

When activation is required, customer-readiness policy treats a false fact as
the explicit `number_not_provisioned` blocker. It cannot activate, route, or
dispatch, and its readiness stage follows the existing number-provisioning
failure/pending vocabulary. This makes the activation stage, runtime readiness,
dashboard admission, and dispatch gate agree.

Activation-disabled legacy mode intentionally retains its historical phone
presence/provider-ID rule. That is a different product policy rather than a
second implementation of the activation invariant. It avoids silently making
old provider-managed deployments depend on activation workflow records they
were never required to create.

No migration or automatic repair is part of this change. Existing mismatched
activation-mode data fails closed and exposes a bounded blocker; operators can
investigate it without the application fabricating links or mutating provider
state.

### 2. `WEB_PORT` owns all default local web origins

Keep `CLERK_AUTHORIZED_PARTIES` as the explicit security override. When it is
unset, derive both default authorized parties from `WEB_PORT` using Compose's
supported nested interpolation:

```yaml
CLERK_AUTHORIZED_PARTIES: "${CLERK_AUTHORIZED_PARTIES:-http://127.0.0.1:${WEB_PORT:-3000},http://localhost:${WEB_PORT:-3000}}"
```

This preserves exact-origin validation and explicit override precedence. It
does not normalize token origins, widen the allowlist, or couple production
configuration to development defaults. The installed Compose implementation
was characterized with secret-free inputs: `WEB_PORT=3300` produced both local
origins on port 3300, while an explicit authorized-parties value replaced the
derived default exactly.

Documentation will state that changing `WEB_PORT` updates all standard local
origins automatically and that `CLERK_AUTHORIZED_PARTIES` remains available for
an intentional nonstandard allowlist.

## Data flow and boundaries

```text
provisioning + assigned phone
            |
            v
number_is_provisioned (one pure fact)
            |
            v
CustomerReadinessSnapshot.number_provisioned
        |                         |
        v                         v
runtime readiness policy    activation facts/response
        |                         |
        +-----------+-------------+
                    v
       consistent routing and UI decisions
```

The helper performs no I/O and introduces no repository or provider dependency.
The existing service layer still owns repository reads, and the policy remains
a deterministic evaluation of an immutable snapshot.

Development Compose performs the separate configuration flow:

```text
WEB_PORT ---------------------> web URL, published port, API CORS
    |
    +---> default Clerk authorized parties

CLERK_AUTHORIZED_PARTIES -----> exact explicit override, when supplied
```

## Error and edge-case behavior

- Missing provisioning, non-succeeded status, missing phone, mismatched phone
  ID, missing provider ID, and whitespace-only provider ID all produce
  `number_is_provisioned=False`.
- Activation-required readiness cannot return `can_route=True` when that fact
  is false, even if the phone projection and agent are otherwise active.
- Activation-disabled readiness retains its previous routing behavior and is
  covered explicitly so the compatibility boundary cannot drift accidentally.
- A custom `WEB_PORT` changes both loopback authorized parties.
- An explicit `CLERK_AUTHORIZED_PARTIES` value wins unchanged over the derived
  default.
- No credential, token, provider identifier, phone number, or `.env` content is
  printed by tests or documentation.

## Testing strategy

Implementation follows strict red-green-refactor:

1. Add a literal table-driven unit matrix for the pure provisioning predicate.
2. Extend the existing mismatched-link activation test to require
   `runtime_readiness.can_route=False` and the explicit blocker; observe the
   current contradiction as the RED failure.
3. Add customer-readiness policy coverage proving activation-required mismatch
   fails closed and activation-disabled legacy behavior is unchanged.
4. Add deployment/Compose tests proving `WEB_PORT=3300` updates every local
   origin and an explicit Clerk authorized-parties override still wins; observe
   the fixed-port output as RED.
5. Run focused API tests, the complete API suite including the isolated
   PostgreSQL concurrency test, Ruff, mypy, `git diff --check`, and the existing
   web/agent verification gates because readiness is consumed across service
   boundaries.

Tests assert observable results rather than source text. A realistic mutation
of the exact link check, activation-required guard, custom port, or override
precedence must fail at least one test.

## Performance and maintenance

The canonical predicate is constant-time and reuses already-loaded objects; it
adds no query, allocation of consequence, or provider call. Carrying one
boolean in the readiness snapshot is negligible. Centralizing the rule removes
duplicated knowledge and gives future readiness consumers one explicit fact.

Nested Compose interpolation removes the need to synchronize two port settings
in the standard path while retaining the explicit security override. No new
dependency, runtime service, cache, or abstraction layer is introduced.

## Non-goals

- Repairing or deleting existing database rows.
- Mutating Telnyx, LiveKit, Clerk, or recording-provider state.
- Changing the current account's keep-live decision.
- Enabling realtime or local recording infrastructure.
- Changing production Compose authentication requirements.
- Resolving deferred documentation Issues 33–35 in the implementation commits
  for Issues 36–37.
