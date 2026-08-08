from uuid import uuid4

import pytest

from app.core.observability import Observability, instrument_provider
from app.services.recording_service import RecordingService


class _Instrument:
    def add(self, *_args, **_kwargs) -> None:
        return None

    record = add
    set = add


class _Meter:
    def create_counter(self, *_args, **_kwargs) -> _Instrument:
        return _Instrument()

    create_histogram = create_counter
    create_gauge = create_counter


class _Span:
    def __init__(self, attributes: dict | None) -> None:
        self.attributes = dict(attributes or {})

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_status(self, _status) -> None:
        return None


class _Tracer:
    def __init__(self) -> None:
        self.spans: list[_Span] = []

    def start_as_current_span(self, _name: str, *, attributes=None, **_kwargs):
        span = _Span(attributes)
        self.spans.append(span)
        return span


class _InstrumentedStorage:
    def __init__(self, telemetry: Observability) -> None:
        self.observability = telemetry

    @instrument_provider("s3", "get_download_url")
    async def get_download_url(self, *, object_key: str) -> str:
        if object_key.startswith("missing/"):
            raise FileNotFoundError
        return "https://storage.example.test/signed"


def _service() -> tuple[RecordingService, _InstrumentedStorage, _Tracer]:
    tracer = _Tracer()
    telemetry = Observability(meter=_Meter(), tracer=tracer)
    provider = _InstrumentedStorage(telemetry)
    return RecordingService(provider=provider), provider, tracer


@pytest.mark.anyio
async def test_recording_download_span_binds_call_id_only_around_provider_call() -> None:
    service, provider, tracer = _service()
    call_id = uuid4()
    user_id = uuid4()

    result = await service.get_access_url(
        call_id=call_id,
        user_id=user_id,
        recording_object_key="calls/RECORDING_OBJECT_SECRET.mp3",
    )
    await provider.get_download_url(object_key="outside/RECORDING_OBJECT_SECRET.mp3")

    assert result == "https://storage.example.test/signed"
    assert tracer.spans[0].attributes["opevo.call.id"] == str(call_id)
    assert "opevo.call.id" not in tracer.spans[1].attributes
    rendered = repr([span.attributes for span in tracer.spans])
    assert str(user_id) not in rendered
    assert "RECORDING_OBJECT_SECRET" not in rendered


@pytest.mark.anyio
async def test_missing_recording_resets_bound_call_id_after_provider_error() -> None:
    service, provider, tracer = _service()
    call_id = uuid4()

    result = await service.get_access_url(
        call_id=call_id,
        user_id=uuid4(),
        recording_object_key="missing/RECORDING_OBJECT_SECRET.mp3",
    )
    with pytest.raises(FileNotFoundError):
        await provider.get_download_url(
            object_key="missing/outside/RECORDING_OBJECT_SECRET.mp3"
        )

    assert result is None
    assert tracer.spans[0].attributes["opevo.call.id"] == str(call_id)
    assert "opevo.call.id" not in tracer.spans[1].attributes
    assert "RECORDING_OBJECT_SECRET" not in repr(
        [span.attributes for span in tracer.spans]
    )
