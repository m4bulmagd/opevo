import asyncio
from pathlib import Path

import pytest

from app.core.observability import get_observability
from app.providers.storage.s3 import S3Storage
from app.providers.storage import s3 as s3_module


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LIFECYCLE_PATH = REPOSITORY_ROOT / "infra/minio/recording-lifecycle.json"


class DeletionInspectingClient:
    def __init__(self) -> None:
        self.bucket_exists_calls: list[str] = []
        self.remove_calls: list[tuple[str, str]] = []

    def bucket_exists(self, bucket_name: str) -> bool:
        self.bucket_exists_calls.append(bucket_name)
        return True

    def remove_object(self, bucket_name: str, object_key: str) -> None:
        self.remove_calls.append((bucket_name, object_key))


def test_explicit_storage_construction_does_not_read_global_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_global_read() -> None:
        raise AssertionError("explicit S3 construction read global configuration")

    monkeypatch.setattr(
        s3_module,
        "get_settings",
        forbidden_global_read,
        raising=False,
    )
    monkeypatch.setattr(
        s3_module,
        "get_observability",
        forbidden_global_read,
        raising=False,
    )

    storage = S3Storage(
        bucket_name="runtime-recordings",
        endpoint_url="https://storage.example.com",
        access_key="runtime-access",
        secret_key="runtime-secret",
        region="eu-west-3",
        client=DeletionInspectingClient(),
        observability=object(),
    )

    assert storage.bucket_name == "runtime-recordings"
    assert storage.endpoint_url == "https://storage.example.com"
    assert storage.access_key == "runtime-access"
    assert storage.secret_key == "runtime-secret"
    assert storage.region == "eu-west-3"


@pytest.mark.anyio
async def test_storage_provider_deletes_recording_object() -> None:
    client = DeletionInspectingClient()
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
        client=client,
        observability=get_observability(),
    )

    await storage.delete_object(object_key="calls/user/call.mp3")

    assert client.bucket_exists_calls == ["recordings"]
    assert client.remove_calls == [("recordings", "calls/user/call.mp3")]


@pytest.mark.anyio
async def test_storage_closes_its_owned_http_pool_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minio

    class HttpPool:
        def __init__(self) -> None:
            self.clear_calls = 0

        def clear(self) -> None:
            self.clear_calls += 1

    pool = HttpPool()
    observed: dict[str, object] = {}

    class MinioClient:
        def __init__(self, endpoint: str, **kwargs: object) -> None:
            observed["endpoint"] = endpoint
            observed["http_client"] = kwargs["http_client"]

    monkeypatch.setattr(s3_module, "PoolManager", lambda **_kwargs: pool)
    monkeypatch.setattr(minio, "Minio", MinioClient)
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="https://storage.example.com",
        access_key="access-key",
        secret_key="secret-key",
        region="eu-west-3",
        observability=object(),
    )

    storage._get_client()
    await asyncio.gather(storage.aclose(), storage.aclose())
    await storage.aclose()

    assert observed == {
        "endpoint": "storage.example.com",
        "http_client": pool,
    }
    assert pool.clear_calls == 1


@pytest.mark.anyio
async def test_storage_never_closes_an_injected_client_pool() -> None:
    class BorrowedClient(DeletionInspectingClient):
        def __init__(self) -> None:
            super().__init__()
            self.clear_calls = 0

        def clear(self) -> None:
            self.clear_calls += 1

    client = BorrowedClient()
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="https://storage.example.com",
        access_key="access-key",
        secret_key="secret-key",
        region="eu-west-3",
        observability=object(),
        client=client,
    )

    await storage.aclose()

    assert client.clear_calls == 0


def test_local_stack_has_no_automatic_expiration_and_keeps_recordings_private() -> None:
    production_compose = (REPOSITORY_ROOT / "compose.yaml").read_text()
    compose = (REPOSITORY_ROOT / "compose.dev.yaml").read_text()

    assert "minio:" not in production_compose
    assert not LIFECYCLE_PATH.exists()
    assert "recording-lifecycle.json" not in compose
    assert "mc ilm" not in compose
    assert "mc mb --ignore-existing local/recordings" in compose
    assert "mc anonymous set private local/recordings" in compose
