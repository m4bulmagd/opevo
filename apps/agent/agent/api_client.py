import asyncio
import logging
from uuid import UUID

import httpx
from presvo_contracts import (
    CallCompletionAcknowledgement,
    CallCompletionRequest,
    ContractError,
    TranscriptAppendAcknowledgement,
    TranscriptAppendRequest,
    TranscriptSegment,
    VerificationCompletionAcknowledgement,
    VerificationCompletionRequest,
    create_contract,
    dump_contract,
    parse_contract,
)

from agent.config import get_settings
from agent.safe_logging import install_safe_http_client_logging, report_contract_failure


logger = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {408, 425, 429}


class TranscriptAppendError(Exception):
    """Base class for safe transcript-delivery errors."""


class TranscriptAppendRetryableError(TranscriptAppendError):
    """The segment was not acknowledged and may be retried unchanged."""


class TranscriptAppendPermanentError(TranscriptAppendError):
    """The segment cannot be retried safely without operator intervention."""


class TranscriptAppendContractError(TranscriptAppendPermanentError):
    """A local request or successful response violated the transcript contract."""


class TranscriptAppendAcknowledgementError(TranscriptAppendContractError):
    """A successful response did not acknowledge the expected segment."""


class CallCompletionContractError(ValueError):
    """A local request or successful response violated the completion contract."""


class CallCompletionAcknowledgementError(CallCompletionContractError):
    """A successful response did not acknowledge the expected finalization job."""


class CallCompletionRetryableError(RuntimeError):
    """Call completion did not receive a durable acknowledgement."""


class VerificationCompletionRetryableError(RuntimeError):
    """Verification completion may be retried unchanged."""


class VerificationCompletionPermanentError(RuntimeError):
    """Verification completion was permanently rejected."""


class VerificationCompletionAcknowledgementError(VerificationCompletionPermanentError):
    """A successful response did not acknowledge this verification session."""


def _log_contract_error(*, operation: str, error: ContractError) -> None:
    report_contract_failure(
        logger,
        operation=operation,
        contract_name=error.contract_name,
        code=error.code,
        transport="http",
    )


class AgentApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        install_safe_http_client_logging()
        settings = get_settings()
        self.base_url = (base_url or settings.api_base_url).rstrip("/")
        self.http_client = http_client
        self._owns_http_client = http_client is None
        self.timeout = timeout if timeout is not None else settings.api_timeout_seconds
        self.max_retries = (
            max_retries if max_retries is not None else settings.api_max_retries
        )

    async def append_transcript(
        self,
        call_id: UUID,
        dispatch_token: str,
        segment: TranscriptSegment,
    ) -> TranscriptAppendAcknowledgement:
        if not dispatch_token:
            raise TranscriptAppendPermanentError("dispatch token is required")
        try:
            request = create_contract(TranscriptAppendRequest, segment=segment)
            request_payload = dump_contract(request)
        except ContractError as error:
            _log_contract_error(operation="append_transcript", error=error)
            raise TranscriptAppendContractError(
                "transcript append request contract failed"
            ) from None
        try:
            response = await self._get_http_client().post(
                f"{self.base_url}/api/agent/calls/{call_id}/transcript",
                json=request_payload,
                headers={"x-agent-token": dispatch_token},
            )
        except httpx.TransportError:
            raise TranscriptAppendRetryableError(
                "transcript append transport failure"
            ) from None
        if response.status_code in RETRYABLE_STATUS_CODES or response.status_code >= 500:
            raise TranscriptAppendRetryableError(
                f"transcript append retryable status={response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise TranscriptAppendPermanentError(
                f"transcript append permanent status={response.status_code}"
            )
        try:
            acknowledgement = parse_contract(
                TranscriptAppendAcknowledgement, response.content
            )
        except ContractError as error:
            _log_contract_error(operation="append_transcript", error=error)
            raise TranscriptAppendAcknowledgementError(
                "transcript append acknowledgement is malformed"
            ) from None
        if acknowledgement.sequence_number != segment.sequence_number:
            report_contract_failure(
                logger,
                operation="append_transcript",
                contract_name="TranscriptAppendAcknowledgement",
                code="correlation_mismatch",
                transport="http",
            )
            raise TranscriptAppendAcknowledgementError(
                "transcript append acknowledgement sequence mismatch"
            )
        return acknowledgement

    async def complete_verification(
        self,
        session_id: UUID,
        token: str,
    ) -> VerificationCompletionAcknowledgement:
        if not isinstance(token, str) or not token.strip():
            raise VerificationCompletionPermanentError(
                "verification completion credentials are required"
            )
        request = create_contract(VerificationCompletionRequest)
        url = f"{self.base_url}/api/activation/verification/{session_id}/complete"
        last_error: VerificationCompletionRetryableError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._get_http_client().post(
                    url,
                    json=dump_contract(request),
                    headers={"x-verification-token": token},
                )
            except httpx.TransportError:
                last_error = VerificationCompletionRetryableError(
                    "verification completion transport failure"
                )
                logger.warning("complete_verification retry classification=transport")
            else:
                if response.status_code in RETRYABLE_STATUS_CODES or 500 <= response.status_code < 600:
                    last_error = VerificationCompletionRetryableError(
                        f"verification completion retryable status={response.status_code}"
                    )
                    logger.warning("complete_verification retry classification=http")
                elif response.status_code < 200 or response.status_code >= 300:
                    raise VerificationCompletionPermanentError(
                        f"verification completion permanent status={response.status_code}"
                    )
                else:
                    try:
                        acknowledgement = parse_contract(
                            VerificationCompletionAcknowledgement, response.content
                        )
                    except ContractError as error:
                        _log_contract_error(operation="complete_verification", error=error)
                        raise VerificationCompletionAcknowledgementError(
                            "verification completion acknowledgement is malformed"
                        ) from None
                    if acknowledgement.session_id != session_id:
                        report_contract_failure(
                            logger,
                            operation="complete_verification",
                            contract_name="VerificationCompletionAcknowledgement",
                            code="correlation_mismatch",
                            transport="http",
                        )
                        raise VerificationCompletionAcknowledgementError(
                            "verification completion acknowledgement correlation mismatch"
                        )
                    return acknowledgement
            if attempt < self.max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
        if last_error is None:
            raise VerificationCompletionRetryableError(
                "verification completion exhausted without acknowledgement"
            )
        raise last_error

    async def complete_call(
        self,
        call_id: UUID,
        dispatch_token: str,
        request: CallCompletionRequest,
    ) -> CallCompletionAcknowledgement:
        if not dispatch_token:
            raise ValueError("Dispatch token is required")
        url = f"{self.base_url}/api/agent/calls/{call_id}/complete"
        try:
            request_payload = dump_contract(request)
        except ContractError as error:
            _log_contract_error(operation="complete_call", error=error)
            raise CallCompletionContractError(
                "call completion request contract failed"
            ) from None
        last_error: CallCompletionRetryableError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._get_http_client().post(
                    url,
                    json=request_payload,
                    headers={"x-agent-token": dispatch_token},
                )
            except httpx.TransportError as error:
                last_error = CallCompletionRetryableError(
                    "call completion transport failure"
                )
                logger.warning(
                    "complete_call retry classification=transport error_type=%s",
                    type(error).__name__,
                )
            else:
                if response.status_code in RETRYABLE_STATUS_CODES or response.status_code >= 500:
                    last_error = CallCompletionRetryableError(
                        f"call completion retryable status={response.status_code}"
                    )
                    logger.warning("complete_call retry classification=http")
                elif response.status_code < 200 or response.status_code >= 300:
                    raise ValueError(
                        f"call completion permanent status={response.status_code}"
                    )
                else:
                    try:
                        acknowledgement = parse_contract(
                            CallCompletionAcknowledgement, response.content
                        )
                    except ContractError as error:
                        _log_contract_error(operation="complete_call", error=error)
                        raise CallCompletionAcknowledgementError(
                            "call completion acknowledgement is malformed"
                        ) from None
                    if acknowledgement.job_id != f"call-finalization:{call_id}":
                        report_contract_failure(
                            logger,
                            operation="complete_call",
                            contract_name="CallCompletionAcknowledgement",
                            code="correlation_mismatch",
                            transport="http",
                        )
                        raise CallCompletionAcknowledgementError(
                            "call completion acknowledgement correlation mismatch"
                        )
                    return acknowledgement
            if attempt < self.max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
        if last_error is None:
            raise CallCompletionRetryableError(
                "call completion exhausted without acknowledgement"
            )
        raise last_error

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
