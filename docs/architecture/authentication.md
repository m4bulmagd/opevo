# Authentication Architecture

Opevo selects exactly one authentication provider per deployment with
`AUTH_PROVIDER`. The supported values are `clerk`, `supabase`, and the explicit
development-only `local` provider. Provider selection is a startup decision;
the application does not link identities across providers or switch providers
for an individual request.

## Stable application contract

Provider SDKs and token formats terminate at adapter boundaries. The API
adapters validate a credential and return an `ExternalIdentity` containing the
provider subject plus optional trusted bootstrap profile data. The shared
authentication service resolves that subject through `users.external_user_id`,
provisions the local record when appropriate, and returns an
`AuthenticatedUser` containing only Opevo's internal UUID.

Every domain service, Stripe workflow, realtime connection, repository
ownership check, and durable event uses that internal UUID. Provider subjects
must not leak past identity resolution. This keeps provider identifiers out of
billing metadata and other long-lived integration contracts.

The web application follows the same rule. Provider-specific modules own SDK
imports, cookie handling, route protection, sign-in/up forms, account security,
and sign-out behavior. Shared pages consume only the neutral session and UI
controls. The browser never receives the local development token.

## Provider behavior

| Provider | API verification and provisioning | Web session |
| --- | --- | --- |
| Clerk | Verifies Clerk JWTs. A signed Clerk webhook may pre-provision or update the bootstrap profile; first authenticated use remains safe and idempotent. | Clerk owns browser sessions and hosted auth UI. |
| Supabase | Verifies the project issuer, audience, signature, and expiry from the project JWKS. The verified `sub` and email claims lazily provision the same local user model. | `@supabase/ssr` owns cookie refresh, password auth, recovery, callback exchange, and sign-out. |
| Local | Accepts one server-only fixed token and synthetic identity in development only. | Server-side requests use the fixed token; interactive hosted auth pages are unavailable. |

Authentication failures are mapped to bounded application errors and do not
expose provider exception text or credentials.

## Configuration

Set the same `AUTH_PROVIDER` value for the API and web service. Only the
selected provider's credentials are required:

- Clerk API: `CLERK_ISSUER`, `CLERK_AUTHORIZED_PARTIES`, exactly one of
  `CLERK_JWT_KEY` or `CLERK_JWKS_URL`, and `CLERK_WEBHOOK_SECRET` when the
  webhook route is enabled. Clerk web: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and
  `CLERK_SECRET_KEY`.
- Supabase API: `SUPABASE_URL` and optionally
  `SUPABASE_JWT_AUDIENCE` (default `authenticated`). The project must use an
  asymmetric JWT signing key exposed through its JWKS endpoint; legacy shared
  secret signing is intentionally unsupported. Supabase web:
  `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`.
- Local development: server-only `LOCAL_AUTH_TOKEN` in both processes.

Runtime validation fails closed when the selected provider is missing required
configuration. Unselected provider values can remain blank. Production Compose
therefore passes both provider-shaped variable sets without deciding which is
active; application startup validates the selected set.

The web image is provider-specific because Next.js embeds `NEXT_PUBLIC_*`
values during `next build`. Build `apps/web/Dockerfile` with the selected
`AUTH_PROVIDER` and its public provider values, then deploy that image with the
same runtime selection. Changing only the runtime environment cannot convert a
Clerk-built image into a Supabase-built image.

## Adding another provider

A new provider implements the API authenticator contract and the web provider
leaf modules, then adds an API factory branch plus explicit branches in the
neutral web capability wrappers it supports. It must return the same neutral
identity/session contracts, use the shared provisioning path, and keep SDK
imports inside its provider directory. It must not add provider subjects to
domain services or durable external metadata.

This seam also supports a self-hosted authentication implementation. Replacing
the provider is operationally simple before launch because there are no active
users to migrate. After users exist, changing providers requires an explicit
identity-mapping or account-linking design; that migration is intentionally not
part of the runtime abstraction.
