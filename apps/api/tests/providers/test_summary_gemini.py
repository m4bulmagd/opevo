import asyncio
import builtins
from types import SimpleNamespace
from contextlib import asynccontextmanager

import httpx
import pytest
from google.genai import errors as genai_errors

from app.core.provider_failures import ProviderFailure
from app.providers.summaries.gemini import GeminiSummaryProvider


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
    with pytest.raises(ValueError, match="Gemini returned no text"):
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
