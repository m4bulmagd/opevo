import asyncio
import logging

import httpx

from agent.config import get_settings


logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {502, 503, 504}


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
        self.agent_token = agent_token or settings.agent_internal_api_token
        self.http_client = http_client
        self.timeout = timeout if timeout is not None else settings.api_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.api_max_retries

    async def complete_call(self, payload: dict) -> dict:
        if not self.agent_token:
            raise ValueError("AGENT_INTERNAL_API_TOKEN is required")

        url = f"{self.base_url}/api/agent/calls/{payload['call_id']}/complete"
        token = payload.get("dispatch_token") or self.agent_token
        headers = {"x-agent-token": token}
        body = {
            "duration_seconds": payload["duration_seconds"],
            "transcript": payload.get("transcript") or [],
            "caller_number": payload.get("caller_number"),
        }

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.http_client is not None:
                    response = await self.http_client.post(url, json=body, headers=headers)
                    response.raise_for_status()
                    return response.json()

                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, json=body, headers=headers)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in RETRYABLE_STATUS_CODES:
                    raise
                logger.warning(
                    "complete_call attempt %d/%d failed with status %d",
                    attempt, self.max_retries, exc.response.status_code,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                logger.warning(
                    "complete_call attempt %d/%d failed: %s",
                    attempt, self.max_retries, exc,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))

        raise last_exc
