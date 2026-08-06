import asyncio
import builtins
import json
import logging
import traceback
from types import SimpleNamespace
from contextlib import asynccontextmanager

import httpx
import pytest
from google.genai import errors as genai_errors

from app.core.provider_failures import ProviderFailure
from app.providers.summaries.gemini import (
    GeminiSummaryProvider as _GeminiSummaryProvider,
    _MalformedGeminiResponse,
)


class _Telemetry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    @asynccontextmanager
    async def provider_operation(self, provider: str, operation: str, **_kwargs):
        try:
            yield
        except Exception:
            self.calls.append((provider, operation, "error"))
            raise
        else:
            self.calls.append((provider, operation, "success"))


class GeminiSummaryProvider(_GeminiSummaryProvider):
    def __init__(
        self,
        *,
        api_key: str | None = "test-key",
        model: str = "gemini-test",
        observability=None,
        client=None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            observability=observability or _Telemetry(),
            client=client,
        )


def test_extract_json_accepts_markdown_fenced_payload() -> None:
    response = SimpleNamespace(
        text="""```json
{
  "summary_text": "Caller asked about opening hours.",
  "caller_intent": "Ask about opening hours",
  "action_items": ["Provide opening hours"],
  "sentiment": "neutral",
  "follow_up_required": false
}
```"""
    )

    payload = GeminiSummaryProvider._extract_json(response)

    assert payload["summary_text"] == "Caller asked about opening hours."
    assert payload["action_items"] == ["Provide opening hours"]


def test_extract_json_accepts_prose_wrapped_payload() -> None:
    response = SimpleNamespace(
        text=(
            "Here is the structured summary:\n"
            '{'
            '"summary_text":"Caller asked about pricing.",'
            '"caller_intent":"Ask about pricing",'
            '"action_items":["Share current pricing"],'
            '"sentiment":"neutral",'
            '"follow_up_required":false'
            '}'
        )
    )

    payload = GeminiSummaryProvider._extract_json(response)

    assert payload["caller_intent"] == "Ask about pricing"
    assert payload["follow_up_required"] is False


def test_extract_json_rejects_missing_text() -> None:
    with pytest.raises(_MalformedGeminiResponse):
        GeminiSummaryProvider._extract_json(SimpleNamespace(text=""))


@pytest.mark.anyio
async def test_generate_summary_uses_async_client_and_parses_result() -> None:
    class SyncModels:
        def generate_content(self, **_kwargs):
            raise AssertionError("synchronous Gemini client must not run on event loop")

    class AsyncModels:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                text=(
                    '{"summary_text":"Async summary",'
                    '"caller_intent":"Ask",'
                    '"action_items":["Reply"],'
                    '"sentiment":"neutral",'
                    '"follow_up_required":false}'
                )
            )

    async_models = AsyncModels()
    telemetry = _Telemetry()
    client = SimpleNamespace(
        models=SyncModels(),
        aio=SimpleNamespace(models=async_models),
    )

    result = await GeminiSummaryProvider(
        client=client,
        model="gemini-test",
        observability=telemetry,
    ).generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert result.summary_text == "Async summary"
    assert len(async_models.calls) == 1
    assert async_models.calls[0]["model"] == "gemini-test"
    assert "CALLER: Hello" in async_models.calls[0]["contents"]
    assert telemetry.calls == [("gemini", "generate_summary", "success")]


class _FailingAsyncModels:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def generate_content(self, **_kwargs):
        raise self.error


def _async_client(error: BaseException) -> SimpleNamespace:
    return SimpleNamespace(aio=SimpleNamespace(models=_FailingAsyncModels(error)))


def _summary_payload(**overrides: object) -> dict[str, object]:
    return {
        "summary_text": "Summary",
        "caller_intent": "Intent",
        "action_items": ["Reply"],
        "sentiment": "neutral",
        "follow_up_required": False,
        **overrides,
    }


def _response_client(response: object) -> SimpleNamespace:
    class AsyncModels:
        async def generate_content(self, **_kwargs):
            return response

    return SimpleNamespace(aio=SimpleNamespace(models=AsyncModels()))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected_disposition", "expected_error_class"),
    [
        (408, "retryable", "timeout"),
        (429, "retryable", "rate_limited"),
        (401, "terminal", "authentication"),
        (403, "terminal", "authentication"),
        (409, "terminal", "conflict"),
        (422, "terminal", "validation"),
        (503, "retryable", "unavailable"),
    ],
)
async def test_gemini_maps_known_api_statuses_to_safe_provider_failures(
    status: int,
    expected_disposition: str,
    expected_error_class: str,
) -> None:
    response_body = {"message": "GEMINI_RESPONSE_BODY_SENTINEL"}
    error = genai_errors.APIError(status, response_body)
    provider = GeminiSummaryProvider(client=_async_client(error), model="gemini-test")

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("gemini", "generate_summary", expected_disposition, expected_error_class)
    assert "GEMINI_RESPONSE_BODY_SENTINEL" not in str(exc_info.value)
    assert exc_info.value.__cause__ is error


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_error_class"),
    [
        (httpx.ReadTimeout("GEMINI_TIMEOUT_SENTINEL"), "timeout"),
        (
            genai_errors.UnknownApiResponseError(
                "GEMINI_RESPONSE_BODY_SENTINEL"
            ),
            "validation",
        ),
    ],
)
async def test_gemini_maps_known_transport_and_malformed_response_failures(
    error: Exception,
    expected_error_class: str,
) -> None:
    provider = GeminiSummaryProvider(client=_async_client(error), model="gemini-test")

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert (
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("terminal" if expected_error_class == "validation" else "retryable", expected_error_class)
    assert "SENTINEL" not in str(exc_info.value)
    assert exc_info.value.__cause__ is error


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(text="GEMINI_RESPONSE_BODY_SENTINEL not-json"),
        SimpleNamespace(text='{"summary_text":"missing schema"}'),
    ],
)
async def test_gemini_maps_malformed_content_to_terminal_validation(
    response: SimpleNamespace,
) -> None:
    class AsyncModels:
        async def generate_content(self, **_kwargs):
            return response

    provider = GeminiSummaryProvider(
        client=SimpleNamespace(aio=SimpleNamespace(models=AsyncModels())),
        model="gemini-test",
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("gemini", "generate_summary", "terminal", "validation")
    assert "GEMINI_RESPONSE_BODY_SENTINEL" not in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        _summary_payload(summary_text=7),
        _summary_payload(summary_text={"text": "summary"}),
        _summary_payload(summary_text=None),
        _summary_payload(caller_intent=7),
        _summary_payload(caller_intent={"intent": "summary"}),
        _summary_payload(caller_intent=None),
        _summary_payload(sentiment=7),
        _summary_payload(sentiment={"sentiment": "neutral"}),
        _summary_payload(sentiment=None),
        _summary_payload(action_items="Reply"),
        _summary_payload(action_items=None),
        _summary_payload(action_items=["Reply", 7]),
        _summary_payload(action_items=["Reply", {"item": "next"}]),
        _summary_payload(action_items=["Reply", None]),
        _summary_payload(follow_up_required=0),
        _summary_payload(follow_up_required=1),
        _summary_payload(follow_up_required="false"),
        _summary_payload(follow_up_required={"value": False}),
        _summary_payload(follow_up_required=None),
        _summary_payload(unexpected="field"),
    ],
)
async def test_gemini_rejects_wrong_type_or_shape_response_payloads(
    payload: object,
) -> None:
    provider = GeminiSummaryProvider(
        client=_response_client(SimpleNamespace(text=json.dumps(payload))),
        model="gemini-test",
    )

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert (
        exc_info.value.provider,
        exc_info.value.operation,
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("gemini", "generate_summary", "terminal", "validation")
    assert exc_info.value.__cause__ is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [TypeError("GEMINI_RESPONSE_ACCESSOR_TYPE_ERROR"), ValueError("GEMINI_RESPONSE_ACCESSOR_VALUE_ERROR")],
)
async def test_gemini_propagates_response_accessor_defects_unchanged(
    error: Exception,
) -> None:
    class DefectiveResponse:
        @property
        def text(self) -> str:
            raise error

    provider = GeminiSummaryProvider(
        client=_response_client(DefectiveResponse()),
        model="gemini-test",
    )

    with pytest.raises(type(error)) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert exc_info.value is error


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [TypeError("GEMINI_PARSER_TYPE_ERROR"), ValueError("GEMINI_PARSER_VALUE_ERROR")],
)
async def test_gemini_propagates_injected_parser_defects_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    def defective_extract_json(_response: object) -> dict:
        raise error

    monkeypatch.setattr(
        GeminiSummaryProvider,
        "_extract_json",
        staticmethod(defective_extract_json),
    )
    provider = GeminiSummaryProvider(
        client=_response_client(SimpleNamespace(text=json.dumps(_summary_payload()))),
        model="gemini-test",
    )

    with pytest.raises(type(error)) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert exc_info.value is error


@pytest.mark.anyio
async def test_gemini_maps_missing_credentials_to_terminal_configuration_failure() -> None:
    provider = GeminiSummaryProvider(api_key="", client=None, model="gemini-test")

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert (
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("terminal", "authentication")
    assert exc_info.value.__cause__ is None


@pytest.mark.anyio
async def test_gemini_maps_missing_sdk_import_to_terminal_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def missing_google(name: str, *args, **kwargs):
        if name == "google":
            raise ImportError("GEMINI_IMPORT_SENTINEL")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_google)
    provider = GeminiSummaryProvider(api_key="test-key", client=None, model="gemini-test")

    with pytest.raises(ProviderFailure) as exc_info:
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])

    assert (
        exc_info.value.disposition,
        exc_info.value.error_class,
    ) == ("terminal", "unknown")
    assert "GEMINI_IMPORT_SENTINEL" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ImportError)


@pytest.mark.anyio
@pytest.mark.parametrize("error", [TypeError("GEMINI_DEFECT_SENTINEL"), RuntimeError("GEMINI_DEFECT_SENTINEL")])
async def test_gemini_does_not_translate_injected_programming_defects(
    error: Exception,
) -> None:
    provider = GeminiSummaryProvider(client=_async_client(error), model="gemini-test")

    with pytest.raises(type(error), match="GEMINI_DEFECT_SENTINEL"):
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])


@pytest.mark.anyio
async def test_gemini_propagates_cancellation_unchanged() -> None:
    provider = GeminiSummaryProvider(
        client=_async_client(asyncio.CancelledError()),
        model="gemini-test",
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.generate_summary([{"speaker": "CALLER", "text": "Hello"}])


@pytest.mark.anyio
async def test_gemini_closes_owned_sdk_async_transport_not_sync_transport() -> None:
    class AsyncClient:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    class Client:
        def __init__(self) -> None:
            self.aio = AsyncClient()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    client = Client()
    provider = GeminiSummaryProvider(
        api_key="test-key",
        model="gemini-test",
        observability=object(),
    )
    provider.client = client

    await provider.aclose()

    assert client.aio.close_calls == 1
    assert client.close_calls == 0


@pytest.mark.anyio
async def test_gemini_owned_sync_only_client_uses_sync_close_fallback() -> None:
    class SyncClient:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    client = SyncClient()
    provider = GeminiSummaryProvider(
        api_key="test-key",
        model="gemini-test",
        observability=object(),
    )
    provider.client = client

    await provider.aclose()

    assert client.close_calls == 1


@pytest.mark.anyio
async def test_gemini_concurrent_closers_join_one_owned_cleanup() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class AsyncClient:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            started.set()
            await release.wait()

    client = SimpleNamespace(aio=AsyncClient())
    provider = GeminiSummaryProvider(
        api_key="test-key",
        model="gemini-test",
        observability=object(),
    )
    provider.client = client

    first = asyncio.create_task(provider.aclose())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    second = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    await provider.aclose()

    assert client.aio.close_calls == 1


@pytest.mark.anyio
async def test_gemini_cancelled_waiter_does_not_cancel_owned_cleanup_and_second_joins() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class AsyncClient:
        def __init__(self) -> None:
            self.close_calls = 0
            self.was_cancelled = False

        async def aclose(self) -> None:
            self.close_calls += 1
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                self.was_cancelled = True
                raise
            finally:
                finished.set()

    client = SimpleNamespace(aio=AsyncClient())
    provider = GeminiSummaryProvider(
        api_key="test-key",
        model="gemini-test",
        observability=object(),
    )
    provider.client = client

    first = asyncio.create_task(provider.aclose())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(provider.aclose())
    await asyncio.sleep(0)
    assert not finished.is_set()
    release.set()
    await second

    assert finished.is_set()
    assert client.aio.close_calls == 1
    assert client.aio.was_cancelled is False


@pytest.mark.anyio
async def test_gemini_failed_close_after_sole_waiter_cancellation_is_safely_observed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    close_error = RuntimeError("GEMINI_CLOSE_PRIVATE_SENTINEL")
    loop_contexts: list[dict[str, object]] = []

    class AsyncClient:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            started.set()
            try:
                await release.wait()
                raise close_error
            finally:
                finished.set()

    client = SimpleNamespace(aio=AsyncClient())
    provider = GeminiSummaryProvider(
        api_key="test-key",
        model="gemini-test",
        observability=object(),
    )
    provider.client = client
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_contexts.append(context))
    try:
        with caplog.at_level(logging.WARNING):
            first = asyncio.create_task(provider.aclose())
            await asyncio.wait_for(started.wait(), timeout=0.5)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

            release.set()
            await asyncio.wait_for(finished.wait(), timeout=0.5)
            await asyncio.sleep(0)

            with pytest.raises(RuntimeError) as caught:
                await provider.aclose()
    finally:
        loop.set_exception_handler(previous_handler)

    formatted_diagnostics = caplog.text + "\n".join(
        "".join(traceback.format_exception(error))
        if isinstance((error := context.get("exception")), BaseException)
        else repr(context)
        for context in loop_contexts
    )
    assert caught.value is close_error
    assert client.aio.close_calls == 1
    assert loop_contexts == []
    assert "event=gemini_client_close_failed" in caplog.text
    assert "GEMINI_CLOSE_PRIVATE_SENTINEL" not in formatted_diagnostics


@pytest.mark.anyio
async def test_gemini_cancelled_close_task_is_observed_without_callback_failure() -> None:
    loop_contexts: list[dict[str, object]] = []

    class AsyncClient:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            raise asyncio.CancelledError("owned-close-cancelled")

    client = SimpleNamespace(aio=AsyncClient())
    provider = GeminiSummaryProvider(
        api_key="test-key",
        model="gemini-test",
        observability=object(),
    )
    provider.client = client
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_contexts.append(context))
    try:
        with pytest.raises(asyncio.CancelledError) as first:
            await provider.aclose()
        await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError) as second:
            await provider.aclose()
    finally:
        loop.set_exception_handler(previous_handler)

    assert isinstance(first.value, asyncio.CancelledError)
    assert isinstance(second.value, asyncio.CancelledError)
    assert client.aio.close_calls == 1
    assert loop_contexts == []


@pytest.mark.anyio
async def test_gemini_never_closes_an_injected_client() -> None:
    class AsyncClient:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    class Client:
        def __init__(self) -> None:
            self.aio = AsyncClient()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    client = Client()
    provider = GeminiSummaryProvider(
        api_key=None,
        model="gemini-test",
        observability=object(),
        client=client,
    )

    await provider.aclose()

    assert client.aio.close_calls == 0
    assert client.close_calls == 0
