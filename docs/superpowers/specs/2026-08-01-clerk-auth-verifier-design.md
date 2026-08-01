# Application-Scoped Clerk Authentication Verifier Design

**Date:** 2026-08-01
**Status:** Approved interactively; awaiting written-spec review
**Issues:** 3A and 13A from `docs/engineering/2026-07-30-agent-api-review-decisions.md`

## Summary

The API will use one application-scoped asynchronous authentication provider for
all Clerk session-token verification. REST authentication and the disabled
realtime WebSocket path will share the same verifier, signing-key cache,
authorized-party policy, failure vocabulary, and observability.

Clerk mode will require an explicit exact-origin authorized-party allowlist.
The verifier will support either networkless verification with
`CLERK_JWT_KEY` or asynchronous JWKS retrieval with coalesced refreshes,
explicit deadlines, a five-minute fresh cache, and a ten-minute bounded grace
for previously known keys. Unknown keys never use stale data.

This work does not enable realtime. Issues 1A and 14A remain deferred. After
3A and 13A, the approved priority is Issues 4, 5, 6, 7, and 8.

## Context

The current `ClerkAuthProvider` verifies signature, issuer, optional audience,
and temporal claims, but it does not validate Clerk's `azp` authorized-party
claim. REST dependency resolution constructs a new provider per request, while
the realtime service constructs a separate provider. Remote signing-key lookup
uses synchronous `PyJWKClient` calls from async request paths.

Consequences:

- a Clerk token from an unapproved presenting origin can cross the API's
  intended frontend boundary;
- REST requests discard provider-local JWKS cache state;
- REST and WebSocket authentication policy can drift;
- a cold or rotated-key JWKS request can block the event loop;
- concurrent misses can amplify into repeated outbound refreshes;
- provider outages and invalid credentials do not have one explicit failure
  contract.

The first production phase is expected to use one or two API replicas. A
process-local cache therefore preserves almost all cache value: signing keys
are shared across users, so each active replica warms once rather than once per
user. A distributed cache would add more failure and coordination surface than
it removes at this scale.

## Goals

1. Require and validate exact authorized parties for every Clerk session token.
2. Use one verifier instance for REST and future WebSocket authentication.
3. Keep all JWKS I/O nonblocking and bounded.
4. Coalesce cold, expiry, and rotation refreshes.
5. Define exact fresh, stale-known-key, unknown-key, and outage behavior.
6. Distinguish rejected credentials from temporary authentication-provider
   unavailability.
7. Preserve token, claim, key, origin, and provider-error confidentiality.
8. Cover success, concurrency, rotation, timeout, cancellation, malformed
   input, and shutdown paths test-first.

## Non-goals

- Enabling realtime or changing its delivery contract.
- Implementing per-user Redis subscriptions or WebSocket backpressure.
- Adding a distributed JWKS cache, database cache, or cross-replica lock.
- Replacing Clerk or PyJWT with another authentication SDK.
- Implementing the broader process composition-root work approved as Issue 6.
- Changing local authentication semantics.
- Changing Clerk webhook signature semantics.
- Deriving the Clerk allowlist from CORS configuration.

## Selected approach and rejected alternatives

### Selected: explicit application-scoped asynchronous verifier

One deep authentication module owns claim policy and a small internal
signing-key resolver seam. Static-key and JWKS resolution are real adapters at
that seam. The JWKS adapter owns its HTTP client, cache state, refresh task,
deadlines, and defensive limits.

This costs more focused implementation and test code than wrapping the current
provider, but it makes the security and availability policy explicit and keeps
it local to one module.

### Rejected: thread-wrapped `PyJWKClient` with a coarse lock

This is smaller initially, but warm requests would share a coarse lock and the
approved stale, cancellation, refresh-rate, and error-mapping rules would stay
coupled to library internals. It is under-engineered for the accepted failure
contract.

### Rejected: static `CLERK_JWT_KEY` only

This removes request-time networking but makes every signing-key rotation an
operator deployment. Static-key mode remains supported and preferred when its
rotation is managed, but it is not the only supported production mode.

### Rejected: shared Redis JWKS cache

With one or two replicas, this saves at most one small refresh per cache
generation while adding authentication-time network I/O, serialization,
distributed locking, ownership, and Redis-outage semantics.

## Architecture

### External authentication seam

`AuthProvider` keeps one small interface:

```python
async def verify_token(token: str) -> UserIdentity
```

The interface includes these invariants:

- success returns only a verified external identity;
- credential rejection raises a typed non-retryable authentication failure;
- temporary key-provider unavailability raises a typed retryable failure;
- implementations never expose bearer tokens or complete claims;
- owned async resources are closed once during application shutdown.

`LocalAuthProvider` implements the same async interface without networking.
`ClerkAuthProvider` owns Clerk claim policy and one signing-key resolver.

### Internal signing-key seam

The internal resolver interface accepts the encoded token and returns a key
that is eligible for signature verification. It has two adapters:

1. **Static adapter:** returns the configured `CLERK_JWT_KEY`; it creates no
   HTTP client and does not require a `kid` header.
2. **JWKS adapter:** safely reads and bounds the unverified `kid`, resolves it
   through an application-scoped cache, and refreshes through an asynchronous
   HTTP client.

Callers and route handlers do not know which adapter is active.

Clerk mode requires exactly one verification source. Configuring neither or
both of `CLERK_JWT_KEY` and `CLERK_JWKS_URL` fails application construction
with a value-redacted error. There is no implicit precedence rule.

### Application ownership

The API lifecycle constructs exactly one provider from captured `Settings` and
stores it on application state. The REST dependency returns that exact
instance. If realtime is enabled in a later project, `RealtimeService` receives
the same instance; this project leaves every realtime flag false.

The lifecycle closes the provider, its HTTP client, and any owned refresh task
once. There is no module-global provider or cache.

FastAPI dependency overrides remain available for focused route and integration
tests. Direct fakes implement the same async interface.

## Authorized-party policy

### Configuration

`CLERK_AUTHORIZED_PARTIES` is a comma-separated list of canonical HTTP(S)
origins. It is required whenever `AUTH_MODE=clerk`, including test, staging,
preview, and production environments. `AUTH_MODE=local` does not require it.

Every Clerk-mode environment also requires a non-empty issuer and exactly one
verification source. `CLERK_JWKS_URL`, when selected, must be an absolute HTTPS
URL without credentials or a fragment. Tests inject the HTTP adapter rather
than weakening this runtime rule.

Each configured entry must:

- be non-empty and already trimmed;
- use `http` or `https`;
- contain a host and optional valid port;
- contain no path, query, fragment, username, password, wildcard, whitespace,
  backslash, or control character;
- use canonical lowercase scheme and host spelling;
- omit the default port and include any non-default port;
- be unique within the list.

Invalid configuration fails application construction without echoing the raw
value. IPv4, bracketed IPv6, localhost, and non-default ports are supported
when canonical.

`CORS_ALLOWED_ORIGINS` remains separate. CORS controls browser response access;
authorized parties bind accepted Clerk session tokens to known frontend
origins. Similar values do not make the settings interchangeable.

### Token validation

After cryptographic verification, the token must contain `azp` as a non-empty
string exactly equal to one configured canonical origin. The verifier does not
normalize a token-provided `azp`; case differences, trailing slashes, partial
domains, and misleading subdomains are rejected.

Missing, null, list-valued, numeric, malformed, or unapproved `azp` claims are
credential failures.

## JWT validation policy

The verifier fixes the allowed algorithm to `RS256`; it never derives the
allowed algorithm from token data. It validates:

- signature;
- configured issuer;
- `exp`, which is required and must be current;
- `nbf`, which is required and must not be in the future;
- `sub`, which is required and must be a non-empty string;
- configured audience when `CLERK_AUDIENCE` is non-empty;
- mandatory `azp` as specified above.

JWKS mode additionally requires a non-empty bounded string `kid`. Static-key
mode does not require `kid` because the trusted key is already selected by
configuration.

No unverified claim influences an outbound URL, allowed algorithm, issuer,
audience, or authorized-party list.

## JWKS client and defensive limits

JWKS mode uses one application-scoped `httpx.AsyncClient`, added as an explicit
runtime dependency. Its defaults are:

| Setting | Default | Purpose |
|---|---:|---|
| `CLERK_JWKS_CACHE_TTL_SECONDS` | `300` | Fresh key-set lifetime |
| `CLERK_JWKS_STALE_GRACE_SECONDS` | `600` | Additional known-key-only grace |
| `CLERK_JWKS_CONNECT_TIMEOUT_SECONDS` | `0.5` | DNS/TCP/TLS connection bound |
| `CLERK_JWKS_READ_TIMEOUT_SECONDS` | `1.0` | Response read bound |
| `CLERK_JWKS_POOL_TIMEOUT_SECONDS` | `0.25` | Client-pool acquisition bound |
| `CLERK_JWKS_TOTAL_TIMEOUT_SECONDS` | `2.0` | End-to-end refresh/wait bound |

All duration settings must be finite and positive. Cache TTL must be between 30
and 3,600 seconds; stale grace must be between 1 and 3,600 seconds; and each
network/total timeout must be between 0.05 and 10 seconds. Validation errors
identify only the setting name, never the rejected value.

Fixed implementation limits avoid unnecessary operator knobs:

- maximum encoded `kid` length: 128 characters;
- maximum JWKS response body: 256 KiB;
- maximum accepted signing keys per JWKS document: 16;
- minimum interval between completed refresh attempts: 5 seconds.

The endpoint comes only from captured application settings. JWKS requests do
not follow redirects; every redirect response is a provider failure. Any
non-success status, oversized body, invalid JSON, non-object JSON, empty
signing-key set, unsupported key material, duplicate `kid`, excessive key
count, and connection or timeout failure is also a provider failure. A failed
refresh never replaces a previously valid cache generation.

## Cache and refresh state machine

Each successful JWKS fetch atomically replaces the complete cached generation
and records monotonic `fetched_at`, `fresh_until`, and `stale_until` deadlines.
Removed keys do not survive a successful replacement.

### Fresh known key

If `now <= fresh_until` and `kid` is present, return it without networking.

### Cold cache

Create one shared refresh task. All callers await the same shielded task within
their total deadline. A cancelled or timed-out waiter cannot cancel the shared
refresh for other callers.

### Fresh unknown key

An unknown key can indicate rotation or attacker-controlled input. At most one
refresh runs. The five-second minimum refresh interval prevents repeated random
`kid` values from amplifying into unbounded outbound requests.

- if refresh is eligible, coalesce and attempt it once;
- if the most recent successful refresh is inside the minimum interval, reject
  the unknown key without another network call;
- if a successful refresh still lacks the key, reject the credential;
- if refresh fails, report provider unavailability because a legitimate
  rotation cannot be distinguished from outage.

An authentic rotation immediately after a completed refresh can therefore see
at most the five-second refresh-cooldown rejection window. This bounded
availability tradeoff protects Clerk and the API from untrusted-`kid` refresh
amplification.

### Expired generation with known key

Attempt one eligible refresh first. On success, use only the replacement
generation. On provider failure, the previously known key may be used only
while `now <= stale_until`. Every stale use emits bounded telemetry.

### Grace exhausted

If `now > stale_until`, a failed refresh cannot authorize any token. The
request fails as temporary provider unavailability.

### Failed refresh cooldown

A failed attempt records a bounded provider-unavailable state for the minimum
refresh interval. Concurrent and immediately following callers do not create a
retry storm. Known keys can use the approved grace; unknown keys and exhausted
known keys fail as unavailable until another refresh is eligible.

## Failure vocabulary and transport mapping

The authentication module exposes two safe failure families with bounded
reason codes:

| Failure family | Examples | REST | WebSocket |
|---|---|---:|---:|
| Credential rejected | malformed JWT, invalid signature, claims, issuer, audience, `azp`, or key absent after successful refresh | `401` with generic `Invalid token` | generic `invalid_token`, close `1008` |
| Provider unavailable | JWKS connection/read/total timeout, unavailable endpoint, malformed/oversized JWKS without a usable known key, exhausted stale grace | `503` with generic `Authentication temporarily unavailable` | generic `auth_unavailable`, close `1013` |

An unknown key during the successful-refresh cooldown is a credential
rejection. An unknown key whose eligible refresh fails is provider
unavailability.

The existing `User not synced` result remains `401`. Local-token mismatch
remains `401`. Configuration errors remain startup failures.

No response or log includes token data, claims, subject, authorized party,
`kid`, JWKS body, endpoint response body, configured secret/key, or raw provider
exception text.

## Observability

Use bounded reason codes only. Record:

- verification rejection by safe category;
- JWKS refresh attempt and outcome;
- coalesced refresh wait;
- stale-known-key use;
- refresh cooldown rejection;
- provider-unavailable result;
- refresh duration without endpoint or token attributes.

Logs use the existing safe-exception reporting pattern. Metrics must have a
fixed label vocabulary; they never label by user, origin, `kid`, URL, exception
message, or token value.

Stale-key use is a warning signal, not a successful-refresh substitute. It
must be visible enough for an operator to identify a sustained Clerk outage
before the grace expires.

## Data flow

### REST

1. The bearer dependency extracts a token.
2. `get_auth_provider()` returns the application-scoped provider.
3. The provider resolves a trusted signing key and validates the complete token
   policy asynchronously.
4. Existing user lookup maps the verified Clerk subject to an internal user.
5. The route receives only `AuthenticatedUserIdentity`.

### WebSocket

The disabled WebSocket authentication path calls the same provider and maps the
same typed failures to WebSocket-safe messages and close codes. This project
tests parity but does not include the WebSocket router or client in production
runtime configuration.

### Clerk webhook

Webhook requests continue to use Svix signature verification and the Clerk
webhook secret. Session-token `azp`, JWKS caching, and session-token failure
mapping do not alter webhook verification semantics.

## Deployment scope

Production Compose requires and passes `CLERK_AUTHORIZED_PARTIES` to the API
service only. It must not appear in worker, agent, web, migration, or local E2E
service environments.

The JWKS timeout/cache settings may use validated defaults. Environment examples
document their names and behavior without embedding a production origin.

Local Compose keeps `AUTH_MODE=local`; it does not need Clerk settings and keeps
`REALTIME_ENABLED`/`NEXT_PUBLIC_REALTIME_ENABLED` false. Test fixtures set an
explicit canonical test origin and issue tokens containing that `azp`.

No deployment is performed as part of this work. A future deployment must set
the real frontend origins explicitly before Clerk mode can start.

## Test strategy

Implementation is test-first. Every regression test must fail for the intended
reason before production code is added.

### Configuration and origin tests

- missing and empty allowlists in Clerk mode;
- local mode without an allowlist;
- one and multiple valid canonical origins;
- whitespace, empty elements, duplicates, invalid schemes, default/non-default
  ports, paths, queries, fragments, credentials, wildcards, backslashes,
  control characters, IPv4, bracketed IPv6, and misleading hostnames;
- finite/positive/bounded duration validation;
- validation-error redaction.

### Claim and transport-policy tests

- valid static-key and JWKS tokens;
- correct, missing, empty, null, numeric, list-valued, wrong, case-different,
  path-suffixed, prefix, suffix, and subdomain-confusion `azp`;
- missing/expired `exp`, missing/future `nbf`, missing/empty/non-string `sub`;
- wrong issuer and configured audience;
- malformed token/header, fixed-algorithm rejection, missing/malformed/oversized
  `kid` in JWKS mode;
- exact REST `401` versus `503` mapping;
- exact WebSocket `1008` versus `1013` mapping;
- REST/WebSocket provider-instance and policy parity;
- local authentication and Clerk webhook non-regression.

### Cache, concurrency, and outage tests

- fresh known-key requests perform no repeat fetch;
- many concurrent cold requests produce exactly one fetch;
- concurrent rotation/unknown-key requests produce one eligible refresh;
- refresh cooldown bounds repeated random-`kid` fetches;
- successful rotation atomically replaces keys;
- failed refresh preserves but does not extend the previous generation;
- stale known key succeeds inside grace and fails immediately after it;
- unknown key never uses stale data;
- a cancelled/timed-out waiter does not cancel the shared refresh;
- DNS/connect/read/pool/total timeout, HTTP failure, redirect policy, malformed
  JSON/JWKS, oversized response, excessive keys, duplicate `kid`, and empty key
  set;
- fake monotonic time covers exact TTL, grace, and cooldown boundaries;
- slow fetch does not stall an event-loop heartbeat;
- shutdown closes the client and refresh task exactly once.

### Redaction and observability tests

Use sentinels in token, subject, `azp`, `kid`, endpoint response, and exception
text. Assert none appear in logs, responses, metric labels, or exceptions.
Assert exact bounded reason codes and refresh/stale counters.

### Full verification

- API Ruff and authoritative `mypy app`;
- complete API pytest suite and line/branch coverage ratchets;
- complete shared and agent quality/test gates;
- complete local non-update E2E runner, proving local auth and disabled realtime;
- deployment-readiness tests and development/production Compose rendering;
- API runtime image build from monorepo root;
- dependency/lockfile audit, `git diff --check`, exact disposable cleanup, and an
  independent final review.

## Acceptance criteria

1. Every Clerk token requires an exact approved `azp`.
2. REST and WebSocket use the same application-scoped provider instance.
3. Static-key mode performs no JWKS I/O.
4. JWKS mode never blocks the event loop.
5. Concurrent refreshes are coalesced and refresh amplification is bounded.
6. Known keys have exactly five fresh minutes plus ten bounded stale minutes;
   unknown keys never use stale data.
7. Credential rejection and provider unavailability have distinct, safe
   transport behavior.
8. Authentication logs, metrics, responses, and errors reveal no sensitive or
   attacker-controlled values.
9. Local authentication, Clerk webhooks, and disabled realtime do not regress.
10. All focused and full verification gates pass without lowering coverage,
    skipping tests, relaxing assertions, or changing realtime flags.

## Follow-up order

After this specification and implementation are complete, the approved order
is:

1. Issue 4 — split critical and background worker queues with limits.
2. Issue 5 — split the outbox topic god module.
3. Issue 6 — introduce explicit process composition roots.
4. Issue 7 — centralize provider-failure vocabulary.
5. Issue 8 — stage the LiveKit upgrade and remove private SDK hooks.

Realtime correctness and scaling (Issues 1A and 14A) remain intentionally
deferred until the user chooses to resume them.
