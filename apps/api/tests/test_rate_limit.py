from app.core.config import Settings
from app.core.rate_limit import limiter


def _settings(*, app_env: str) -> Settings:
    return Settings(
        app_env=app_env,
        database_url="sqlite+aiosqlite://",
        redis_url="redis://localhost:6379/0",
        auth_provider="clerk",
        agent_dispatch_jwt_secret="test-dispatch-secret-with-at-least-32-bytes",
    )


def test_test_app_explicitly_disables_limiter(
    monkeypatch,
) -> None:
    from app.main import create_app

    monkeypatch.setattr(limiter, "enabled", True)

    application = create_app(_settings(app_env="test"))

    assert application.state.limiter.enabled is False


def test_fresh_non_test_app_reenables_limiter_after_test_app() -> None:
    from app.main import create_app

    create_app(_settings(app_env="test"))

    application = create_app(_settings(app_env="development"))

    assert application.state.limiter.enabled is True


def test_normalized_development_environment_registers_development_routes() -> None:
    from app.main import create_app

    application = create_app(_settings(app_env=" DeVeLoPmEnT "))

    registered_paths = {route.path for route in application.routes}
    assert "/api/development/activate-starter" in registered_paths
    assert "/api/development/simulate-forwarded-call" in registered_paths
