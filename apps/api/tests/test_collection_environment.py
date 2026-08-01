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
