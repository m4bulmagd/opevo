import pytest

from app.providers.notifications.firebase import FirebaseNotificationProvider
from app.providers.storage.s3 import S3Storage


class FakeMinioClient:
    def __init__(self) -> None:
        self.created_buckets: list[str] = []
        self.put_calls: list[dict] = []
        self.presigned_calls: list[dict] = []

    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name in self.created_buckets

    def make_bucket(self, bucket_name: str) -> None:
        self.created_buckets.append(bucket_name)

    def put_object(self, bucket_name: str, object_key: str, data, length: int, content_type: str) -> None:
        self.put_calls.append(
            {
                "bucket_name": bucket_name,
                "object_key": object_key,
                "data": data.read(),
                "length": length,
                "content_type": content_type,
            }
        )

    def presigned_get_object(self, bucket_name: str, object_key: str) -> str:
        self.presigned_calls.append(
            {
                "bucket_name": bucket_name,
                "object_key": object_key,
            }
        )
        return f"http://minio:9000/{bucket_name}/{object_key}?signed=fresh"


class FakeMessagingModule:
    def __init__(self) -> None:
        self.messages: list[object] = []

    class Notification:
        def __init__(self, *, title: str, body: str) -> None:
            self.title = title
            self.body = body

    class Message:
        def __init__(self, *, topic: str, data: dict[str, str], notification) -> None:
            self.topic = topic
            self.data = data
            self.notification = notification

    def send(self, message) -> str:
        self.messages.append(message)
        return "fcm-message-id"


@pytest.mark.anyio
async def test_s3_storage_uploads_recordings_with_env_backed_endpoint() -> None:
    client = FakeMinioClient()
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
        client=client,
    )

    stored = await storage.upload_bytes(
        object_key="calls/user-123/call-456.mp3",
        data=b"audio-bytes",
        content_type="audio/mpeg",
    )

    assert client.created_buckets == ["recordings"]
    assert client.put_calls == [
        {
            "bucket_name": "recordings",
            "object_key": "calls/user-123/call-456.mp3",
            "data": b"audio-bytes",
            "length": 11,
            "content_type": "audio/mpeg",
        }
    ]
    assert stored.url == "http://minio:9000/recordings/calls/user-123/call-456.mp3"


@pytest.mark.anyio
async def test_s3_storage_mints_fresh_download_url() -> None:
    client = FakeMinioClient()
    storage = S3Storage(
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        region="us-east-1",
        client=client,
    )

    signed_url = await storage.get_download_url(object_key="calls/user-123/call-456.mp3")

    assert client.created_buckets == ["recordings"]
    assert client.presigned_calls == [
        {
            "bucket_name": "recordings",
            "object_key": "calls/user-123/call-456.mp3",
        }
    ]
    assert signed_url == "http://minio:9000/recordings/calls/user-123/call-456.mp3?signed=fresh"


@pytest.mark.anyio
async def test_firebase_provider_sends_topic_notification_per_user() -> None:
    messaging = FakeMessagingModule()
    provider = FirebaseNotificationProvider(messaging_module=messaging)

    status = await provider.send_notification(
        user_id="user_123",
        notification_type="call_completed",
        payload={"summary_text": "Caller asked about opening hours.", "minutes_charged": 2},
    )

    assert status == "fcm-message-id"
    message = messaging.messages[0]
    assert message.topic == "user-user_123"
    assert message.data["notification_type"] == "call_completed"
    assert message.data["summary_text"] == "Caller asked about opening hours."
