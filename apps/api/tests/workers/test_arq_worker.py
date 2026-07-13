import importlib
import inspect

import pytest
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers import arq_worker


def test_worker_settings_use_configured_redis_url(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/7")
    get_settings.cache_clear()

    module = importlib.reload(arq_worker)

    assert isinstance(module.WorkerSettings.redis_settings, RedisSettings)
    assert module.WorkerSettings.redis_settings.host == "redis"
    assert module.WorkerSettings.redis_settings.port == 6379
    assert module.WorkerSettings.redis_settings.database == 7

    get_settings.cache_clear()


def test_worker_job_functions_accept_arq_context() -> None:
    for function in arq_worker.WorkerSettings.functions:
        parameters = list(inspect.signature(function).parameters.values())
        assert parameters
        assert parameters[0].name == "ctx"


@pytest.mark.anyio
async def test_worker_startup_initializes_safe_logging(monkeypatch) -> None:
    setup_calls: list[bool] = []
    monkeypatch.setattr(arq_worker, "setup_logging", lambda: setup_calls.append(True), raising=False)

    await arq_worker.WorkerSettings.on_startup({})

    assert setup_calls == [True]
