# Clerk-First Local Compose Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the standard local Compose stack use real Clerk authentication, fail closed on incomplete Clerk configuration, and retain synthetic local authentication only as an explicit deterministic-test opt-in.

**Architecture:** One web authentication configuration function resolves the selected mode and validates every mode-dependent requirement before any Clerk wrapper, proxy, page, or session code is exported. Compose defaults API and web to Clerk and supplies only non-secret local origin policy; its local token is blank unless an operator explicitly selects local mode. The disposable browser runner opts into local mode itself, while the interactive stack exercises the real Clerk-to-API identity boundary.

**Tech Stack:** Next.js 16 proxy, Clerk Next.js 6, React 19, TypeScript 5.9, Vitest 3, FastAPI runtime validation, Python 3.13, pytest 9, Docker Compose.

## Global Constraints

- Standard `compose.dev.yaml` defaults API and web to `AUTH_MODE=clerk`.
- A standard Compose render has no usable `LOCAL_AUTH_TOKEN`.
- Local auth remains development-only and requires both `AUTH_MODE=local` and a nonblank server-only `LOCAL_AUTH_TOKEN`.
- Clerk mode requires nonblank `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` in development and production.
- Production additionally requires a backend URL and continues rejecting local auth.
- The API accepts exactly `http://127.0.0.1:3000` and `http://localhost:3000` as default local Clerk authorized parties.
- `/activate(.*)` and `/dashboard(.*)` never pass through when Clerk mode is selected.
- The local token never appears in a `NEXT_PUBLIC_*` variable, build argument, log, exception, or committed real credential.
- Existing Clerk-linked user, phone, subscription, profile, and activation rows are preserved.
- Activation readiness remains enabled; no blocker is bypassed and no activation state is fabricated.
- Billing, telephony, voice-provider selection, realtime, and database cleanup remain outside this implementation plan.
- Every behavior change is test-first: run the named focused test and observe the expected failure before editing production code.

---

## File Responsibility Map

- `apps/web/src/lib/auth/auth-mode.ts`: resolve `clerk | local` and validate all mode-dependent web runtime requirements in one pure function.
- `apps/web/src/lib/auth/clerk-config.ts`: capture process environment once, call the pure validator, and export only valid derived mode flags.
- `apps/web/src/lib/auth/server-session.ts`: select explicit local identity or a real Clerk server session; it no longer models an unconfigured-Clerk runtime.
- `apps/web/src/proxy.ts`: use Clerk protection whenever Clerk is selected and local pass-through only when local is explicitly selected.
- `apps/web/src/components/auth/clerk-setup-notice.tsx`: remove this now-unreachable fail-open setup UI after the fail-closed validator is green.
- `apps/web/src/app/**`: remove branches that attempted to render protected dashboard or auth pages under an invalid Clerk configuration.
- `apps/web/tests/lib/auth-mode.test.ts`: pure mode/configuration boundary tests.
- `apps/web/tests/lib/clerk-config.test.ts`: environment-capture and module-initialization tests.
- `apps/web/tests/lib/proxy.test.ts`: protected-route versus explicit-local proxy behavior.
- `apps/web/tests/lib/server-session.test.ts`: real Clerk and explicit-local session selection.
- `apps/web/tests/app/auth-entry.test.tsx`: hosted Clerk entry and explicit-local redirect behavior after dead setup state removal.
- `compose.dev.yaml`: Clerk-first standard service environment and explicit local-token passthrough.
- `scripts/run-local-e2e.sh`: owns the explicit synthetic-local opt-in for disposable browser tests.
- `apps/api/tests/test_deployment_readiness.py`: rendered Compose contracts and executed runner-environment assertions.
- `apps/web/.env.example` and `apps/api/.env.example`: credential-free standard Clerk and explicit local opt-in examples.
- `README.md`, `docs/architecture/local-self-service-activation.md`, and `docs/architecture/staging-smoke-runbook.md`: standard interactive versus disposable-local run instructions.

---

### Task 1: Fail Closed for Incomplete Web Clerk Configuration

**Files:**

- Modify: `apps/web/tests/lib/auth-mode.test.ts`
- Modify: `apps/web/tests/lib/clerk-config.test.ts`
- Modify: `apps/web/tests/lib/proxy.test.ts`
- Modify: `apps/web/tests/lib/server-session.test.ts`
- Modify: `apps/web/tests/app/auth-entry.test.tsx`
- Modify: `apps/web/src/lib/auth/auth-mode.ts`
- Modify: `apps/web/src/lib/auth/clerk-config.ts`
- Modify: `apps/web/src/lib/auth/server-session.ts`
- Modify: `apps/web/src/proxy.ts`
- Delete: `apps/web/src/components/auth/clerk-setup-notice.tsx`
- Modify: `apps/web/src/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
- Modify: `apps/web/src/app/(auth)/sign-up/[[...sign-up]]/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/account/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/billing/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/[callId]/page.tsx`

**Interfaces:**

- Produces: `requireWebAuthConfiguration(input: WebAuthConfigurationInput): WebAuthMode`.
- Preserves: `resolveWebAuthMode(input): WebAuthMode`, `authMode`, `shouldWrapClerk`, and `selectFirstNonblank(...)` for current consumers.
- Removes: `isClerkConfigured`, `isAppAuthConfigured`, `requireProductionClerkConfig`, `shouldUseClerkMiddleware`, and `CLERK_REQUIRED_ENV_VARS` because a successfully imported configuration is now complete by construction.
- Error contract: `Missing required authentication settings: <safe setting names>`; values are never echoed.

- [ ] **Step 1: Write the failing pure configuration tests**

Replace the production-only validation cases in `auth-mode.test.ts` with behavior that names the actual break: development Clerk mode must not tolerate either missing key.

```ts
import {
  requireWebAuthConfiguration,
  resolveWebAuthMode,
} from "@/lib/auth/auth-mode";

const developmentClerkConfig = {
  nodeEnv: "development",
  authMode: "clerk",
  publishableKey: "pk_test_configured",
  secretKey: "clerk-test-fixture",
  backendBaseUrl: "http://api:8000",
};

it.each([
  ["publishable key", { publishableKey: " " }, "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"],
  ["secret key", { secretKey: "" }, "CLERK_SECRET_KEY"],
])("rejects a missing Clerk %s in development", (_label, override, missingName) => {
  expect(() =>
    requireWebAuthConfiguration({ ...developmentClerkConfig, ...override }),
  ).toThrow(missingName);
});

it("accepts explicit local development without Clerk keys", () => {
  expect(
    requireWebAuthConfiguration({
      nodeEnv: "development",
      authMode: "local",
      publishableKey: "",
      secretKey: "",
      backendBaseUrl: "http://api:8000",
    }),
  ).toBe("local");
});

it("requires the backend URL only in production", () => {
  expect(() =>
    requireWebAuthConfiguration({
      ...developmentClerkConfig,
      nodeEnv: "production",
      backendBaseUrl: "",
    }),
  ).toThrow("API_BASE_URL or NEXT_PUBLIC_API_BASE_URL");
});
```

Retain the unknown-mode, blank-default-to-Clerk, non-development-local rejection, complete production, and `NEXT_PUBLIC_LOCAL_AUTH_TOKEN` source-safety tests.

- [ ] **Step 2: Write the failing module-initialization tests**

Rewrite `clerk-config.test.ts` to dynamically import the real module only after stubbing each complete environment. Do not assert on Clerk framework internals.

```ts
async function importClerkConfig() {
  vi.resetModules();
  return import("@/lib/auth/clerk-config");
}

function stubConfiguredDevelopmentClerk() {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_MODE", "clerk");
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_configured");
  vi.stubEnv("CLERK_SECRET_KEY", "clerk-test-fixture");
  vi.stubEnv("API_BASE_URL", "http://api:8000");
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000");
}

it("fails module initialization for incomplete development Clerk mode", async () => {
  stubConfiguredDevelopmentClerk();
  vi.stubEnv("CLERK_SECRET_KEY", " ");

  await expect(importClerkConfig()).rejects.toThrow("CLERK_SECRET_KEY");
});

it("derives Clerk wrappers from a valid selected mode", async () => {
  stubConfiguredDevelopmentClerk();

  const config = await importClerkConfig();

  expect(config.authMode).toBe("clerk");
  expect(config.shouldWrapClerk).toBe(true);
});

it("permits wrapper pass-through only in explicit local development", async () => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_MODE", "local");
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
  vi.stubEnv("CLERK_SECRET_KEY", "");
  vi.stubEnv("API_BASE_URL", "http://api:8000");

  const config = await importClerkConfig();

  expect(config.authMode).toBe("local");
  expect(config.shouldWrapClerk).toBe(false);
});
```

Keep the production public-backend fallback test through `selectFirstNonblank`, and add a proxy-module import rejection assertion for incomplete development Clerk mode.

- [ ] **Step 3: Update session and auth-entry expectations before production edits**

In `server-session.test.ts`, replace the current “returns an unauthenticated session when development Clerk keys are absent” test with a module-import rejection:

```ts
it("cannot construct a server session module with incomplete Clerk configuration", async () => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_MODE", "clerk");
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
  vi.stubEnv("CLERK_SECRET_KEY", "");
  vi.stubEnv("API_BASE_URL", "http://api:8000");

  await expect(importServerSession()).rejects.toThrow(
    "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
  );
});
```

Retain authenticated Clerk, Clerk-without-session-token, explicit-local, and blank-local-token cases. In `auth-entry.test.tsx`, remove the unreachable `configured` mock state and keep only the real hosted entry and explicit-local redirect assertions.

- [ ] **Step 4: Run focused web tests and confirm the correct red signal**

Run:

```bash
npm --prefix apps/web run test:ci -- \
  tests/lib/auth-mode.test.ts \
  tests/lib/clerk-config.test.ts \
  tests/lib/proxy.test.ts \
  tests/lib/server-session.test.ts \
  tests/app/auth-entry.test.tsx
```

Expected: FAIL because development Clerk mode currently returns early, module initialization succeeds without Clerk keys, and the server-session module still models unconfigured Clerk as an ordinary unauthenticated state. If the new tests pass before production edits, stop and correct the test seam.

- [ ] **Step 5: Implement one pure fail-closed configuration boundary**

Replace `ProductionWebAuthInput` and `requireProductionWebAuth` in `auth-mode.ts` with:

```ts
type WebAuthConfigurationInput = WebAuthModeInput & {
  publishableKey?: string;
  secretKey?: string;
  backendBaseUrl?: string;
};

export function requireWebAuthConfiguration(
  input: WebAuthConfigurationInput,
): WebAuthMode {
  const mode = resolveWebAuthMode(input);
  const missing = [
    mode === "clerk" && isBlank(input.publishableKey)
      ? CLERK_PUBLISHABLE_KEY
      : undefined,
    mode === "clerk" && isBlank(input.secretKey)
      ? CLERK_SECRET_KEY
      : undefined,
    input.nodeEnv === "production" && isBlank(input.backendBaseUrl)
      ? BACKEND_BASE_URL
      : undefined,
  ].filter((setting): setting is string => Boolean(setting));

  if (missing.length > 0) {
    throw new Error(
      `Missing required authentication settings: ${missing.join(", ")}`,
    );
  }

  return mode;
}
```

The production-local rejection remains owned by `resolveWebAuthMode`, so the validator does not duplicate it.

- [ ] **Step 6: Simplify environment capture and session selection**

Reduce `clerk-config.ts` to one validation call and derived wrapper flag:

```ts
import { requireWebAuthConfiguration } from "@/lib/auth/auth-mode";

function isAbsent(value: string | undefined): boolean {
  return !value?.trim();
}

export function selectFirstNonblank(
  ...candidates: Array<string | undefined>
): string | undefined {
  return candidates.find((candidate) => !isAbsent(candidate));
}

const backendBaseUrl = selectFirstNonblank(
  process.env.API_BASE_URL,
  process.env.NEXT_PUBLIC_API_BASE_URL,
);

export const authMode = requireWebAuthConfiguration({
  nodeEnv: process.env.NODE_ENV,
  authMode: process.env.AUTH_MODE,
  publishableKey: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: process.env.CLERK_SECRET_KEY,
  backendBaseUrl,
});

export const shouldWrapClerk = authMode === "clerk";
```

In `server-session.ts`, remove the `isClerkConfigured` import and the unconfigured-Clerk return branch. The only branches become explicit local identity and Clerk's real `auth()` result. `requireServerSession()` retains its identity-plus-token check.

`proxy.ts` keeps the current route matcher and conditional export, but its condition is now selected mode rather than credential completeness. The proxy unit test must continue proving both protected paths call `auth.protect()` and explicit local mode calls `NextResponse.next()`.

- [ ] **Step 7: Remove unreachable setup-notice branches as the green refactor**

After the focused tests first pass, delete `clerk-setup-notice.tsx`, remove `isAppAuthConfigured` imports/checks from the listed auth and dashboard pages, and render their normal Clerk/authenticated content directly. Keep every existing `authMode === "local"` behavior that is still reachable, including the sign-in/sign-up redirect and local development account controls.

Run:

```bash
rg -n "ClerkSetupNotice|isAppAuthConfigured|isClerkConfigured|requireProductionClerkConfig|shouldUseClerkMiddleware" \
  apps/web/src apps/web/tests
```

Expected: no matches.

- [ ] **Step 8: Verify green and commit Task 1**

Run:

```bash
npm --prefix apps/web run test:ci -- \
  tests/lib/auth-mode.test.ts \
  tests/lib/clerk-config.test.ts \
  tests/lib/proxy.test.ts \
  tests/lib/server-session.test.ts \
  tests/app/auth-entry.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run check
```

Expected: all commands pass with no warnings or secret values.

Commit:

```bash
git add -- apps/web/src apps/web/tests
git commit -m "fix(web): fail closed on incomplete Clerk auth"
```

---

### Task 2: Make Standard Compose Clerk-First and Keep Browser CI Explicitly Local

**Files:**

- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `compose.dev.yaml`
- Modify: `scripts/run-local-e2e.sh`

**Interfaces:**

- Standard default: API and web resolve `AUTH_MODE=clerk`, a blank local token, and API local authorized parties.
- Explicit override: `AUTH_MODE=local LOCAL_AUTH_TOKEN=<nonblank>` reaches API and web only.
- Browser CI: `run-local-e2e.sh` owns that explicit override and reuses one token value for Compose and Playwright API requests.
- Worker remains free of Clerk session-verification settings and local identity credentials.

- [ ] **Step 1: Write rendered Compose tests for default and explicit modes**

Change the local Compose helper so ambient developer variables cannot influence its default render:

```py
LOCAL_COMPOSE_AUTH_DEFAULTS = {
    "AUTH_MODE": "",
    "LOCAL_AUTH_TOKEN": "",
    "CLERK_AUTHORIZED_PARTIES": "",
}


def load_local_compose_yaml(
    environment: dict[str, str] | None = None,
) -> dict:
    return render_compose(
        "compose.dev.yaml",
        LOCAL_COMPOSE_AUTH_DEFAULTS | (environment or {}),
        resolve_env_files=False,
    )
```

Replace `test_local_compose_keeps_local_auth_and_realtime_disabled` with:

```py
def test_local_compose_defaults_interactive_services_to_clerk() -> None:
    document = load_local_compose_yaml()
    api_environment = resolved_service_environment(document, "api")
    web_environment = resolved_service_environment(document, "web")
    worker_environment = resolved_service_environment(document, "worker")

    assert api_environment["AUTH_MODE"] == "clerk"
    assert web_environment["AUTH_MODE"] == "clerk"
    assert api_environment["LOCAL_AUTH_TOKEN"] == ""
    assert web_environment["LOCAL_AUTH_TOKEN"] == ""
    assert api_environment["CLERK_AUTHORIZED_PARTIES"] == (
        "http://127.0.0.1:3000,http://localhost:3000"
    )
    assert api_environment.get("REALTIME_ENABLED", "false") == "false"
    for setting in ("AUTH_MODE", "LOCAL_AUTH_TOKEN", "CLERK_AUTHORIZED_PARTIES"):
        assert setting not in worker_environment
```

Add a second behavior test:

```py
def test_local_compose_accepts_explicit_synthetic_auth_for_disposable_tests() -> None:
    document = load_local_compose_yaml(
        {
            "AUTH_MODE": "local",
            "LOCAL_AUTH_TOKEN": "disposable-local-token",
        }
    )

    for service in ("api", "web"):
        environment = resolved_service_environment(document, service)
        assert environment["AUTH_MODE"] == "local"
        assert environment["LOCAL_AUTH_TOKEN"] == "disposable-local-token"
```

Rename the source-oriented test to
`test_development_compose_scopes_clerk_identity_and_provider_modes` and assert
rendered service behavior instead of exact source lines. Preserve assertions
that fake provider modes and activation remain explicit and that no
`NEXT_PUBLIC_LOCAL_AUTH_TOKEN` exists.

- [ ] **Step 2: Add an executed runner assertion for explicit local auth**

Extend the fake `docker` in the signal/cleanup runner test to record bounded auth state, never the token value:

```sh
if [ "${AUTH_MODE:-}" = "local" ]; then
  auth_mode_state=local
else
  auth_mode_state=unexpected
fi
if [ -n "${LOCAL_AUTH_TOKEN:-}" ]; then
  local_token_state=configured
else
  local_token_state=missing
fi
printf '%s|%s|%s\n' "$auth_mode_state" "$local_token_state" "$*" >> "$PROBE_LOG"
```

Assert every captured Docker invocation starts with `local|configured|`. This test catches a runner that forgets either opt-in without asserting a credential value.

- [ ] **Step 3: Run the focused deployment tests and confirm red**

Run from `apps/api`:

```bash
.venv/bin/pytest \
  tests/test_deployment_readiness.py::test_local_compose_defaults_interactive_services_to_clerk \
  tests/test_deployment_readiness.py::test_local_compose_accepts_explicit_synthetic_auth_for_disposable_tests \
  tests/test_deployment_readiness.py::test_development_compose_scopes_clerk_identity_and_provider_modes \
  tests/test_deployment_readiness.py::test_local_e2e_runner_preserves_signal_exit_and_failure_logs \
  -q
```

Expected: FAIL because Compose currently resolves local auth with a usable default token and the runner does not explicitly export the selected mode/token for Compose.

- [ ] **Step 4: Implement Clerk-first Compose values**

In both API and web `environment` blocks, replace hard-coded local auth with:

```yaml
AUTH_MODE: "${AUTH_MODE:-clerk}"
LOCAL_AUTH_TOKEN: "${LOCAL_AUTH_TOKEN:-}"
```

Add to API only:

```yaml
CLERK_AUTHORIZED_PARTIES: "${CLERK_AUTHORIZED_PARTIES:-http://127.0.0.1:3000,http://localhost:3000}"
```

Do not add Clerk session settings or local credentials to worker, migrate, agent, or any `NEXT_PUBLIC_*` name. Leave activation enabled and provider modes unchanged in this task.

- [ ] **Step 5: Make disposable browser CI own the local opt-in without duplication**

Near the existing port/reference exports in `run-local-e2e.sh`, add:

```sh
export AUTH_MODE=local
export LOCAL_AUTH_TOKEN=presvo-local-development-token
```

Replace the duplicate token literal later in the script with:

```sh
export E2E_LOCAL_AUTH_TOKEN="$LOCAL_AUTH_TOKEN"
```

This keeps one synthetic credential source and still passes only the server-side token into Compose and the test-only API client.

- [ ] **Step 6: Verify green and commit Task 2**

Run from `apps/api`:

```bash
.venv/bin/pytest tests/test_deployment_readiness.py -q
```

Then run from the repository root and assert the rendered behavior without
printing the document or any resolved credential:

```bash
docker compose -f compose.dev.yaml config --no-env-resolution --format json \
  | apps/api/.venv/bin/python -c '
import json
import sys

document = json.load(sys.stdin)
api = document["services"]["api"]["environment"]
web = document["services"]["web"]["environment"]
worker = document["services"]["worker"]["environment"]
assert api["AUTH_MODE"] == "clerk"
assert web["AUTH_MODE"] == "clerk"
assert api["LOCAL_AUTH_TOKEN"] == ""
assert web["LOCAL_AUTH_TOKEN"] == ""
assert api["CLERK_AUTHORIZED_PARTIES"] == "http://127.0.0.1:3000,http://localhost:3000"
assert "AUTH_MODE" not in worker
assert "LOCAL_AUTH_TOKEN" not in worker
print("compose_auth_contract=pass")
'
```

Expected: `compose_auth_contract=pass` and no environment values.

Commit:

```bash
git add -- compose.dev.yaml scripts/run-local-e2e.sh apps/api/tests/test_deployment_readiness.py
git commit -m "fix(dev): make Compose authentication Clerk-first"
```

---

### Task 3: Align Examples and Runbooks with the New Explicit Boundary

**Files:**

- Modify: `apps/web/.env.example`
- Modify: `apps/api/.env.example`
- Modify: `README.md`
- Modify: `docs/architecture/local-self-service-activation.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`

**Interfaces:**

- Standard interactive instructions require Clerk configuration and use normal Compose defaults.
- Provider-free browser CI instructions use `scripts/run-local-e2e.sh`, which explicitly selects local auth.
- Manual synthetic-local instructions show both required opt-in variables on one command and label the token development-only.
- Documentation contains placeholders only, never real secrets or real account identifiers.

- [ ] **Step 1: Update environment examples**

In both `.env.example` files:

- describe Clerk as the standard interactive authentication mode;
- remove claims that Compose automatically supplies a usable local identity;
- keep the two commented explicit opt-in lines together:

```dotenv
# AUTH_MODE=local
# LOCAL_AUTH_TOKEN=replace-with-a-development-only-token
```

- retain the warning against `NEXT_PUBLIC_LOCAL_AUTH_TOKEN`;
- document the API local authorized-party example as:

```dotenv
CLERK_AUTHORIZED_PARTIES=http://127.0.0.1:3000,http://localhost:3000
```

Do not replace the safe placeholders for issuer, JWKS, webhook, or Clerk keys with developer values.

- [ ] **Step 2: Separate standard interactive and disposable provider-free commands**

Update `README.md` and `local-self-service-activation.md` so the standard command is described as Clerk-authenticated. Keep provider fakes distinct from identity: fake billing/telephony do not imply a synthetic user.

Document manual provider-free local auth as an explicit command:

```bash
AUTH_MODE=local \
LOCAL_AUTH_TOKEN=replace-with-a-development-only-token \
docker compose -f compose.dev.yaml up --build postgres redis minio minio-init migrate api worker web
```

Continue recommending `bash scripts/run-local-e2e.sh` for disposable CI-equivalent proof because that script owns isolation, credentials, ports, and cleanup.

- [ ] **Step 3: Correct the real-provider smoke prerequisites**

In `staging-smoke-runbook.md`, list all authentication inputs required by the standard local Clerk stack:

```dotenv
AUTH_MODE=clerk
CLERK_ISSUER=https://your-instance.clerk.accounts.dev
CLERK_AUTHORIZED_PARTIES=http://127.0.0.1:3000,http://localhost:3000
CLERK_JWKS_URL=https://your-instance.clerk.accounts.dev/.well-known/jwks.json
CLERK_WEBHOOK_SECRET=whsec_...
```

Keep web publishable/secret keys in `apps/web/.env`, API verifier/webhook values in `apps/api/.env`, and explain that Compose defaults `AUTH_MODE` but each application still validates its own required credentials.

- [ ] **Step 4: Review documentation consistency and commit Task 3**

Run:

```bash
rg -n "Compose supplies.*local|AUTH_MODE=local|LOCAL_AUTH_TOKEN=presvo-local-development-token" \
  README.md apps/web/.env.example apps/api/.env.example docs/architecture
```

Expected: any remaining local-mode references are explicitly labeled opt-in, test-only, or historical; there is no claim that the standard Compose stack authenticates a synthetic owner.

Run:

```bash
git diff --check
```

Commit:

```bash
git add -- \
  README.md \
  apps/web/.env.example \
  apps/api/.env.example \
  docs/architecture/local-self-service-activation.md \
  docs/architecture/staging-smoke-runbook.md
git commit -m "docs: explain Clerk-first local authentication"
```

---

### Task 4: Verify the Codebase and the Running Clerk Boundary

**Files:**

- No persistent file changes expected.
- Temporary diagnostic artifacts may be created only under `/tmp` and must be removed before completion.

**Interfaces:**

- Consumes: completed Tasks 1–3 and existing developer Clerk credentials without printing them.
- Produces: automated test evidence, container configuration booleans, a green cookie-free regression probe, and a handoff for the authenticated activation stage.

- [ ] **Step 1: Run complete web verification**

```bash
npm --prefix apps/web run test:ci
npm --prefix apps/web run typecheck
npm --prefix apps/web run check
npm --prefix apps/web run build
```

Expected: all pass without warnings, skipped tests, snapshot updates, or credential output.

- [ ] **Step 2: Run relevant API/configuration verification**

From `apps/api`:

```bash
.venv/bin/pytest tests/test_deployment_readiness.py tests/auth -q
.venv/bin/ruff check app tests
.venv/bin/mypy app
```

Expected: all pass. Do not weaken global thresholds or auth assertions to make the run green.

- [ ] **Step 3: Recreate only API and web under standard Clerk defaults**

From the repository root, preserve the already-running Postgres, Redis, MinIO, worker, agent, and ngrok processes. Recreate only API and web:

```bash
docker compose \
  -f compose.dev.yaml \
  -f /tmp/presvo-voice-e2e.override.yaml \
  up -d --no-deps --force-recreate api web
```

Wait for both services to become healthy. If either fails, inspect only bounded startup errors and boolean configuration state; never print environment values, tokens, or keys.

- [ ] **Step 4: Confirm running modes through boolean-only diagnostics**

For web, print only `authMode` and booleans for publishable/secret configuration. For API, print only `authMode` and booleans for issuer, exactly-one signing source, and authorized parties. Required result:

```text
web.authMode=clerk
web.clerkPublishableConfigured=true
web.clerkSecretConfigured=true
api.authMode=clerk
api.clerkIssuerConfigured=true
api.clerkSigningSourceCount=1
api.clerkAuthorizedPartiesConfigured=true
```

The local token values must not be printed. A blank token in standard mode is acceptable and expected.

- [ ] **Step 5: Re-run the original cookie-free regression probe**

Run:

```bash
bash -c 'presvo_auth_status=$(curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:3000/dashboard); if [ "$presvo_auth_status" = "200" ]; then echo "FAIL: unauthenticated dashboard returned 200"; exit 1; fi; echo "PASS: unauthenticated dashboard was denied ($presvo_auth_status)"'
```

Expected: `PASS` with a redirect or other authentication-denial status, never `200`. Repeat against `/activate` and confirm the same boundary.

- [ ] **Step 6: Perform the authenticated Clerk smoke with the owner**

Ask the owner to open a new private browser, visit `/dashboard`, and confirm Clerk sign-in appears. After the owner signs in with the account associated with phone `***99`:

- the dashboard or authoritative activation redirect loads;
- API requests carry a real Clerk token and return no `401`/`503`;
- a separate new private browser remains signed out;
- a masked read-only database check confirms the request resolves the existing Clerk-linked user rather than `local_presvo_user`;
- no profile, activation, subscription, phone, or user row is deleted or fabricated.

- [ ] **Step 7: Clean temporary diagnostics and record the next-stage boundary**

Remove only temporary files created for this diagnostic/verification stage after verifying their exact paths, including `/tmp/presvo_readiness_probe.py` and `/tmp/inspect_livekit_webhooks.py`. Keep `/tmp/presvo-voice-e2e.override.yaml` until the voice test is complete because the running worker still depends on it.

Record that provider modes must be audited before clicking provider-mutating activation controls. Switching fake billing/telephony to real providers and deleting synthetic account `***65` require separate exact-scope decisions; neither is silently performed by this plan.

No commit is expected for this operational task unless verification uncovers a code or documentation defect, in which case return to a new failing test before changing code.

---

### Task 5: Resume a Legacy Account Whose Number Was Completed Before Explicit Consent Existed

**Owner decision:** 23A. A completed provider side effect satisfies the number prerequisite without fabricating historical consent. This is a compatibility rule for authoritative completed state, not a bypass for unfinished provisioning.

**Files:**

- Modify: `apps/api/app/services/activation_policy.py`
- Modify: `apps/api/app/services/activation_snapshot_service.py`
- Modify: `apps/api/tests/activation/test_activation_policy.py`
- Modify: `apps/api/tests/activation/test_activation_snapshot_service.py`
- Modify: `apps/web/tests/app/activation-page.test.tsx`
- Modify: this implementation plan with final evidence only if implementation details differ from this approved boundary

**Interfaces and invariants:**

- `ActivationFacts` exposes one explicit `number_provisioned` boolean rather than a looser `phone_ready` interpretation.
- `ActivationSnapshotService` sets `number_provisioned=true` only when the provisioning row is `succeeded`, the assigned phone has a nonblank provider number identity, and `provisioning.phone_number_id` exactly matches `phone.id`.
- Missing `provisioning_consented_at` still blocks every incomplete, failed, unlinked, or non-provider-ready number path before a new order can begin.
- An already completed number advances to `forwarding_required` even when it predates explicit consent. `provisioning_consented_at`, the provisioning idempotency key, and activation events remain unchanged; the snapshot does not claim the `provisioning_consented` milestone.
- `number_provisioned` is the single policy fact used for both stage evaluation and the completed-number milestone. Do not duplicate the completion predicate in multiple policy branches.
- Forwarding verification, go-live approval, runtime readiness, provider modes, and authentication remain unchanged.
- No migration, manual row repair, provider call, outbox event, or fake activation transition is permitted.

- [ ] **Step 1: Add the permanent red policy regression**

Add a policy test whose exact facts are: confirmed profile, eligible subscription, no historical consent, succeeded provisioning, completed number, no forwarding verification, and no go-live approval. Assert `forwarding_required`, `configure_forwarding`, the forwarding blocker, `number_provisioned` present, and `provisioning_consented` absent.

Update the existing missing-consent precedence case so the number is explicitly incomplete; it must remain `provisioning_consent_required`.

Run:

```bash
apps/api/.venv/bin/python -m pytest \
  apps/api/tests/activation/test_activation_policy.py -q
```

Expected before production edits: FAIL because the completed legacy facts still return `provisioning_consent_required`.

- [ ] **Step 2: Add the red snapshot regression and linkage edge cases**

Using the real `ActivationSnapshotService` with repository fakes, cover:

1. succeeded provisioning linked to the exact provider-ready phone plus null consent advances to forwarding while the response keeps `provisioning_consented_at=null`;
2. a succeeded provisioning row linked to another phone does not count as completed;
3. a missing/blank provider number identity does not count as completed;
4. queued, running, failed, or absent provisioning never gains the compatibility path merely because a phone row exists.

The positive case must fail before production edits for the same stage mismatch as the live account.

- [ ] **Step 3: Add the web contract regression for the resulting snapshot**

Render the real activation page with a `forwarding_required` snapshot that includes `number_provisioned`, omits `provisioning_consented`, and has a null consent timestamp. Request `milestone=forwarding` and assert the forwarding heading/guidance renders rather than the number card. This locks the cross-application contract; no web production change is expected.

- [ ] **Step 4: Implement the minimal central policy change**

Derive the exact linked completion fact once in `ActivationSnapshotService`. In `ActivationPolicy`, require consent only while that fact is false, retain failed/in-progress handling, and reuse the same fact for the `number_provisioned` completed milestone. Do not infer or write consent.

- [ ] **Step 5: Verify focused and neighboring activation behavior**

Run:

```bash
apps/api/.venv/bin/python -m pytest apps/api/tests/activation -q
npm --prefix apps/web run test:ci -- \
  tests/app/activation-page.test.tsx \
  tests/app/number-milestone.test.tsx \
  tests/app/forwarding-milestone.test.tsx
apps/api/.venv/bin/ruff check apps/api/app apps/api/tests
apps/api/.venv/bin/mypy apps/api/app
npm --prefix apps/web run typecheck
```

Expected: all pass with no skip, warning introduced by this change, snapshot rewrite, or credential output.

- [ ] **Step 6: Commit, review, and resume the owner smoke**

Commit only the approved policy, snapshot, tests, and durable plan evidence:

```bash
git commit -m "fix(api): resume legacy completed activation"
```

After independent task review, recreate only API and web, reconfirm Clerk-mode health and cookie-free redirects, and ask the owner to refresh the existing activation page. The page must render forwarding without any database/provider mutation. Then continue the original forwarding-verification and voice smoke boundary.

---

## Completion Criteria

- Standard Compose renders API and web in Clerk mode with no usable default local token.
- Explicit local auth still powers the disposable browser runner and remains development-only.
- Incomplete development Clerk configuration fails closed before protected content renders.
- All focused and full web/API configuration tests pass without weakened assertions.
- Cookie-free `/dashboard` and `/activate` no longer return HTTP 200.
- The owner can sign in through Clerk and reach the existing activation state for `***99`.
- A legacy account with an exactly linked, succeeded, provider-ready number can reach forwarding without fabricated provisioning consent, while every incomplete number still requires explicit consent.
- No database deletion, readiness bypass, fake activation transition, realtime enablement, or credential exposure occurs.
- Provider-mode audit is explicitly handed off before the real activation and inbound-call stage.
