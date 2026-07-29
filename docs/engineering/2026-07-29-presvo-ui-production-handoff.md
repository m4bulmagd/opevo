# Presvo UI Production Handoff

**Status:** The complete Presvo interface is implemented, production-buildable,
and locally verified. It preserves the approved template's colors, typography,
spacing, borders, shadows, cards, responsive hierarchy, and light/dark themes.
France is the first market; the launch copy remains English.

This handoff describes the integration boundary. It is not evidence of cloud
deployment, real-provider certification, legal approval, load capacity, or
formal accessibility conformance.

## Product and data boundary

- `apps/web` is the only frontend. Browser code does not call providers
  directly.
- Authenticated pages load live data through typed API clients. Mutations cross
  server actions before reaching FastAPI.
- PostgreSQL remains authoritative for activation, account, assistant, call,
  usage, and lifecycle state.
- The database must be migrated through Alembic head
  `0017_assistant_overrides`. That revision stores customer-owned assistant
  content overrides while retaining the confirmed business profile as the
  source of truth for inherited values.
- Unsupported interactions use resettable client-local state and persistent
  visible `Preview` text. A Preview must not be presented as saved, purchased,
  connected, or provider-confirmed.
- The retained future component
  `apps/web/src/components/ui/sonic-waveform.tsx` is intentionally unused and
  excluded from production route manifests.

## Runtime configuration

Copy the package `.env.example` files and provide secrets through the deployment
platform. Do not commit resolved values.

### Web

| Setting | Production requirement |
| --- | --- |
| `NEXT_PUBLIC_APP_URL` | Canonical HTTPS web origin. |
| `NEXT_PUBLIC_API_BASE_URL` | Public HTTPS API origin. |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Production Clerk publishable key. |
| `CLERK_SECRET_KEY` | Server-only production Clerk key. |
| `NEXT_PUBLIC_REALTIME_ENABLED` | Keep `false`; the observer is not a supported launch feature. |

`AUTH_MODE=local`, `LOCAL_AUTH_TOKEN`, `BILLING_MODE=fake`, and
`TELEPHONY_MODE=fake` are provider-free local-development settings. They are
server-only and must never be copied into a `NEXT_PUBLIC_*` variable or used in
production.

### API

Production requires:

- `APP_ENV=production`, exact HTTPS `CORS_ALLOWED_ORIGINS`, PostgreSQL
  `DATABASE_URL`, and Redis `REDIS_URL`;
- `REALTIME_ENABLED=false`, a long random `AGENT_DISPATCH_JWT_SECRET`, and a TTL
  covering the maximum call plus dispatch/finalization grace;
- Clerk issuer, JWKS, and webhook credentials;
- Stripe secret, webhook, Starter price, Checkout URLs, Billing Portal return
  URL, and a reviewed `STRIPE_BILLING_PORTAL_CONFIGURATION_ID`;
- LiveKit URL, API key/secret, and the matching agent name;
- Telnyx API key, active and disabled connection IDs, and
  `TELNYX_ORDERING_ENABLED=true`;
- private object-store bucket, endpoint, credentials, and region;
- summary provider/model credentials and production telemetry destinations.

Set `BILLING_MODE=stripe`, `CARRIER_LOOKUP_MODE=telnyx`, and
`TELEPHONY_MODE=telnyx`. The API rejects fake provider modes in production.
The Stripe Portal configuration must allow cancellation only at period end and
must disable proration.

### Agent worker

The worker needs the matching LiveKit URL/key/secret/agent name, internal API
base URL, Redis URL, Speechmatics or Deepgram credentials, Gemini credentials,
and the configured TTS provider credentials. Keep `AGENT_DEBUG_STREAMS=false`;
review endpointing, Silero VAD, and turn-detector values in staging.

The time-bounded Transformers dependency exceptions are documented in
`docs/security/dependency-exceptions.md`. They expire on 2026-08-14 and must be
removed or explicitly re-reviewed before that date.

## Live route and backend map

| Interface | Live contract |
| --- | --- |
| `/`, `/sign-in`, `/sign-up` | Clerk authentication and protected-route handoff. |
| `/activate` | Activation read/resume, business profile save, carrier lookup/confirmation, billing, provisioning consent/retry, forwarding verification, test window, go-live, and restart. |
| `/dashboard` | Account, onboarding, metrics, recent calls, usage/billing, and assistant configuration reads. |
| `/dashboard/calls` | Server-owned search, filters, pagination, and `GET /api/calls`. |
| `/dashboard/calls/[callId]` | Call detail/recording reads and confirmed terminal-call removal through `GET`/`DELETE /api/calls/{call_id}`. |
| `/dashboard/agent` | `GET`/`PATCH /api/agent/config`; persisted identity, owner context, instructions, knowledge, and guarded routing state. |
| `/dashboard/billing` | Subscription, usage, ledger, hosted Checkout, and hosted Billing Portal actions. |
| `/dashboard/account` | Account identity/theme reads, confirmed deactivation, and hosted reactivation billing flow. |

The web server-action layer is the integration seam. Keep FastAPI wire types in
the API client and map them into page/view-model types before presentation.
Provider SDKs, local-auth tokens, and private API credentials must remain out of
client components.

Internal worker contracts remain documented in
`docs/architecture/integration-endpoints.md`. They include call-scoped agent
authorization, transcript/finalization operations, dispatch, recording
reconciliation, summary generation, provisioning, routing, and account
deactivation work.

## Local-only Preview map

| Surface | Module and current behavior |
| --- | --- |
| Authenticated header | `WorkspaceNotificationsPreview`: local notification drawer/read state shared by the workspace shell. |
| `/dashboard/live-call` | `LiveCallPreview`: complete local call-monitor simulation and controls. |
| `/dashboard/agent?tab=preview` | `AssistantPreview` and `TestAssistantPreview`: local voice/personality/test-session interactions. |
| `/dashboard/billing` | `PlanComparisonPreview`: non-Starter comparison and selection simulation; it cannot purchase or change a plan. |
| `/dashboard/account` | `AccountSettingsPreview`: notification preferences, retention, password, MFA, and other future settings. |

These modules have visible Preview labels, reset controls, no authenticated API
or server-action imports, and regression tests that reject network or mutation
behavior. When a backend capability is implemented:

1. define its typed FastAPI contract and authorization rules;
2. expose it through the authenticated web API client and server action;
3. replace the Preview component behind the existing presentation boundary;
4. add failure, pending, retry, and backend-confirmed success states;
5. remove the Preview label only after browser evidence proves real
   persistence or provider confirmation.

Do not change the Presvo design tokens or page hierarchy as part of backend
integration.

## Deployment order

1. Back up PostgreSQL and confirm restore ownership.
2. Run the release migration through `0017_assistant_overrides`.
3. Deploy worker/agent processes.
4. Deploy the API and require liveness/readiness success.
5. Deploy the web application.
6. Run authenticated activation, dashboard, call review, assistant save,
   billing, deactivation/reactivation, and failure-path smoke tests.

Before customer traffic, separately provision and verify Clerk and Stripe
webhooks, the pinned Stripe Portal configuration, Telnyx ordering/routing,
LiveKit agent/webhook ownership, private object storage, alert routing, backups,
French recording disclosures, privacy/legal/support surfaces, and real-provider
staging certification.

## Verification evidence

Fresh release-gate evidence on 2026-07-29:

- web: Biome passed on 204 files; TypeScript passed; Vitest passed 433 tests in
  45 files; the optimized Next.js 16.2.12 production build passed;
- production routes: `/`, `/activate`, `/dashboard`,
  `/dashboard/account`, `/dashboard/agent`, `/dashboard/billing`,
  `/dashboard/calls`, `/dashboard/calls/[callId]`,
  `/dashboard/live-call`, `/sign-in`, `/sign-up`, `/unauthorized`, and the
  not-found boundary;
- API: Ruff passed, mypy passed 168 files, and pytest passed 2,102 tests;
- migrations: a blank disposable database reached the single head
  `0017_assistant_overrides`;
- agent: Ruff passed, mypy passed 16 files, and pytest passed 250 tests with
  four credential-gated tests skipped;
- dependencies: `npm audit` and the API Python audit reported zero known
  vulnerabilities; the agent audit reported zero unignored findings and six
  governed rows for five exact exception IDs;
- browser lifecycle: pending the final immutable Docker run after this handoff
  is committed.

The browser matrix covers 1440 × 1100 desktop and 390 × 844 mobile in light and
dark themes, with route-specific interaction, keyboard, focus, reduced-motion,
overflow, and visual regression checks. A passing local matrix does not replace
formal accessibility, performance, real-provider, security, legal, or
operational certification.

