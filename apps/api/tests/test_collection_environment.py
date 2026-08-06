import os
from pathlib import Path

import pytest

import conftest
from app.core.clerk_verification_source import select_clerk_verification_source
from app.core.config import Settings
from app.core.runtime_validation import validate_api_runtime
from app.main import app as collection_app


def test_collection_time_application_defers_runtime_construction() -> None:
    assert collection_app.state.runtime is None
    assert not hasattr(collection_app.state, "auth_provider")


@pytest.mark.parametrize("inherited_name", ["clerk_jwt_key", "ClErK_JwT_KeY"])
def test_function_settings_environment_replaces_case_variant_clerk_keys(
    inherited_name: str,
    clerk_key_material: dict[str, str | bytes],
) -> None:
    os.environ[inherited_name] = "inherited-static-key"
    try:
        with pytest.MonkeyPatch.context() as scoped_patch:
            fixture = conftest.settings_env.__wrapped__(
                scoped_patch,
                clerk_key_material,
            )
            next(fixture)
            try:
                settings = Settings(app_env="development")

                assert inherited_name not in os.environ
                assert settings.clerk_jwt_key == clerk_key_material["public_key_pem"]
                assert settings.clerk_jwks_url == ""
            finally:
                with pytest.raises(StopIteration):
                    next(fixture)

        assert os.environ[inherited_name] == "inherited-static-key"
    finally:
        os.environ.pop(inherited_name, None)


def test_function_settings_environment_shadows_dotenv_jwks_for_non_test_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "CLERK_JWKS_URL=https://poison.example.invalid/jwks.json\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings(app_env="development")
    verification_source = select_clerk_verification_source(
        jwt_key=settings.clerk_jwt_key,
        jwks_url=settings.clerk_jwks_url,
    )

    assert settings.clerk_jwks_url == ""
    assert settings.clerk_jwt_key is not None
    assert verification_source is not None
    assert verification_source.kind == "static"
    assert verification_source.value == settings.clerk_jwt_key
    validate_api_runtime(settings)
