from types import SimpleNamespace

import pytest
from livekit import api

import app.services.livekit_recording_service as recording_service_module
from app.services.livekit_recording_service import LiveKitRecordingService


class RecordingProviderSpy:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, str]] = []

    async def ensure_stopped(self, egress_id: str) -> None:
        self.calls.append(("ensure_stopped", egress_id))
        if self.failure is not None:
            raise self.failure

    async def ensure_not_running(self, egress_id: str) -> None:
        self.calls.append(("ensure_not_running", egress_id))
        if self.failure is not None:
            raise self.failure


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation",
    ["ensure_stopped", "ensure_not_running"],
)
async def test_ensure_operations_delegate_to_injected_provider(
    operation: str,
) -> None:
    provider = RecordingProviderSpy()

    await getattr(LiveKitRecordingService(provider=provider), operation)("egress-1")

    assert provider.calls == [(operation, "egress-1")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation",
    ["ensure_stopped", "ensure_not_running"],
)
async def test_ensure_operations_close_owned_livekit_client_after_provider_error(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    failure = RuntimeError("provider operation failed")
    provider = RecordingProviderSpy(failure=failure)
    api_clients = []

    class FakeLiveKitAPI:
        def __init__(self, **_kwargs) -> None:
            self.egress = object()
            self.closed = False
            api_clients.append(self)

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        recording_service_module,
        "get_settings",
        lambda: SimpleNamespace(
            livekit_url="wss://livekit.example.test",
            livekit_api_key="key",
            livekit_api_secret="secret",
            storage_bucket_name="recordings",
            s3_endpoint_url="http://minio:9000",
            s3_access_key="access-key",
            s3_secret_key="secret-key",
            s3_region="us-east-1",
        ),
    )
    monkeypatch.setattr(api, "LiveKitAPI", FakeLiveKitAPI)
    monkeypatch.setattr(
        recording_service_module,
        "LiveKitRecordingProvider",
        lambda **_kwargs: provider,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await getattr(LiveKitRecordingService(), operation)("egress-1")

    assert exc_info.value is failure
    assert provider.calls == [(operation, "egress-1")]
    assert len(api_clients) == 1
    assert api_clients[0].closed is True
