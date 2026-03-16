import importlib
import inspect

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
