import httpx

from agent.config import get_settings


class AgentApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        agent_token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.api_base_url).rstrip("/")
        self.agent_token = agent_token or settings.agent_internal_api_token
        self.http_client = http_client

    async def complete_call(self, payload: dict) -> dict:
        if not self.agent_token:
            raise ValueError("AGENT_INTERNAL_API_TOKEN is required")

        url = f"{self.base_url}/api/agent/calls/{payload['call_id']}/complete"
        headers = {"x-agent-token": self.agent_token}
        body = {
            "user_id": payload["user_id"],
            "duration_seconds": payload["duration_seconds"],
            "minutes_remaining": payload["minutes_remaining"],
            "transcript": payload.get("transcript") or [],
            "caller_number": payload.get("caller_number"),
        }

        if self.http_client is not None:
            response = await self.http_client.post(url, json=body, headers=headers)
            response.raise_for_status()
            return response.json()

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
            return response.json()
