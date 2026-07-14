from livekit import api

from app.core.observability import (
    get_observability,
    instrument_provider,
    validated_error_class,
)
from app.providers.livekit_recording.base import RecordingEgressResult
from app.providers.livekit_recording.base import RecordingProvider


class LiveKitRecordingProviderError(Exception):
    def __init__(
        self,
        category: str,
        *,
        error_class: str | None = None,
    ) -> None:
        if category not in {"provider_retryable", "provider_terminal"}:
            raise ValueError("Unsafe LiveKit recording provider category")
        super().__init__(category)
        self.category = category
        self.retryable = category == "provider_retryable"
        self.error_class = validated_error_class(
            error_class or ("unavailable" if self.retryable else "unknown")
        )


class LiveKitRecordingProvider(RecordingProvider):
    _SUCCESSFUL_TERMINAL_STATUSES = frozenset(
        {
            api.EgressStatus.EGRESS_COMPLETE,
        }
    )
    _FAILED_TERMINAL_STATUSES = frozenset(
        {
            api.EgressStatus.EGRESS_FAILED,
            api.EgressStatus.EGRESS_ABORTED,
            api.EgressStatus.EGRESS_LIMIT_REACHED,
        }
    )
    _STOPPABLE_STATUSES = frozenset(
        {
            api.EgressStatus.EGRESS_STARTING,
            api.EgressStatus.EGRESS_ACTIVE,
            api.EgressStatus.EGRESS_ENDING,
        }
    )

    def __init__(
        self,
        *,
        egress_client,
        bucket_name: str,
        endpoint_url: str,
        access_key: str | None,
        secret_key: str | None,
        region: str,
        observability=None,
    ) -> None:
        self.egress_client = egress_client
        self.bucket_name = bucket_name
        self.endpoint_url = endpoint_url.rstrip("/")
        self.access_key = access_key or ""
        self.secret_key = secret_key or ""
        self.region = region
        self.observability = observability or get_observability()

    def _is_aws_endpoint(self) -> bool:
        return "amazonaws.com" in self.endpoint_url

    def _build_s3_upload(self):
        upload = api.S3Upload(
            bucket=self.bucket_name,
            access_key=self.access_key,
            secret=self.secret_key,
            region=self.region,
            force_path_style=not self._is_aws_endpoint(),
        )
        if not self._is_aws_endpoint():
            upload.endpoint = self.endpoint_url
        return upload

    @instrument_provider("livekit", "start_recording")
    async def start_room_recording(
        self,
        *,
        room_name: str,
        user_id: str,
        call_id: str,
    ) -> RecordingEgressResult:
        object_key = f"calls/{user_id}/{call_id}.ogg"
        request = api.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=True,
            file=api.EncodedFileOutput(
                filepath=object_key,
                s3=self._build_s3_upload(),
            ),
        )
        try:
            info = await self.egress_client.start_room_composite_egress(request)
        except Exception as exc:  # pragma: no cover - exercised by tests via wrapping
            category, error_class = self._exception_details(exc)
            raise LiveKitRecordingProviderError(
                category,
                error_class=error_class,
            ) from None

        return RecordingEgressResult(
            egress_id=info.egress_id,
            object_key=object_key,
            url=f"{self.endpoint_url}/{self.bucket_name}/{object_key}",
        )

    @instrument_provider("livekit", "stop_recording")
    async def stop_room_recording(self, *, egress_id: str) -> None:
        await self._stop_room_recording(egress_id=egress_id)

    async def _stop_room_recording(self, *, egress_id: str) -> None:
        request = api.StopEgressRequest(egress_id=egress_id)
        try:
            await self.egress_client.stop_egress(request)
        except Exception as exc:  # pragma: no cover - exercised by tests via wrapping
            category, error_class = self._exception_details(exc)
            raise LiveKitRecordingProviderError(
                category,
                error_class=error_class,
            ) from None

    @instrument_provider("livekit", "ensure_recording_stopped")
    async def ensure_stopped(self, egress_id: str) -> None:
        info = await self._get_egress(egress_id)
        if info is None:
            raise LiveKitRecordingProviderError(
                "provider_retryable",
                error_class="unavailable",
            )
        if info.status in self._SUCCESSFUL_TERMINAL_STATUSES:
            return
        self._raise_for_failed_terminal(info.status)
        if info.status not in self._STOPPABLE_STATUSES:
            raise LiveKitRecordingProviderError(
                "provider_retryable",
                error_class="unknown",
            )

        await self._stop_room_recording(egress_id=egress_id)
        refreshed = await self._get_egress(egress_id)
        if refreshed is None:
            raise LiveKitRecordingProviderError(
                "provider_retryable",
                error_class="unavailable",
            )
        if refreshed.status in self._SUCCESSFUL_TERMINAL_STATUSES:
            return
        self._raise_for_failed_terminal(refreshed.status)
        raise LiveKitRecordingProviderError(
            "provider_retryable",
            error_class="unavailable",
        )

    def _raise_for_failed_terminal(self, status) -> None:
        if status in self._FAILED_TERMINAL_STATUSES:
            error_class = {
                api.EgressStatus.EGRESS_ABORTED: "conflict",
                api.EgressStatus.EGRESS_LIMIT_REACHED: "rate_limited",
            }.get(status, "unknown")
            raise LiveKitRecordingProviderError(
                "provider_terminal",
                error_class=error_class,
            )

    async def _get_egress(self, egress_id: str):
        try:
            response = await self.egress_client.list_egress(
                api.ListEgressRequest(egress_id=egress_id)
            )
        except Exception as exc:
            category, error_class = self._exception_details(exc)
            raise LiveKitRecordingProviderError(
                category,
                error_class=error_class,
            ) from None
        matches = [
            item
            for item in response.items
            if getattr(item, "egress_id", None) == egress_id
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise LiveKitRecordingProviderError(
                "provider_retryable",
                error_class="conflict",
            )
        return matches[0]

    @staticmethod
    def _exception_details(error: Exception) -> tuple[str, str]:
        if isinstance(error, TimeoutError):
            return "provider_retryable", "timeout"
        if isinstance(error, api.TwirpError):
            code = error.code
            status = error.status
            if code == "deadline_exceeded" or status in {408, 504}:
                return "provider_retryable", "timeout"
            if code == "resource_exhausted" or status == 429:
                return "provider_retryable", "rate_limited"
            if code in {"unavailable", "internal", "data_loss"} or status >= 500:
                return "provider_retryable", "unavailable"
            if code in {"unauthenticated", "permission_denied"} or status in {
                401,
                403,
            }:
                return "provider_terminal", "authentication"
            if code in {"already_exists", "aborted"} or status == 409:
                return "provider_terminal", "conflict"
            if code in {
                "invalid_argument",
                "not_found",
                "failed_precondition",
                "out_of_range",
                "unimplemented",
            } or status in {400, 404, 405, 415, 422}:
                return "provider_terminal", "validation"
            return "provider_retryable", "unknown"
        if isinstance(error, (ConnectionError, OSError)):
            return "provider_retryable", "unavailable"
        return "provider_retryable", "unknown"
