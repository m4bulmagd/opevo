# Clerk-First Local Compose Authentication Design

**Date:** 2026-08-03
**Status:** Approved interactively; awaiting written-spec review

## Summary

The standard local Docker Compose stack will use real Clerk authentication for
both the Next.js web application and the FastAPI API. It will no longer create
an authenticated synthetic session by default or inject a usable local bearer
token automatically.

The existing development-only local authentication adapter remains available
for deterministic provider-free tests, but using it requires an explicit
operator choice. Clerk configuration will fail closed in development as well as
production: selecting Clerk without all required web or API settings must stop
the affected application instead of leaving protected pages reachable.

This change does not disable activation readiness, fabricate activation state,
or replace any Clerk-linked database identity. The existing phone number ending
in `99` belongs to a Clerk-shaped identity and remains the account used for the
real activation and voice-call smoke test.

## Problem

`compose.dev.yaml` currently hard-codes `AUTH_MODE=local` and the shared local
development token for the web and API services. Compose `environment` values
take precedence over each service's `env_file`, so valid Clerk credentials in
the developer environment cannot select Clerk authentication.

In local mode, the server session adapter intentionally reports a fixed
synthetic user as authenticated, and the Next.js proxy intentionally passes
protected routes through without Clerk. Consequently, a cookie-free request to
`/dashboard` returns HTTP 200, matching the observed private-browser behavior.

There is a second fail-open configuration edge: in development Clerk mode, the
web application currently tolerates missing Clerk keys and declines to install
the Clerk middleware. A configuration typo could therefore recreate an
unprotected route boundary even after the Compose default changes.

## Goals

1. Make real Clerk authentication the standard local Compose behavior.
2. Ensure cookie-free access to `/dashboard` and `/activate` is denied or
   redirected to sign-in.
3. Fail application startup or module initialization when Clerk mode is selected
   without complete required configuration.
4. Keep local authentication available only through an explicit development
   opt-in.
5. Preserve the existing Clerk-linked account and activation data.
6. Lock the behavior down with unit, configuration, and live smoke checks.
7. Keep credentials, session tokens, and Clerk subjects out of logs and test
   output.

## Non-goals

- Removing the local authentication adapter used by deterministic tests.
- Disabling or weakening any activation-readiness prerequisite.
- Creating, editing, or deleting customer activation records as part of the
  authentication change.
- Switching billing, telephony, or voice providers in this change.
- Enabling realtime.
- Changing Clerk token-verification, JWKS-cache, or webhook semantics.

Provider-mode verification and the real activation/voice smoke test form the
next stage after authentication is proven. Any provider mutation will be
reviewed separately before it runs.

## Selected Approach

### Standard Compose mode

The web and API services use a shared operator-selectable Compose value whose
default is Clerk:

```yaml
AUTH_MODE: ${AUTH_MODE:-clerk}
```

The local token is passed only from an explicit operator-provided value and has
no usable default:

```yaml
LOCAL_AUTH_TOKEN: ${LOCAL_AUTH_TOKEN:-}
```

The API receives an explicit local authorized-party allowlist by default:

```yaml
CLERK_AUTHORIZED_PARTIES: ${CLERK_AUTHORIZED_PARTIES:-http://127.0.0.1:3000,http://localhost:3000}
```

Both local origins are intentional because the Compose stack publishes both in
its existing CORS configuration, and a Clerk session token's exact `azp` must
match the origin used in the browser. Production configuration remains separate
and retains its stricter deployment validation.

The standard Compose stack continues loading non-public Clerk credentials from
the existing service-specific environment files. Compose and documentation
must never contain real credential values.

### Explicit local-auth opt-in

Provider-free development may select local auth only by supplying both values
explicitly when starting the stack:

```bash
AUTH_MODE=local LOCAL_AUTH_TOKEN=<development-only-token> docker compose -f compose.dev.yaml up -d
```

Local mode remains restricted to `APP_ENV=development` and
`NODE_ENV=development`. Blank tokens fail closed. No `NEXT_PUBLIC_*` local token
is introduced.

### Web fail-closed behavior

Web authentication configuration will distinguish the selected mode from
credential completeness:

- Clerk mode requires a nonblank publishable key and secret key in every
  environment.
- Missing Clerk configuration raises a bounded error naming only the missing
  setting names.
- Local mode does not require Clerk keys, but remains development-only and
  requires the server-only local token before it can produce an authenticated
  session.
- The Clerk middleware protects `/activate(.*)` and `/dashboard(.*)` whenever
  Clerk mode is selected; there is no unconfigured-Clerk pass-through branch.

The existing API runtime validation already requires the Clerk issuer,
authorized parties, and exactly one signing-key source in Clerk mode. The
Compose configuration will satisfy those requirements rather than weakening
them.

## Authentication Data Flow

1. An unauthenticated browser requests `/dashboard` or `/activate`.
2. The Next.js Clerk middleware intercepts the request and redirects to Clerk
   sign-in; the protected page does not render.
3. After sign-in, Clerk creates the browser session and the web server obtains a
   session token through Clerk's server API.
4. The web backend client sends that token to FastAPI as a bearer token.
5. The application-scoped API Clerk verifier validates the signature, issuer,
   temporal claims, and exact local authorized party.
6. The verified Clerk subject resolves to the existing Opevo user. For the
   account under test, that user owns the phone number ending in `99` and its
   persisted activation records.
7. The activation UI reads and mutates only that authenticated user's canonical
   activation snapshot.

No browser-visible local bearer token or default database identity participates
in this flow.

## Database Treatment

No database deletion is required for the authentication correction:

- the number ending in `99` belongs to a Clerk-shaped identity and is retained;
- the number ending in `65` belongs to the synthetic local identity;
- switching the standard stack to Clerk makes the synthetic identity
  inaccessible to normal browser sessions;
- deleting the synthetic account is optional cleanup after the real activation
  and call test succeeds, and requires a separate exact-scope confirmation.

This ordering keeps cleanup recoverable and avoids deleting potentially useful
deterministic-test state before the real path is proven.

## Error Handling and Edge Cases

- Blank, whitespace-only, or unsupported `AUTH_MODE` values retain explicit
  validation.
- Clerk mode with one missing web key fails closed without printing credential
  values.
- Clerk mode with missing API authorized parties fails API construction.
- Using `localhost` in the browser accepts only the configured `localhost`
  authorized party; using `127.0.0.1` accepts only the configured IP origin.
- An unauthenticated request cannot fall through merely because Clerk
  configuration is incomplete.
- Local auth with a blank token cannot create a synthetic session.
- Production continues rejecting local auth regardless of token presence.
- Existing Clerk sessions and private-browser no-session behavior are tested
  separately to avoid mistaking a cached browser session for route protection.

## Testing Strategy

Implementation follows red-green-refactor.

1. **Compose contract test:** update the deployment-readiness assertions to
   require Clerk-first defaults, an empty-by-default local token, and the exact
   local authorized-party configuration for both supported browser origins.
2. **Web configuration unit tests:** prove development Clerk mode rejects each
   missing key, complete Clerk mode succeeds, explicit development local mode
   remains supported, and non-development local mode is rejected.
3. **Proxy unit tests:** prove `/dashboard` and `/activate` always invoke Clerk
   protection in configured Clerk mode and that only explicit local mode uses
   the pass-through adapter.
4. **Server-session unit tests:** retain coverage for Clerk authenticated,
   Clerk unauthenticated, missing session token, explicit local session, and
   blank local-token failure paths.
5. **Static secret-safety assertions:** preserve the rule that no local token is
   copied into a `NEXT_PUBLIC_*` variable and no real credential is committed.
6. **Live cookie-free regression probe:** after recreating web and API, request
   `/dashboard` without cookies and require a non-200 authentication response or
   redirect. The current known-red signal is:

   ```text
   FAIL: unauthenticated dashboard returned 200
   ```

7. **Authenticated browser smoke:** sign in with the existing Clerk account,
   confirm the API resolves its existing activation snapshot, and verify that a
   private browser remains signed out.

Relevant web and API test suites, type checking, formatting checks, and Compose
configuration validation must pass before the authentication stage is called
complete.

## Operational Sequence

1. Land and verify the Clerk-first configuration and fail-closed web behavior.
2. Recreate only the web and API containers using the approved configuration.
3. Confirm both containers report Clerk mode through boolean-only diagnostics.
4. Run the cookie-free dashboard probe and confirm it is no longer HTTP 200.
5. Have the owner sign in with the existing Clerk account and resume activation.
6. Inspect billing, telephony, LiveKit, worker, and agent modes before any
   provider-backed activation mutation.
7. Complete profile, forwarding verification, go-live, and one real inbound
   voice call without readiness bypasses or fabricated database state.
8. Review the synthetic local account for optional exact-scope deletion only
   after the real test succeeds.

## Tradeoffs

The main cost is that a developer now needs valid Clerk credentials to run the
standard interactive dashboard. This is intentional because the standard path
is meant to exercise production-like identity boundaries. Deterministic
provider-free tests retain the local adapter through explicit opt-in, so test
speed and offline workflows are preserved without making every private browser
an authenticated owner by default.

Failing closed in development is stricter than a convenience-oriented local
fallback, but it makes authentication configuration errors immediate and
observable. That matches the project's preference for explicit behavior,
thoughtful edge-case handling, and strong tests.
