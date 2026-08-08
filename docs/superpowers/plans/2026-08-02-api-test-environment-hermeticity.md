# Hermetic API Test Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API pytest and deployment-readiness assertions independent of an ignored developer `apps/api/.env` while preserving normal dotenv and development Compose behavior.

**Architecture:** `Settings` will use Pydantic's supported settings-source hook to omit only the dotenv source when constructor or process input already selects exact normalized test mode. Pytest will install one construction-safe environment in `pytest_configure`, before collection can import `app.main`, while the existing function fixture will reuse those constants and replace the construction-time JWKS source with generated static key material. Deployment tests will explicitly disable service `env_file` resolution only for local Compose assertions; normal and production rendering retain their existing defaults.

**Tech Stack:** Python 3.13, Pydantic Settings 2.14, pytest 9, FastAPI, Docker Compose, Ruff, mypy, pytest-cov, PostgreSQL 17.8, Redis 7.4.7.

## Global Constraints

- Implement approved review decision **21A** exactly as specified in `docs/superpowers/specs/2026-08-02-api-test-environment-hermeticity-design.md`.
- Keep `SettingsConfigDict(env_file=".env")`; manual local `Settings()` construction must continue loading dotenv unless constructor or process input already selects normalized exact `test` mode.
- Determine test mode only from constructor `app_env`, then process `APP_ENV`; do not inspect dotenv first, detect pytest, inspect command names, or infer from the current working directory.
- Preserve Pydantic source precedence: constructor, process environment, dotenv when enabled, file secrets, then model defaults.
- Establish pytest's construction-safe settings before test-module collection and keep function-scoped generated Clerk key material and cache cleanup.
- Keep normal local Compose commands and production Compose rendering unchanged; use `--no-env-resolution` only inside the local deployment-test helper.
- Keep realtime and activation flow disabled in the construction-safe pytest baseline. Do not enable or implement realtime.
- Do not alter Clerk runtime validation, verifier behavior, production configuration, Compose service declarations, dependencies, lockfiles, or deployment state.
- Never read, copy, rewrite, sanitize, or delete a developer's real `.env`. Controlled dotenv regressions use `tmp_path`; the full poisoned-dotenv proof may create only a pre-checked absent `apps/api/.env` inside this isolated worktree, then must delete that exact disposable file.
- Do not touch `/home/mo/code/ai/bmad-opevo/Opevo_frontend/` or `.worktrees/shadcn-activation-preview`.
- Use `UV_CACHE_DIR=/tmp/uv-cache` for every `uv` command. Do not push, deploy, open a PR, or modify remote branches.
- The full API suite must pass with zero skips against disposable PostgreSQL and Redis, and both line and branch coverage must meet or increase `apps/api/coverage-baseline.json`; never lower either ratchet.

## File Map

- Create `apps/api/tests/test_settings_sources.py`: focused source-selection, normalization, precedence, required-field, and poisoned-dotenv regressions.
- Create `apps/api/tests/test_collection_environment.py`: behavior-level proof that `pytest_configure` made collection-time `app.main` construction safe with exactly one non-network Clerk source.
- Modify `apps/api/app/core/config.py`: one small private test-mode selector and the official `settings_customise_sources` override.
- Modify `apps/api/tests/conftest.py`: named construction defaults, an early `pytest_configure` hook, and DRY reuse in the existing function-scoped settings fixture.
- Modify `apps/api/tests/test_deployment_readiness.py`: opt-in Compose env-file resolution control, a real disposable env-file regression, and isolated local rendering.
- Modify `apps/api/coverage-baseline.json` only if two full-suite reports prove a deterministic higher floor.
- Modify `docs/superpowers/specs/2026-08-02-api-test-environment-hermeticity-design.md` after every acceptance gate passes: mark the approved design implemented and record the verified boundaries without duplicating the issue into the existing visual-review number ledger.

---

### Task 1: Make Pydantic settings sources explicitly test-mode aware

**Files:**
- Create: `apps/api/tests/test_settings_sources.py`
- Modify: `apps/api/app/core/config.py:1-111`

**Interfaces:**
- Consumes: Pydantic's `PydanticBaseSettingsSource.__call__() -> dict[str, Any]` and existing `SettingsConfigDict(env_file=".env")`.
- Produces: `_requests_test_mode(init_settings, env_settings) -> bool` and `Settings.settings_customise_sources(...) -> tuple[PydanticBaseSettingsSource, ...]`.

- [x] **Step 1: Add poisoned-dotenv source-policy tests**

Create `apps/api/tests/test_settings_sources.py` with the following complete test module:

```python
from pathlib import Path

from pydantic import ValidationError
import pytest

from app.core.config import Settings, get_settings


POISONED_DOTENV = """\
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://poison:poison@127.0.0.1:1/poison
REDIS_URL=redis://127.0.0.1:1/0
OTEL_SERVICE_NAME=dotenv-poison-sentinel
ACTIVATION_FLOW_ENABLED=true
CLERK_JWT_KEY=dotenv-static-key
CLERK_JWKS_URL=https://poison.example.invalid/jwks.json
"""


def _write_dotenv(tmp_path: Path, content: str = POISONED_DOTENV) -> None:
    (tmp_path / ".env").write_text(content, encoding="utf-8")


@pytest.mark.parametrize("app_env", ["test", " TEST ", "TeSt"])
def test_explicit_test_environment_ignores_dotenv(
    app_env: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "development")

    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        clerk_jwt_key="constructor-static-key",
        clerk_jwks_url=None,
    )

    assert settings.database_url == "sqlite+aiosqlite://"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.otel_service_name == "opevo-api"
    assert settings.activation_flow_enabled is False
    assert settings.clerk_jwt_key == "constructor-static-key"
    assert settings.clerk_jwks_url is None


def test_process_test_environment_makes_cached_settings_ignore_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", " TeSt ")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CLERK_JWT_KEY", "process-static-key")
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.otel_service_name == "opevo-api"
    assert settings.activation_flow_enabled is False
    assert settings.clerk_jwt_key == "process-static-key"
    assert settings.clerk_jwks_url is None


def test_development_without_pre_dotenv_app_env_loads_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    for name in (
        "APP_ENV",
        "DATABASE_URL",
        "REDIS_URL",
        "OTEL_SERVICE_NAME",
        "ACTIVATION_FLOW_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.app_env == "development"
    assert settings.database_url == (
        "postgresql+asyncpg://poison:poison@127.0.0.1:1/poison"
    )
    assert settings.redis_url == "redis://127.0.0.1:1/0"
    assert settings.otel_service_name == "dotenv-poison-sentinel"
    assert settings.activation_flow_enabled is True


def test_app_env_selected_only_by_dotenv_does_not_preempt_that_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path, POISONED_DOTENV.replace("APP_ENV=development", "APP_ENV=test"))
    monkeypatch.chdir(tmp_path)
    for name in ("APP_ENV", "DATABASE_URL", "REDIS_URL", "OTEL_SERVICE_NAME"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.otel_service_name == "dotenv-poison-sentinel"


@pytest.mark.parametrize("app_env", ["", "   ", "development", "contest"])
def test_non_test_constructor_values_keep_dotenv_enabled(
    app_env: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)

    settings = Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.app_env == app_env
    assert settings.otel_service_name == "dotenv-poison-sentinel"


@pytest.mark.parametrize("app_env", ["", "   ", "development", "contest"])
def test_non_test_process_values_keep_dotenv_enabled(
    app_env: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", app_env)

    settings = Settings(
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
    )

    assert settings.app_env == app_env
    assert settings.otel_service_name == "dotenv-poison-sentinel"


def test_constructor_process_and_dotenv_precedence_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://process/db")
    monkeypatch.setenv("REDIS_URL", "redis://process:6379/0")

    settings = Settings(
        app_env="development",
        database_url="sqlite+aiosqlite://constructor",
    )

    assert settings.app_env == "development"
    assert settings.database_url == "sqlite+aiosqlite://constructor"
    assert settings.redis_url == "redis://process:6379/0"
    assert settings.otel_service_name == "dotenv-poison-sentinel"


def test_test_mode_retains_file_secret_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets_directory = tmp_path / "secrets"
    secrets_directory.mkdir()
    (secrets_directory / "database_url").write_text(
        "sqlite+aiosqlite://file-secret",
        encoding="utf-8",
    )
    (secrets_directory / "redis_url").write_text(
        "redis://file-secret:6379/0",
        encoding="utf-8",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    settings = Settings(app_env="test", _secrets_dir=secrets_directory)

    assert settings.database_url == "sqlite+aiosqlite://file-secret"
    assert settings.redis_url == "redis://file-secret:6379/0"


def test_test_mode_does_not_supply_missing_required_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(app_env="test")

    errors = {tuple(error["loc"]) for error in exc_info.value.errors()}
    assert ("database_url",) in errors
    assert ("redis_url",) in errors
```

The controlled file deliberately contains both Clerk sources and enabled feature flags. No test reads the repository's ignored `.env`.

- [x] **Step 2: Run the focused tests and prove the contaminated cases fail**

Run from `apps/api`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_settings_sources.py
```

Expected: the explicit and process test-mode cases fail because current `Settings` still reads `tmp_path/.env`; dotenv-only selection, development, non-test, precedence, file-secret, and missing-required-field cases pass. Confirm the failure shows dotenv sentinel values entering `Settings`, not a syntax, import, or fixture error.

- [x] **Step 3: Add the minimal source-selection helper**

In `apps/api/app/core/config.py`, import the source type and add this helper immediately above `Settings`:

```python
from pydantic_settings.sources import PydanticBaseSettingsSource


def _requests_test_mode(
    init_settings: PydanticBaseSettingsSource,
    env_settings: PydanticBaseSettingsSource,
) -> bool:
    init_values = init_settings()
    effective_app_env = (
        init_values["app_env"]
        if "app_env" in init_values
        else env_settings().get("app_env")
    )
    return (
        isinstance(effective_app_env, str)
        and effective_app_env.strip().casefold() == "test"
    )
```

Checking key presence, rather than truthiness, preserves constructor precedence even for an empty or invalid explicit value. Calling the configured Pydantic sources keeps environment-name handling aligned with Pydantic instead of duplicating alias/case rules with raw `os.environ` access.

- [x] **Step 4: Override only the dotenv position in `Settings`**

Add this classmethod immediately after `model_config`:

```python
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        if _requests_test_mode(init_settings, env_settings):
            return init_settings, env_settings, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings
```

Do not remove `model_config.env_file`. Do not add pytest/cwd detection or a second settings class.

- [x] **Step 5: Run focused tests and static checks**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_settings_sources.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app/core/config.py tests/test_settings_sources.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
```

Expected: every settings-source test passes; Ruff and mypy report no errors.

- [x] **Step 6: Perform the required mutation check**

Temporarily change the test-mode return to include `dotenv_settings`:

```python
        if _requests_test_mode(init_settings, env_settings):
            return init_settings, env_settings, dotenv_settings, file_secret_settings
```

Run the focused pytest command again. Expected: at least `test_explicit_test_environment_ignores_dotenv` and `test_process_test_environment_makes_cached_settings_ignore_dotenv` fail on the sentinel assertions. Immediately restore the three-source test-mode return with `apply_patch`, rerun the focused test file, and confirm `git diff` contains only the intended implementation and tests.

- [x] **Step 7: Commit the independently passing source policy**

```bash
git add apps/api/app/core/config.py apps/api/tests/test_settings_sources.py
git commit -m "fix(api): isolate test settings from dotenv"
```

---

### Task 2: Establish construction-safe pytest settings before collection

**Files:**
- Create: `apps/api/tests/test_collection_environment.py`
- Modify: `apps/api/tests/conftest.py:1-78`

**Interfaces:**
- Consumes: Task 1's effective pre-dotenv `APP_ENV=test` behavior.
- Produces: `_construction_settings_environment() -> dict[str, str]`, `pytest_configure(config: pytest.Config) -> None`, and a collection-time `app.main.app` configured with exactly one JWKS source and no initialized auth provider.

- [x] **Step 1: Add a collection-time application regression**

Create `apps/api/tests/test_collection_environment.py` exactly as follows:

```python
from app.core.clerk_verification_source import select_clerk_verification_source
from app.main import app as collection_app


def test_collection_time_application_uses_controlled_network_free_settings() -> None:
    settings = collection_app.state.settings
    verification_source = select_clerk_verification_source(
        jwt_key=settings.clerk_jwt_key,
        jwks_url=settings.clerk_jwks_url,
    )

    assert settings.app_env == "test"
    assert settings.realtime_enabled is False
    assert settings.activation_flow_enabled is False
    assert settings.clerk_jwt_key is None
    assert verification_source is not None
    assert verification_source.kind == "jwks"
    assert verification_source.value == (
        "https://clerk.example.com/.well-known/jwks.json"
    )
    assert collection_app.state.auth_provider is None
```

The module-level import is intentional: it executes during collection, before function fixtures. `auth_provider is None` proves application construction did not build a JWKS resolver or make a network request.

- [x] **Step 2: Prove the existing fixture is too late**

Run from `apps/api` with deliberately ambiguous inherited Clerk configuration:

```bash
APP_ENV=development \
DATABASE_URL=sqlite+aiosqlite:// \
REDIS_URL=redis://localhost:6379/0 \
CLERK_ISSUER=https://clerk.example.com \
CLERK_AUTHORIZED_PARTIES=https://app.example.com \
CLERK_JWT_KEY=collection-poison-static-key \
CLERK_JWKS_URL=https://clerk.example.com/.well-known/jwks.json \
AGENT_DISPATCH_JWT_SECRET=shared-test-dispatch-secret-with-at-least-32-bytes \
UV_CACHE_DIR=/tmp/uv-cache \
uv run --frozen --no-sync python -m pytest -q tests/test_collection_environment.py
```

Expected: collection fails before running the test because current `conftest.py` has no `pytest_configure` hook and `app.main` sees both Clerk verification sources.

- [x] **Step 3: Centralize construction-safe constants and environment creation**

In `apps/api/tests/conftest.py`, replace the single `TEST_CLERK_AUTHORIZED_PARTY` declaration with the following explicit constants and helper:

```python
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_call_test"
)
DEFAULT_TEST_REDIS_URL = "redis://localhost:6379/0"
TEST_CLERK_AUTHORIZED_PARTY = "https://app.example.com"
TEST_CLERK_ISSUER = "https://clerk.example.com"
TEST_CLERK_JWKS_URL = "https://clerk.example.com/.well-known/jwks.json"
TEST_CLERK_WEBHOOK_SECRET_BYTES = b"test-webhook-secret"
TEST_CLERK_WEBHOOK_SECRET = "whsec_" + base64.b64encode(
    TEST_CLERK_WEBHOOK_SECRET_BYTES
).decode("utf-8")
TEST_DISPATCH_JWT_SECRET = "shared-test-dispatch-secret-with-at-least-32-bytes"


def _construction_settings_environment() -> dict[str, str]:
    return {
        "APP_ENV": "test",
        "DATABASE_URL": os.environ.get(
            "TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL
        ),
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL),
        "REALTIME_ENABLED": "false",
        "ACTIVATION_FLOW_ENABLED": "false",
        "AUTH_MODE": "clerk",
        "CLERK_ISSUER": TEST_CLERK_ISSUER,
        "CLERK_AUDIENCE": "",
        "CLERK_AUTHORIZED_PARTIES": TEST_CLERK_AUTHORIZED_PARTY,
        "CLERK_JWKS_URL": TEST_CLERK_JWKS_URL,
        "CLERK_WEBHOOK_SECRET": TEST_CLERK_WEBHOOK_SECRET,
        "STRIPE_WEBHOOK_SECRET": "test-stripe-secret",
        "AGENT_DISPATCH_JWT_SECRET": TEST_DISPATCH_JWT_SECRET,
    }
```

Using `TEST_DATABASE_URL` and `TEST_REDIS_URL` when present keeps the same explicit CI/disposable-service contract; their localhost defaults preserve current focused-test behavior.

- [x] **Step 4: Install the controlled environment before collection**

Add this hook immediately after `_construction_settings_environment`:

```python
def pytest_configure(config: pytest.Config) -> None:
    os.environ.update(_construction_settings_environment())
    os.environ.pop("CLERK_JWT_KEY", None)
```

The hook replaces process settings only inside the pytest process. It does not write files or mutate the parent shell.

- [x] **Step 5: Reuse the constants in generated Clerk material and the function fixture**

In `clerk_key_material`, replace the local webhook byte/string construction with:

```python
    webhook_secret_bytes = TEST_CLERK_WEBHOOK_SECRET_BYTES
    webhook_secret = TEST_CLERK_WEBHOOK_SECRET
```

Replace the environment-setting prefix of `settings_env` with:

```python
    for name, value in _construction_settings_environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLERK_JWT_KEY", str(clerk_key_material["public_key_pem"]))
    monkeypatch.setenv("CLERK_JWKS_URL", "")
```

Keep the existing imports, limiter-state restoration, and four cache clears before and after `yield` unchanged. The function fixture deliberately swaps the construction-time JWKS source for a generated static public key. Its explicit empty process JWKS value shadows any local dotenv while source selection treats that value as absent, so it never leaves both sources configured.

- [x] **Step 6: Run the poisoned collection command again**

Repeat the exact command from Step 2.

Expected: one test passes. The hook overwrites `APP_ENV`, database/Redis inputs, disabled flags, and Clerk construction settings, removes the inherited static key, and the collection-time application selects only the JWKS source without initializing an auth provider.

- [x] **Step 7: Run the source, collection, and existing Clerk/deployment fixtures together**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_settings_sources.py \
  tests/test_collection_environment.py \
  tests/auth/test_clerk_auth_config.py \
  tests/test_deployment_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
```

Expected: all focused tests pass with zero skips, including current Clerk runtime-validation assertions; Ruff and mypy report no errors.

- [x] **Step 8: Commit the independently passing early test boundary**

```bash
git add apps/api/tests/conftest.py apps/api/tests/test_collection_environment.py
git commit -m "test(api): configure settings before collection"
```

---

### Task 3: Isolate local Compose assertions from service env files

**Files:**
- Modify: `apps/api/tests/test_deployment_readiness.py:1-99`
- Test: `apps/api/tests/test_deployment_readiness.py`

**Interfaces:**
- Consumes: Docker Compose `config --no-env-resolution` and existing `resolved_service_environment(document, service)`.
- Produces: `render_compose(compose_file, environment, *, resolve_env_files=True, working_directory=REPO_ROOT) -> dict`; production callers retain the default and local assertions opt out explicitly.

- [x] **Step 1: Add a real disposable Compose env-file regression**

Add this test immediately after `local_compose_service_environment`:

```python
def test_compose_render_can_skip_service_env_file_resolution(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    service_env_file = tmp_path / "service.env"
    compose_file.write_text(
        """\
services:
  api:
    image: example.invalid/opevo/api:test
    env_file:
      - ./service.env
    environment:
      EXPLICIT_SENTINEL: from-compose-model
""",
        encoding="utf-8",
    )
    service_env_file.write_text(
        "ENV_FILE_SENTINEL=from-service-env-file\n",
        encoding="utf-8",
    )

    resolved_document = render_compose(
        compose_file,
        {},
        working_directory=tmp_path,
    )
    isolated_document = render_compose(
        compose_file,
        {},
        resolve_env_files=False,
        working_directory=tmp_path,
    )
    resolved_environment = resolved_service_environment(resolved_document, "api")
    isolated_environment = resolved_service_environment(isolated_document, "api")

    assert resolved_environment["ENV_FILE_SENTINEL"] == "from-service-env-file"
    assert "ENV_FILE_SENTINEL" not in isolated_environment
    assert isolated_environment["EXPLICIT_SENTINEL"] == "from-compose-model"
```

This test proves both sides of the contract with Docker Compose itself. It does not inspect or depend on repository `.env` files.

- [x] **Step 2: Run the new test and prove the helper lacks the explicit control**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_deployment_readiness.py::test_compose_render_can_skip_service_env_file_resolution
```

Expected: fail with `TypeError` because `render_compose` does not yet accept `working_directory` or `resolve_env_files`.

- [x] **Step 3: Add explicit helper parameters without changing defaults**

Replace `render_compose` with:

```python
def render_compose(
    compose_file: str | Path,
    environment: dict[str, str],
    *,
    resolve_env_files: bool = True,
    working_directory: Path = REPO_ROOT,
) -> dict:
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "config",
        "--format",
        "json",
    ]
    if not resolve_env_files:
        command.append("--no-env-resolution")
    result = subprocess.run(
        command,
        cwd=working_directory,
        capture_output=True,
        check=False,
        env={**os.environ, **environment},
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
```

The default `True` is essential: `load_compose_yaml()` and every production caller remain byte-for-byte equivalent at the command boundary.

- [x] **Step 4: Opt out only in the local test helper**

Change `local_compose_service_environment` to:

```python
def local_compose_service_environment(service: str) -> dict[str, str]:
    document = render_compose(
        "compose.dev.yaml",
        {},
        resolve_env_files=False,
    )
    return resolved_service_environment(document, service)
```

Do not edit `compose.dev.yaml`. Manual `docker compose -f compose.dev.yaml ...` commands will continue resolving its optional service env files.

- [x] **Step 5: Run the new regression and local/production scope assertions**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/test_deployment_readiness.py::test_compose_render_can_skip_service_env_file_resolution \
  tests/test_deployment_readiness.py::test_production_compose_scopes_clerk_session_verifier_settings_to_api \
  tests/test_deployment_readiness.py::test_production_compose_renders_exactly_one_nonempty_clerk_key_source \
  tests/test_deployment_readiness.py::test_local_compose_keeps_local_auth_and_realtime_disabled \
  tests/test_deployment_readiness.py::test_development_services_load_local_env_files_without_leaking_api_identity_to_worker
```

Expected: five tests pass. Normal disposable env-file resolution includes its sentinel, isolated rendering excludes only that sentinel, production still renders exactly one JWKS source, and local tracked settings remain local-auth/realtime-off.

- [x] **Step 6: Run the full deployment-readiness file and static checks**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_deployment_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check tests/test_deployment_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
```

Expected: the full deployment-readiness file passes with zero skips; Ruff and mypy report no errors.

- [x] **Step 7: Commit the independently passing Compose-test boundary**

```bash
git add apps/api/tests/test_deployment_readiness.py
git commit -m "test(api): isolate local compose env rendering"
```

---

### Task 4: Prove clean and poisoned full-suite behavior, ratchet coverage, and close the design

**Files:**
- Modify conditionally: `apps/api/coverage-baseline.json`
- Modify: `docs/superpowers/specs/2026-08-02-api-test-environment-hermeticity-design.md`
- Disposable and always removed: `apps/api/.env`, `apps/api/.coverage`, `apps/api/coverage.json`, `/tmp/opevo-api-hermeticity-coverage-clean.json`

**Interfaces:**
- Consumes: Tasks 1-3 and the repository's coverage-ratchet checker.
- Produces: two equivalent full API reports (one clean, one poisoned), no skipped tests, no disposable resources, and an implemented design record.

- [x] **Step 1: Verify repository and isolated-worktree preconditions**

From the worktree root, run:

```bash
git status --short --branch
git diff --check
test ! -e apps/api/.env
test ! -e /home/mo/code/ai/bmad-opevo/.worktrees/api-test-env-hermeticity/Opevo_frontend
```

Expected: only the committed implementation plan and three committed implementation tasks are ahead of the design commit, no uncommitted changes exist, the isolated worktree has no API `.env`, and it has no `Opevo_frontend` path. If `apps/api/.env` exists, stop without reading or deleting it.

- [x] **Step 2: Run lock and static-analysis gates**

From `apps/api`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
```

Expected: lockfile unchanged and valid; Ruff and mypy report no errors.

- [x] **Step 3: Start only named disposable PostgreSQL and Redis services**

First prove neither exact name already exists:

```bash
test -z "$(docker ps -a --filter name=^/opevo-api-hermeticity-postgres$ --format '{{.Names}}')"
test -z "$(docker ps -a --filter name=^/opevo-api-hermeticity-redis$ --format '{{.Names}}')"
```

Then start:

```bash
docker run -d \
  --name opevo-api-hermeticity-postgres \
  -e POSTGRES_DB=ai_call_test \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:55459:5432 \
  postgres:17.8-bookworm
docker run -d \
  --name opevo-api-hermeticity-redis \
  -p 127.0.0.1:56389:6379 \
  redis:7.4.7-alpine
```

Poll `docker exec opevo-api-hermeticity-postgres pg_isready -U postgres -d ai_call_test` and `docker exec opevo-api-hermeticity-redis redis-cli ping` in bounded one-second loops for at most 60 attempts. Expected: PostgreSQL reports accepting connections and Redis reports `PONG`. If either fails, capture `docker logs` for that exact container and proceed directly to Step 9 cleanup.

- [x] **Step 4: Run the clean full API suite with coverage**

From `apps/api`, with `CLIENT_TEST_DATABASE_URL` explicitly absent:

```bash
env -u CLIENT_TEST_DATABASE_URL \
  APP_ENV=test \
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55459/ai_call_test \
  TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55459/ai_call_test \
  REDIS_URL=redis://127.0.0.1:56389/0 \
  TEST_REDIS_URL=redis://127.0.0.1:56389/0 \
  CLERK_ISSUER=https://clerk.example.com \
  CLERK_AUTHORIZED_PARTIES=https://app.example.com \
  CLERK_JWKS_URL=https://clerk.example.com/.well-known/jwks.json \
  UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
cp coverage.json /tmp/opevo-api-hermeticity-coverage-clean.json
```

Expected: the complete API suite passes with zero skips; both line and branch checks pass. The general client fixture remains on its approved per-test SQLite path because `CLIENT_TEST_DATABASE_URL` is absent, while dedicated integration tests use PostgreSQL through `TEST_DATABASE_URL`.

- [x] **Step 5: Create only the approved disposable conflicting `.env`**

From the worktree root, re-run `test ! -e apps/api/.env`, then use `apply_patch` to add exactly:

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://poison:poison@127.0.0.1:1/poison
REDIS_URL=redis://127.0.0.1:1/0
REALTIME_ENABLED=true
ACTIVATION_FLOW_ENABLED=true
AUTH_MODE=clerk
CLERK_ISSUER=https://poison.example.invalid
CLERK_AUTHORIZED_PARTIES=https://poison.example.invalid
CLERK_JWT_KEY=poison-static-key
CLERK_JWKS_URL=https://poison.example.invalid/jwks.json
```

Do not use or inspect any `.env` outside this isolated worktree. Confirm only existence and ignored status with:

```bash
test -e apps/api/.env
git check-ignore apps/api/.env
```

- [x] **Step 6: Run the identical full suite from `apps/api` while poison exists**

Repeat the exact pytest and coverage-check commands from Step 4. Expected: the complete API suite again passes with zero skips and identical coverage totals. Then compare only report totals:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -c \
  'import json; from pathlib import Path; clean=json.loads(Path("/tmp/opevo-api-hermeticity-coverage-clean.json").read_text())["totals"]; poisoned=json.loads(Path("coverage.json").read_text())["totals"]; assert clean == poisoned, (clean, poisoned); print(clean)'
```

Expected: the assertion passes. This is the authoritative regression proving a conflicting local dotenv cannot alter collection, settings fixtures, deployment-readiness rendering, skips, or coverage.

- [x] **Step 7: Ratchet coverage only when both reports justify it**

Print the current measured percentages through the repository parser:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -c \
  'import sys; from decimal import Decimal, ROUND_DOWN; sys.path.insert(0, "../.."); from scripts.check_python_coverage import load_report, load_baseline; report=load_report(__import__("pathlib").Path("coverage.json")); baseline=load_baseline(__import__("pathlib").Path("coverage-baseline.json")); floor=lambda value: value.quantize(Decimal("0.01"), rounding=ROUND_DOWN); print(f"measured line={report.line} branch={report.branch}"); print(f"floors line={floor(report.line)} branch={floor(report.branch)}"); print(f"current line={baseline.line} branch={baseline.branch}")'
```

For each metric independently, if its two-decimal round-down floor is higher than the current baseline, update that JSON string to the higher floor with `apply_patch`. Preserve a metric whose floor did not increase. Never lower either value. Re-run the coverage checker and expect PASS.

- [x] **Step 8: Mark the approved design implemented after all gates pass**

In `docs/superpowers/specs/2026-08-02-api-test-environment-hermeticity-design.md`, change:

```markdown
Status: Approved design
```

to:

```markdown
Status: Implemented and verified
```

Append this exact section:

```markdown
## Implementation evidence

The implementation uses Pydantic's supported settings-source seam, an early
pytest construction baseline, and Docker Compose's supported
`--no-env-resolution` flag exactly as designed. Focused source-selection,
collection-time application, and real disposable Compose env-file regressions
all passed. The complete API suite and both coverage ratchets passed once with
no repository dotenv and again with a controlled conflicting `apps/api/.env`;
both runs had zero skips and identical coverage totals.

Normal development dotenv loading and normal service env-file resolution were
proved separately. Production Compose rendering, Clerk runtime behavior,
realtime defaults, dependencies, lockfiles, and deployment state were not
changed.
```

Do not add a second `Issue 21` row to the older visual-execution ledger; this design and plan are the single durable record for the approved API hermeticity decision.

- [x] **Step 9: Remove only exact disposable resources, even after a failed gate**

Use `apply_patch` to delete only the disposable `apps/api/.env` created in Step 5. Then remove generated artifacts and named containers:

```bash
rm -f apps/api/.coverage apps/api/coverage.json /tmp/opevo-api-hermeticity-coverage-clean.json
docker stop opevo-api-hermeticity-postgres opevo-api-hermeticity-redis
docker rm opevo-api-hermeticity-postgres opevo-api-hermeticity-redis
```

Verify cleanup:

```bash
test ! -e apps/api/.env
test ! -e apps/api/.coverage
test ! -e apps/api/coverage.json
test ! -e /tmp/opevo-api-hermeticity-coverage-clean.json
test -z "$(docker ps -a --filter name=^/opevo-api-hermeticity-postgres$ --format '{{.Names}}')"
test -z "$(docker ps -a --filter name=^/opevo-api-hermeticity-redis$ --format '{{.Names}}')"
```

Do not prune Docker and do not remove any differently named container, network, volume, image, cache, ignored file, or user resource.

- [x] **Step 10: Run final repository checks and commit the verified record**

From the worktree root:

```bash
git diff --check
git status --short --branch
git diff --stat c2086c8
git diff --name-only c2086c8
```

Expected changed scope: the two new API test modules, `config.py`, `conftest.py`, `test_deployment_readiness.py`, this implementation plan, the approved design record, and `coverage-baseline.json` only if its measured floor increased. No Compose model, dependency, lockfile, realtime, auth-runtime, deployment, frontend, or agent file may appear.

Commit the verified documentation and any justified coverage ratchet:

```bash
git add \
  docs/superpowers/specs/2026-08-02-api-test-environment-hermeticity-design.md \
  apps/api/coverage-baseline.json
git commit -m "docs: record API test environment verification"
```

If `coverage-baseline.json` did not change, omit it from `git add`. Finish with `git status --short --branch`; expected: clean working tree on `fix/api-test-env-hermeticity`.
