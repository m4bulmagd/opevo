from pathlib import Path

import pytest

from app.providers.storage.s3 import S3Storage


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


@pytest.mark.anyio
async def test_storage_provider_deletes_recording_object() -> None:
    client = DeletionInspectingClient()
    storage = S3Storage(bucket_name="recordings", client=client)

    await storage.delete_object(object_key="calls/user/call.mp3")

    assert client.bucket_exists_calls == ["recordings"]
    assert client.remove_calls == [("recordings", "calls/user/call.mp3")]


def test_local_stack_has_no_automatic_expiration_and_keeps_recordings_private() -> None:
    production_compose = (REPOSITORY_ROOT / "compose.yaml").read_text()
    compose = (REPOSITORY_ROOT / "compose.dev.yaml").read_text()

    assert "minio:" not in production_compose
    assert not LIFECYCLE_PATH.exists()
    assert "recording-lifecycle.json" not in compose
    assert "mc ilm" not in compose
    assert "mc mb --ignore-existing local/recordings" in compose
    assert "mc anonymous set private local/recordings" in compose
