from types import SimpleNamespace

from app.providers.telephony.base import TelephonyProvisioningReviewRequired


class FakeCustomerReadinessService:
    def __init__(self, *, serving: bool) -> None:
        self.serving = serving

    async def evaluate(self, _user_id) -> SimpleNamespace:
        return SimpleNamespace(result=SimpleNamespace(can_route=self.serving))


class FakeTelephonyProvider:
    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        return {
            "e164": "+33123456789",
            "provider_number_id": "pn_123",
            "provider_connection_name": "app-active",
        }

    async def enable_number(self, *, provider_number_id: str) -> str:
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class ReviewRequiredTelephonyProvider:
    async def provision_number(
        self,
        *,
        country_code: str,
        operation_key: str | None = None,
    ) -> dict:
        raise TelephonyProvisioningReviewRequired(
            reason="no_affordable_number",
            payload={
                "event": "phone_number_provisioning_review_required",
                "country_code": country_code,
                "contact_support": True,
            },
        )

    async def enable_number(self, *, provider_number_id: str) -> str:
        return "app-active"

    async def disable_number(self, *, provider_number_id: str) -> str:
        return "app-disabled"


class FakeStorageProvider:
    def __init__(self, download_path: str = ""):
        self.download_path = download_path

    async def download(self, key: str, target_path: str) -> str:
        with open(target_path, "wb") as f:
            f.write(b"fake data")
        return target_path

    async def delete(self, key: str) -> None:
        pass


class FakeSummaryService:
    async def generate_summary(self, transcript: list) -> str:
        return "- Simulated task done\n- Simulated topic discussed"


class MockArqPool:
    def __init__(self):
        self.enqueued_jobs = []
    
    async def enqueue_job(self, name, payload):
        self.enqueued_jobs.append((name, payload))
