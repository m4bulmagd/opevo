import asyncio
import logging

import httpx

from agent.config import get_settings
from agent.schemas import CallTranscriptItem


logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {408, 425, 429}


class TranscriptAppendError(Exception):
    """Base class for safe transcript-delivery errors."""


class TranscriptAppendRetryableError(TranscriptAppendError):
    """The segment was not acknowledged and may be retried unchanged."""


class TranscriptAppendPermanentError(TranscriptAppendError):
    """The segment cannot be retried safely without operator intervention."""


class CallCompletionAcknowledgementError(ValueError):
    """A successful response did not acknowledge the expected finalization job."""


class CallCompletionRetryableError(RuntimeError):
    """Call completion did not receive a durable acknowledgement."""


def is_completion_acknowledgement(value: object, call_id: str) -> bool:
    return (
        isinstance(value, dict)
        and value.get("status") == "accepted"
        and value.get("queued") is True
        and value.get("job_id") == f"call-finalization:{call_id}"
    )


class AgentApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        agent_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.api_base_url).rstrip("/")
        self.agent_token = (
            agent_token
            if agent_token is not None
            else settings.agent_internal_api_token
        )
        self.app_env = settings.app_env.strip().lower()
        self.http_client = http_client
        self._owns_http_client = http_client is None
        self.timeout = timeout if timeout is not None else settings.api_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.api_max_retries

    async def append_transcript(
        self,
        call_id: str,
        dispatch_token: str,
        item: CallTranscriptItem,
    ) -> dict:
        if not dispatch_token:
            raise TranscriptAppendPermanentError("dispatch token is required")

        url = f"{self.base_url}/api/agent/calls/{call_id}/transcript"
        headers = {"x-agent-token": dispatch_token}
        body = item.model_dump()

        try:
            response = await self._get_http_client().post(
                url,
                json=body,
                headers=headers,
            )
        except httpx.TransportError:
            raise TranscriptAppendRetryableError(
                "transcript append transport failure"
            ) from None

        status_code = response.status_code
        if status_code in RETRYABLE_STATUS_CODES or status_code >= 500:
            raise TranscriptAppendRetryableError(
                f"transcript append retryable status={status_code}"
            )
        if status_code < 200 or status_code >= 300:
            raise TranscriptAppendPermanentError(
                f"transcript append permanent status={status_code}"
            )

        try:
            acknowledgement = response.json()
        except (ValueError, TypeError):
            raise TranscriptAppendPermanentError(
                "transcript append acknowledgement is malformed"
            ) from None

        if not isinstance(acknowledgement, dict):
            raise TranscriptAppendPermanentError(
                "transcript append acknowledgement is malformed"
            )
        if acknowledgement.get("status") not in {"stored", "duplicate"}:
            raise TranscriptAppendPermanentError(
                "transcript append acknowledgement is malformed"
            )
        acknowledged_sequence = acknowledgement.get("sequence_number")
        if (
            type(acknowledged_sequence) is not int
            or acknowledged_sequence != item.sequence_number
        ):
            raise TranscriptAppendPermanentError(
                "transcript append acknowledgement sequence mismatch"
            )

        return acknowledgement

    def _get_http_client(self) -> httpx.AsyncClient:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=self.timeout)
        return self.http_client

    async def aclose(self) -> None:
        if not self._owns_http_client or self.http_client is None:
            return
        client = self.http_client
        self.http_client = None
        await client.aclose()

    async def complete_call(self, payload: dict) -> dict:
        dispatch_token = payload.get("dispatch_token")
        if isinstance(dispatch_token, str) and dispatch_token:
            token = dispatch_token
        elif (
            self.app_env == "development"
            and isinstance(self.agent_token, str)
            and self.agent_token
        ):
            token = self.agent_token
        else:
            raise ValueError("Dispatch token is required")

        url = f"{self.base_url}/api/agent/calls/{payload['call_id']}/complete"
        headers = {"x-agent-token": token}
        body = {
            "duration_seconds": payload["duration_seconds"],
            "transcript": payload.get("transcript") or [],
        }

        call_id = payload["call_id"]
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._get_http_client().post(
                    url,
                    json=body,
                    headers=headers,
                )
            except httpx.TransportError as exc:
                last_exc = CallCompletionRetryableError(
                    "call completion transport failure"
                )
                logger.warning(
                    "complete_call attempt %d/%d failed error_type=%s",
                    attempt,
                    self.max_retries,
                    type(exc).__name__,
                )
            else:
                status_code = response.status_code
                if status_code in RETRYABLE_STATUS_CODES or status_code >= 500:
                    last_exc = CallCompletionRetryableError(
                        f"call completion retryable status={status_code}"
                    )
                    logger.warning(
                        "complete_call attempt %d/%d failed with status %d",
                        attempt,
                        self.max_retries,
                        status_code,
                    )
                elif status_code < 200 or status_code >= 300:
                    raise ValueError(
                        f"call completion permanent status={status_code}"
                    )
                else:
                    try:
                        acknowledgement = response.json()
                    except (ValueError, TypeError):
                        raise CallCompletionAcknowledgementError(
                            "call completion acknowledgement is malformed"
                        ) from None
                    if not is_completion_acknowledgement(
                        acknowledgement,
                        call_id,
                    ):
                        raise CallCompletionAcknowledgementError(
                            "call completion acknowledgement is malformed or mismatched"
                        )
                    return acknowledgement

            if attempt < self.max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))

        if last_exc is None:
            raise CallCompletionRetryableError(
                "call completion exhausted without acknowledgement"
            )
        raise last_exc
