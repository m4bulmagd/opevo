# Hermetic API Test Environment Design

Date: 2026-08-02
Status: Approved design
Decision: Review Issue 21A

## Context

The API `Settings` model loads `apps/api/.env` by default. That is correct for
manual local development, but it also lets ignored developer configuration
enter automated tests.

This caused two concrete failures after the Clerk verifier merge:

- pytest configured a generated static Clerk key and removed
  `CLERK_JWKS_URL` from the process environment, after which Pydantic reloaded
  a developer JWKS URL from `.env`; startup then saw both verification sources
  and rejected the configuration;
- the deployment-readiness tests rendered `compose.dev.yaml`, whose optional
  service `env_file` loaded the same local file and made production-only Clerk
  verifier settings appear in the local API environment.

The exact merge commit passed all tests in a tracked-only worktree, proving
that this is test-environment contamination rather than an authentication
runtime defect. Automated tests must produce the same result whether a
developer has no `.env`, a normal local `.env`, or a deliberately conflicting
`.env`.

## Goals

- Exclude dotenv values whenever settings are explicitly constructed in test
  mode.
- Establish construction-safe test settings before pytest imports test
  modules that may import the application.
- Render local Compose configuration in tests without resolving service
  `env_file` contents.
- Preserve normal `.env` loading for manual development and normal local
  Compose commands.
- Add behavior-level regressions for both contamination paths.
- Keep the policy centralized, explicit, and small.

## Non-goals

- Do not remove, rename, rewrite, inspect, or sanitize a developer's real
  `.env` file.
- Do not change production settings, Clerk verification behavior, realtime
  defaults, or deployment configuration.
- Do not add a general settings framework, a pytest wrapper requirement, or a
  maintained list of every optional setting that might appear in `.env`.
- Do not redesign the optional shared-PostgreSQL
  `CLIENT_TEST_DATABASE_URL` fixture.

## Chosen architecture

### 1. Settings sources are mode-aware

`Settings` will keep `.env` as its default dotenv source. Its official
Pydantic settings-source customization seam will omit only the dotenv source
when the effective application environment is `test`.

The effective environment is determined before dotenv is considered:

1. an explicit constructor value such as `Settings(app_env="test", ...)`;
2. otherwise the process environment's `APP_ENV` value;
3. otherwise no test-mode override applies and the normal dotenv source stays
   enabled.

Environment comparisons are trimmed and case-normalized. Constructor values
and process environment values retain their existing precedence. File-secret
and default sources retain their existing behavior. The implementation must
not detect pytest, inspect command names, or depend on the current working
directory as a proxy for test mode.

Consequences:

- `APP_ENV=test` is an explicit promise that test execution is hermetic;
- `APP_ENV=development` and the default local path continue loading `.env`;
- a direct `Settings(app_env="test", ...)` call is isolated even when the
  surrounding process is not already in test mode;
- a developer who intentionally places `APP_ENV=test` only inside `.env`
  still loads that file, because no higher-precedence source requested test
  isolation before dotenv evaluation.

### 2. Pytest establishes its environment before collection

The root API `tests/conftest.py` will define one construction-safe baseline
environment and install it in `pytest_configure`, before test modules are
collected. The baseline will include the same no-network Clerk construction
values used by CI plus required database, Redis, webhook, and dispatch
settings.

The existing function-scoped fixture remains responsible for generated Clerk
key material, cache resets, and per-test restoration. Baseline values and
per-test overrides will share named constants/helpers where doing so removes
real duplication. Tests may still override settings explicitly for individual
cases.

This is a test-process boundary, not application behavior. The pytest process
may replace inherited configuration; the user's shell and files remain
unchanged.

### 3. Compose test rendering does not resolve service env files

The deployment test helper will gain an explicit option controlling service
env-file resolution. Production rendering keeps the current default. Local
Compose assertions will call Docker Compose with `config --no-env-resolution`
so their resolved `environment` contains only values declared by the Compose
model and the controlled command environment, not values from optional local
service env files.

Normal `docker compose -f compose.dev.yaml ...` commands remain unchanged and
continue loading `apps/api/.env`, `apps/agent/.env`, and `apps/web/.env` as
declared by the development Compose file.

## Data flow

### Application settings in tests

1. pytest loads `tests/conftest.py`.
2. `pytest_configure` installs the controlled baseline and sets
   `APP_ENV=test`.
3. application code constructs `Settings` during collection or fixture setup.
4. the settings-source policy sees test mode before reading dotenv sources.
5. constructor values and process environment values are applied; `.env` is
   excluded.

### Local Compose assertions

1. a deployment test requests the local Compose environment.
2. the helper invokes real Docker Compose with env-file resolution disabled.
3. Docker Compose parses and interpolates the tracked Compose model but does
   not merge optional service env-file values.
4. assertions inspect the resulting explicit service environment.

## Error handling and edge cases

- Missing required test settings must fail normally through `Settings`; test
  mode must not invent silent application defaults.
- Empty, whitespace-only, and non-test `APP_ENV` values must not select test
  isolation. Case variants that normalize exactly to `test` must select it.
- Explicit `app_env="test"` must win over a conflicting development value in
  `.env`.
- An explicit non-test constructor value must preserve normal source ordering;
  higher-precedence explicit/process values still override dotenv values.
- The Compose helper must fail with the existing captured stderr when Docker
  Compose cannot parse or render a file.
- The local Compose test must not depend on whether the repository currently
  contains any ignored `.env` file.

## Test strategy

### Settings unit tests

Use a temporary working directory with a controlled poisoned `.env`; never
read or copy a developer file.

- Prove `Settings(app_env="test", ...)` ignores conflicting dotenv values.
- Prove process `APP_ENV=test` makes `get_settings()` ignore conflicting
  dotenv values.
- Prove development settings still load controlled dotenv values.
- Prove constructor/process precedence remains unchanged.
- Mutation check: restoring the dotenv source in test mode must make at least
  one poisoned-file regression fail.

### Pytest collection regression

Exercise application construction under the early controlled baseline and
prove it selects exactly one Clerk verification source without network I/O.
The test should assert application behavior or constructed provider type, not
grep fixture source text.

### Compose helper regression

Create a disposable Compose file and env file under `tmp_path`, render them
with the real Docker Compose command, and prove:

- normal env-file resolution includes a sentinel value;
- test-local rendering with env-file resolution disabled excludes it;
- tracked explicit environment values remain present.

Keep existing production and local Compose scope assertions.

### Full verification

- `uv lock --check`
- Ruff and mypy for the API
- focused red/green settings and deployment tests
- complete API suite with PostgreSQL integration tests and zero skips
- line and branch coverage ratchets
- development and production Compose rendering
- one full API run from `apps/api` while a disposable conflicting `.env` is
  present in the isolated worktree
- exact removal of the disposable `.env`, coverage reports, containers, and
  other test resources

## Acceptance criteria

1. A controlled poisoned `.env` cannot affect any `Settings` construction
   whose effective pre-dotenv application environment is `test`.
2. A controlled development `.env` still affects normal development settings.
3. pytest establishes construction-safe settings before test-module imports.
4. The complete API suite passes from its normal `apps/api` directory while a
   conflicting disposable `.env` exists.
5. Local Compose test rendering excludes service env-file values without
   changing the real development Compose file or command behavior.
6. Production Compose rendering remains unchanged.
7. No developer file, authentication behavior, realtime flag, deployment, or
   remote branch is modified.
8. All static, test, coverage, and cleanup gates pass with no new skips.

## Tradeoffs

The settings source policy adds a small amount of configuration logic to the
application settings class. That is preferable to a test wrapper or an
ever-growing fixture blacklist because it defines one explicit invariant:
test mode does not consume developer dotenv state.

`docker compose config --no-env-resolution` means local deployment tests do
not validate the contents of a developer's optional env files. That is
intentional: those files are unversioned user inputs. Separate example-file
and explicit-environment tests continue validating the repository's declared
configuration contract.
