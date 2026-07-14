import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.providers.storage.s3 import S3Storage


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LIFECYCLE_PATH = REPOSITORY_ROOT / "infra/minio/recording-lifecycle.json"


class LifecycleInspectingClient:
    def __init__(self, lifecycle) -> None:
        self.lifecycle = lifecycle
        self.bucket_exists_calls: list[str] = []
        self.lifecycle_calls: list[str] = []

    def bucket_exists(self, bucket_name: str) -> bool:
        self.bucket_exists_calls.append(bucket_name)
        return True

    def get_bucket_lifecycle(self, bucket_name: str):
        self.lifecycle_calls.append(bucket_name)
        return self.lifecycle


def test_recording_lifecycle_manifest_has_exact_retention_rule() -> None:
    manifest = json.loads(LIFECYCLE_PATH.read_text())

    assert manifest == {
        "Rules": [
            {
                "Expiration": {"Days": 30},
                "ID": "expire-call-recordings-after-30-days",
                "Filter": {"Prefix": "calls/"},
                "Status": "Enabled",
            }
        ]
    }


def test_development_minio_init_imports_lifecycle_and_enforces_private_access() -> None:
    production_compose = (REPOSITORY_ROOT / "compose.yaml").read_text()
    compose = (REPOSITORY_ROOT / "compose.dev.yaml").read_text()

    assert "minio:" not in production_compose
    assert "./infra/minio/recording-lifecycle.json:/config/recording-lifecycle.json:ro" in compose
    assert "mc mb --ignore-existing local/recordings" in compose
    assert "mc ilm rule import local/recordings < /config/recording-lifecycle.json" in compose
    assert "mc anonymous set private local/recordings" in compose


@pytest.mark.anyio
async def test_storage_provider_inspects_configured_bucket_lifecycle() -> None:
    expected_lifecycle = object()
    client = LifecycleInspectingClient(expected_lifecycle)
    storage = S3Storage(bucket_name="recordings", client=client)

    lifecycle = await storage.get_bucket_lifecycle()

    assert lifecycle is expected_lifecycle
    assert client.bucket_exists_calls == ["recordings"]
    assert client.lifecycle_calls == ["recordings"]


@pytest.mark.anyio
async def test_minio_reference_deployment_lifecycle_and_privacy() -> None:
    endpoint = os.getenv("MINIO_INTEGRATION_ENDPOINT")
    if not endpoint:
        pytest.skip("MINIO_INTEGRATION_ENDPOINT is not configured")

    storage = S3Storage(
        bucket_name=os.getenv("MINIO_INTEGRATION_BUCKET", "recordings"),
        endpoint_url=endpoint,
        access_key=os.getenv("MINIO_INTEGRATION_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_INTEGRATION_SECRET_KEY", "minioadmin"),
    )
    lifecycle = await storage.get_bucket_lifecycle()
    matching_rules = [
        rule
        for rule in lifecycle.rules
        if rule.rule_filter is not None
        and rule.rule_filter.prefix == "calls/"
        and rule.expiration is not None
        and rule.expiration.days == 30
        and rule.status == "Enabled"
    ]
    assert matching_rules

    client = storage._get_client()
    from minio.error import S3Error

    try:
        policy = await __import__("asyncio").to_thread(
            client.get_bucket_policy,
            storage.bucket_name,
        )
    except S3Error as exc:
        assert exc.code == "NoSuchBucketPolicy"
    else:
        assert policy in {None, ""}

    object_key = f"calls/lifecycle-integration-{uuid4().hex}.bin"
    try:
        stored = await storage.upload_bytes(
            object_key=object_key,
            data=b"lifecycle-integration",
            content_type="application/octet-stream",
        )
        assert stored.object_key == object_key
        from minio.commonconfig import Tags

        lifecycle_test_tags = Tags.new_object_tags()
        lifecycle_test_tags["lifecycle-test"] = "task-12"
        await __import__("asyncio").to_thread(
            client.set_object_tags,
            storage.bucket_name,
            object_key,
            lifecycle_test_tags,
        )
        stored_tags = await __import__("asyncio").to_thread(
            client.get_object_tags,
            storage.bucket_name,
            object_key,
        )
        assert stored_tags is not None
        assert stored_tags["lifecycle-test"] == "task-12"
        assert await storage.get_download_url(object_key=object_key) is not None
    finally:
        await __import__("asyncio").to_thread(
            client.remove_object,
            storage.bucket_name,
            object_key,
        )
