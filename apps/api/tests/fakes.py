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


class CaptureInstrument:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.measurements: list[tuple[float, dict]] = []

    def _write(self, value, attributes=None) -> None:
        if self.failure is not None:
            raise self.failure
        self.measurements.append((value, dict(attributes or {})))

    add = _write
    record = _write
    set = _write


class CaptureMeter:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.instruments: dict[str, CaptureInstrument] = {}

    def _create(self, name: str, **_kwargs) -> CaptureInstrument:
        instrument = CaptureInstrument(failure=self.failure)
        self.instruments[name] = instrument
        return instrument

    create_counter = _create
    create_histogram = _create
    create_gauge = _create


class CaptureSpan:
    def __init__(self, name: str, attributes: dict | None, *, kind=None) -> None:
        self.name = name
        self.attributes = dict(attributes or {})
        self.status = None
        self.kind = kind

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def set_attribute(self, key: str, value) -> None:
        self.attributes[key] = value

    def set_status(self, status) -> None:
        self.status = status


class CaptureTracer:
    def __init__(self) -> None:
        self.spans: list[CaptureSpan] = []

    def start_as_current_span(self, name: str, *, attributes=None, **kwargs):
        span = CaptureSpan(name, attributes, kind=kwargs.get("kind"))
        self.spans.append(span)
        return span


class MockArqPool:
    def __init__(self):
        self.enqueued_jobs = []
    
    async def enqueue_job(self, name, payload):
        self.enqueued_jobs.append((name, payload))
