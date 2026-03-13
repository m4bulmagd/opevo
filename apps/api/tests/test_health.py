def test_settings_load_required_fields(settings) -> None:
    assert settings.app_env == "test"
    assert settings.database_url.startswith("postgresql")


def test_healthcheck_returns_ok(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
