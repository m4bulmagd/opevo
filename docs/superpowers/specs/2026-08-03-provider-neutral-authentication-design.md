# Provider-Neutral Authentication Design

**Date:** 2026-08-03
**Status:** Approved; implementation planning deferred

## Summary

Opevo will refactor its complete browser-to-API authentication path around a
provider-neutral seam while retaining Clerk as the only hosted authentication
adapter in phase one. The existing development-only local adapter remains
available. Supabase, or a future Opevo-owned authentication system, can be
added later by implementing the same interfaces without changing routes,
business logic, persistence relationships, Stripe ownership, or realtime
delivery.

Exactly one authentication provider is selected for an entire deployment.
Opevo does not support different providers for different users, concurrent
hosted providers, account linking, or identity merging. The final hosted
provider will be selected before production launch, so this work optimizes for
a clean global provider choice rather than a live user migration.

The application keeps its own UUID as the canonical user identifier. Provider
identifiers are used only to cross the authentication seam and resolve that
internal UUID. Every downstream module uses the internal UUID.

## Context

The API already has an asynchronous `AuthProvider` interface with Clerk and
local adapters, and most route handlers already use the application's internal
user UUID. The current seam is incomplete because Clerk terminology and
behavior leak through it:

- `UserIdentity` and `AuthenticatedUserIdentity` expose `clerk_user_id`;
- `users.clerk_user_id` and repository methods are provider-named;
- the REST dependency special-cases `LocalAuthProvider`;
- billing writes and reads `clerk_user_id` in Stripe metadata;
- realtime registers connections with the Clerk subject while published events
  use the internal UUID;
- configuration, shared logs, and runtime validation assume Clerk;
- Clerk webhook payload interpretation is coupled to the shared auth module;
- Next.js shared session, route protection, layouts, account identity, account
  security, and authentication pages import Clerk directly.

There are no production users and no deployment whose configuration or data
must remain backward compatible. Historical migration files remain immutable,
but active application terminology and configuration can change cleanly.

## Goals

1. Make the complete Next.js-to-FastAPI authentication path provider-neutral.
2. Preserve Clerk authentication, route protection, JWT verification, JWKS
   behavior, authorized-party policy, webhook provisioning, and failure
   semantics in phase one.
3. Preserve deterministic development-only local authentication.
4. Keep exactly one active provider per deployment.
5. Make the internal Opevo UUID the only user identifier consumed by routes,
   business logic, Stripe, and realtime.
6. Confine Clerk SDK imports, claims, payloads, settings, copy, and behavior to
   Clerk adapters, Clerk configuration, and Clerk-specific tests.
7. Make a future Supabase or Opevo-owned adapter additive rather than another
   cross-codebase refactor.
8. Keep configuration and authentication failures fail-closed and
   value-redacted.

## Non-goals

- Implementing Supabase authentication in phase one.
- Adding a placeholder Supabase adapter or Supabase dependency.
- Supporting more than one hosted provider in a deployment.
- Supporting multiple external identities for one Opevo user.
- Supporting account linking, identity merging, or provider selection per
  request or per user.
- Migrating production users, passwords, sessions, or provider identifiers.
- Building password storage, password reset, email verification, MFA, session
  revocation, or other custom-auth capabilities.
- Replacing Clerk's current JWT, JWKS, authorized-party, or webhook security
  policy.
- Rewriting historical Alembic migrations.

## Selected Approach

### Provider adapters around neutral interfaces

Opevo will retain a small token-verification interface and place provider
behavior behind adapters. Shared authentication code will map a verified
external identity to the internal Opevo user before returning control to a
route or realtime caller. The frontend will expose provider-neutral session,
route-protection, and authentication-control modules whose leaf adapters own
provider SDKs and UI.

This approach is selected because it creates locality for provider behavior
without introducing a general plugin framework or a multi-identity data model.
Clerk and local auth make the seam real in phase one; Supabase can become a
third adapter later.

### Rejected: terminology-only refactor

Merely renaming `clerk_user_id` and wrapping the current imports would be fast,
but Clerk session and provisioning assumptions would remain embedded behind
neutral names. Adding Supabase would require a second structural refactor.

### Rejected: full identity subsystem

An `auth_identities` table, provider registry, generic webhook dispatcher,
account linking, and concurrent-provider support solve requirements Opevo does
not have. They would increase schema, lifecycle, testing, and security
complexity without improving the planned single-provider deployment.

## Domain Terms and Invariants

### External identity

An `ExternalIdentity` is the verified result of a provider token:

```python
@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    external_user_id: str
    bootstrap_profile: ExternalUserProfile | None = None
```

Its interface has these invariants:

- `external_user_id` is a non-empty provider-issued identifier;
- the identifier has been authenticated before the value is returned;
- `bootstrap_profile`, when present, contains only adapter-trusted data;
- no caller-provided email or profile data can become a bootstrap profile;
- bearer tokens and complete provider claims are never retained or returned.

### External user profile

An `ExternalUserProfile` is the normalized input to user provisioning:

```python
@dataclass(frozen=True, slots=True)
class ExternalUserProfile:
    external_user_id: str
    email: str
```

Provider payload parsing and trust decisions occur before this model is
constructed. The shared provisioning module does not understand Clerk or any
future provider's event schema.

### Authenticated user

An `AuthenticatedUser` is the result exposed to routes and realtime:

```python
@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    internal_user_id: UUID
```

It intentionally does not expose `external_user_id`. A caller that only needs
authorization or ownership must not be able to choose the provider identifier
by convenience.

### Global provider selection

Exactly one adapter is selected for a process through `AUTH_PROVIDER`. The
selected provider applies to every user and request served by that deployment.
Provider identity is not persisted on each user row.

## Backend Architecture

### Token-verification seam

The backend retains one small external authentication interface:

```python
class AuthProvider(ABC):
    @abstractmethod
    async def verify_token(self, token: str) -> ExternalIdentity:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
```

`ClerkAuthProvider` owns Clerk claims, algorithms, authorized parties, signing
keys, and JWKS behavior. `LocalAuthProvider` owns constant-time local token
verification and returns its fixed trusted bootstrap profile. Neither adapter
queries application business tables.

The FastAPI lifespan is the composition root. It validates the selected
provider, constructs exactly one adapter, stores it on application state, and
closes it during shutdown. There is no request-scoped provider construction or
module-global provider instance.

### Identity resolution

A shared authentication module accepts a bearer token, the selected
`AuthProvider`, and a database session. It:

1. verifies the token;
2. looks up `users.external_user_id`;
3. if the user is absent and the verified identity contains a trusted
   `bootstrap_profile`, invokes shared user provisioning;
4. if the user is absent without a trusted profile, rejects the request as not
   provisioned;
5. returns only `AuthenticatedUser(internal_user_id=...)`.

REST dependencies and realtime authentication call this same module. Shared
code does not use `isinstance` checks to discover provider behavior.

### User provisioning

The existing user bootstrap behavior becomes a provider-neutral
`UserProvisioning` module. It accepts only an `ExternalUserProfile`, acquires
the existing external-ID bootstrap lock, creates or resolves the user, and
ensures the default agent configuration, business profile, and customer
activation records.

The Clerk webhook remains provider-specific at the transport edge:

1. the Clerk adapter verifies the Svix signature;
2. the Clerk schema adapter parses the event and extracts the primary email;
3. the adapter constructs `ExternalUserProfile`;
4. the shared provisioning module creates or resolves the application user;
5. the webhook event is committed with `provider="clerk"` for idempotency and
   audit history.

Clerk session verification does not start provisioning Clerk users from token
claims. A missing Clerk-provisioned user retains the existing safe rejection.
Local auth preserves first-request creation by returning a trusted local
bootstrap profile.

### Persistence

The active `users` model becomes:

```text
id                UUID, primary key
external_user_id  string(255), unique, non-null, indexed
email             string(320), unique, non-null
...existing application profile and lifecycle fields
```

There is no `auth_provider` column. The provider is global deployment
configuration, not user data. All foreign keys continue to reference
`users.id`.

The user repository exposes provider-neutral operations such as
`get_by_external_user_id()` and `create(external_user_id=..., email=...)`.
Only authentication, identity persistence, and user provisioning may use
`external_user_id`. General business repositories and modules use the internal
UUID.

### Stripe and realtime

New Stripe checkout and subscription metadata contains `user_id`, plan, and
lifecycle data, but no external provider identifier. Stripe webhook ownership
is resolved from the internal UUID and existing Stripe subscription/customer
relationships. Because there is no production Stripe state, no dual-read
legacy provider-ID path is required.

Realtime token authentication resolves the external identity to an internal
user before registering a WebSocket. Connection keys, Redis channels,
contract user IDs, and fanout comparisons all use the same internal UUID string.
This removes the current external-ID/internal-UUID mismatch.

## Frontend Architecture

### Server session seam

Shared Next.js code consumes a provider-neutral server-session result. It can
determine whether a request is authenticated and obtain a bearer access token,
but it does not receive a Clerk session object or require an external user ID.
The backend client continues sending the access token in the Authorization
header.

The Clerk server-session adapter wraps Clerk's server SDK. The local adapter
returns the configured development token. A future Supabase adapter can manage
its own cookies, refresh behavior, and access-token retrieval behind the same
shared interface.

### Route protection

The Next.js proxy delegates protected-route handling to the selected frontend
adapter. Clerk's middleware and route matcher live in the Clerk adapter. Local
auth may pass through only in development. Missing or incomplete selected
provider configuration cannot fall through to an unprotected route.

### Provider UI

Provider-specific sign-in, signup, sign-out, profile lookup, and credential
management remain leaf implementations selected by provider-neutral wrappers.
Shared layouts, pages, account modules, actions, and workspace modules do not
import provider SDKs or expose provider-specific copy.

The design deliberately does not invent one large universal UI interface for
every possible auth feature. Each shared wrapper exposes only the capability
the current product surface needs. A future provider may render a different
form or redirect flow without changing the shared page shell.

### Frontend composition

One server-only provider-selection module validates `AUTH_PROVIDER` and exposes
the selected session, route-protection, and UI adapters. Provider secrets stay
server-only. Browser-safe provider configuration is exported only when the
selected adapter explicitly requires it.

## Configuration

Phase one accepts:

```text
AUTH_PROVIDER=clerk | local
```

Clerk remains the standard/default provider. Local remains development-only.
`supabase` is not accepted until a working Supabase adapter exists; selecting
an unsupported or unavailable provider fails startup.

The old `AUTH_MODE` name is removed from active application code, Compose,
tests, examples, runbooks, and current architecture documentation. No deployed
environment requires an alias or deprecation period.

Provider-specific settings retain provider prefixes and are validated only by
the selected adapter:

- Clerk adapters own `CLERK_*` settings;
- local adapters own `LOCAL_*` settings;
- production rejects `local` regardless of local-token presence;
- the API and web process independently validate their required slice of the
  selected provider configuration;
- errors identify missing setting names but never their values.

Provider-specific webhook routers are registered only when their provider is
selected. Shared application routes are provider-independent.

## Request and Provisioning Flows

### Authenticated Clerk request

```text
Browser Clerk adapter
  -> Clerk access token
  -> shared backend client Authorization header
  -> ClerkAuthProvider verifies token
  -> ExternalIdentity(external_user_id)
  -> shared identity resolver
  -> users.external_user_id lookup
  -> AuthenticatedUser(internal_user_id)
  -> route/business module
```

### Clerk signup provisioning

```text
Clerk webhook
  -> Clerk signature/schema adapter
  -> ExternalUserProfile(external_user_id, email)
  -> shared UserProvisioning
  -> users row and default application records
```

### Local authenticated request

```text
Local frontend adapter
  -> local bearer token
  -> LocalAuthProvider verifies token
  -> ExternalIdentity(external_user_id, trusted bootstrap profile)
  -> shared identity resolver
  -> resolve or provision local user
  -> AuthenticatedUser(internal_user_id)
```

## Failure Behavior

Shared failure families remain bounded and provider-neutral:

| Failure | REST behavior | Frontend/route behavior |
|---|---:|---|
| Missing, invalid, expired, or rejected credentials | generic `401` | unauthenticated redirect/session-required result |
| Temporary verification or signing-key-provider outage | generic `503` | bounded unavailable result |
| Verified external identity not provisioned | generic `401` | generic unauthenticated/session-required result |
| Missing or invalid selected-provider configuration | startup failure | startup/module initialization failure |
| Unsupported or unimplemented provider | startup failure | startup/module initialization failure |

Shared logs use names such as `auth_token_rejected` rather than
`clerk_token_rejected`. The selected provider may be recorded only as a bounded
label from the validated configuration set. Logs, metrics, responses, and
exceptions never expose bearer tokens, complete claims, external IDs, emails,
keys, secrets, provider payloads, or raw provider errors.

The existing Clerk distinction between credential rejection and JWKS/provider
unavailability is preserved inside the Clerk adapter.

## Migration Strategy

A new forward Alembic migration renames `users.clerk_user_id` to
`external_user_id`, including the active index and unique constraint names.
Historical migrations remain unchanged. The migration does not delete or
recreate users and does not require resetting the local database.

Application models, repositories, fixtures, seed scripts, Compose files,
active documentation, and tests move to provider-neutral terminology. A
source-containment check may allow historical migrations and historical design
records while rejecting new Clerk leakage in active shared code.

Since no production users or provider-linked Stripe state exist, phase one
does not implement dual-token acceptance, provider-ID mapping, password/session
migration, or legacy Stripe metadata reads.

## Test Strategy

Implementation is test-first. Every behavior change begins with a regression
or interface test that fails for the intended reason.

### Shared backend contract

- successful verification returns a normalized external identity;
- rejected credentials and temporary unavailability use the shared bounded
  failures;
- identity resolution returns only the internal UUID;
- absent users with trusted bootstrap profiles are provisioned generically;
- absent users without trusted profiles are rejected safely;
- owned provider resources close exactly once;
- tokens, external IDs, profile data, and provider errors are redacted.

### Clerk adapter

Existing tests continue covering static and JWKS verification, algorithms,
issuer, audience, temporal claims, authorized parties, key rotation, refresh
coalescing, bounded stale behavior, failures, observability, shutdown, and Svix
webhook verification. Assertions change only where the shared interface or
provider-neutral names require it.

### Local adapter

- constant-time token verification behavior is preserved;
- invalid tokens use the shared rejection type;
- verified local identities contain the fixed trusted bootstrap profile;
- first-request provisioning uses the shared provisioning module;
- local mode remains development-only and rejects blank configuration.

### Persistence and downstream ownership

- repository and provisioning tests use `external_user_id`;
- all foreign-key relationships remain on internal `users.id`;
- route tests prove handlers use only `AuthenticatedUser.internal_user_id`;
- Stripe tests prove metadata contains internal `user_id` and no external
  provider identifier;
- Stripe webhooks resolve ownership without provider metadata;
- realtime tests prove registration, channel names, event contracts, and fanout
  comparisons use the internal UUID.

### Frontend

- provider selection accepts Clerk and development local mode and rejects all
  unsupported values;
- Clerk session adaptation returns only the shared session shape;
- the backend client obtains and forwards an access token through the shared
  session module;
- Clerk route protection remains fail-closed;
- local route behavior remains development-only;
- shared sign-in, signup, sign-out, profile, and account-security wrappers
  delegate to Clerk leaf implementations;
- missing Clerk configuration cannot expose protected pages;
- provider secrets never enter public configuration.

### Containment and full verification

A source-containment test rejects Clerk SDK imports, `clerk_user_id`, and
Clerk-shaped shared types outside an explicit allowlist of Clerk adapters,
Clerk configuration, Clerk tests, historical migrations, and historical design
records.

Completion requires the full API and web lint, type, unit, integration,
coverage, Compose-rendering, local E2E, deployment-readiness, secret-safety, and
diff checks already used by the repository. A Clerk-mode smoke test must cover
protected routing, token forwarding, API identity resolution, and webhook
provisioning without exposing credentials.

## Future Provider Addition

A future Supabase phase adds:

- a Supabase backend token-verification adapter;
- a Supabase frontend session/cookie and route-protection adapter;
- Supabase sign-in, signup, sign-out, profile, and security leaf controls;
- a trusted Supabase provisioning input;
- `supabase` to the validated `AUTH_PROVIDER` set;
- provider-specific tests and configuration.

It does not alter the `users` relationship model, authenticated route types,
business modules, Stripe ownership, or realtime identifiers.

A future Opevo-owned auth system follows the same pattern. The seam isolates
application integration, but it does not reduce the inherent security and
operational work of passwords, recovery, verification, MFA, revocation, abuse
prevention, and signing-key lifecycle management.

## Acceptance Criteria

1. Phase one supports exactly `AUTH_PROVIDER=clerk` and development-only
   `AUTH_PROVIDER=local`; every other value fails closed.
2. Clerk sign-in, signup, route protection, bearer-token forwarding, FastAPI
   verification, JWKS behavior, and webhook provisioning retain their current
   security and availability behavior.
3. Local authentication remains deterministic, provider-free, and
   development-only.
4. Active persistence uses `users.external_user_id`; there is no per-user
   provider column or multi-identity table.
5. REST routes and realtime receive only an internal Opevo user UUID after
   authentication.
6. All foreign keys, Stripe ownership metadata, WebSocket keys, Redis channels,
   and realtime contracts use the internal UUID.
7. Shared frontend and backend code contains no Clerk SDK imports,
   Clerk-specific identity fields, Clerk claims, or Clerk payload parsing.
8. Clerk-specific behavior is local to Clerk adapters, configuration,
   webhooks, and tests.
9. Shared authentication failures and observability are provider-neutral,
   bounded, fail-closed, and value-redacted.
10. No Supabase dependency, placeholder adapter, account-linking mechanism, or
    speculative provider registry is introduced.
11. The complete repository verification suite and Clerk-mode smoke path pass.
12. Adding a future provider requires adapters, provider configuration, and
    tests, but no change to routes, business logic, persistence relationships,
    Stripe, or realtime.
