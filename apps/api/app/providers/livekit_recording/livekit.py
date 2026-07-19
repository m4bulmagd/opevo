from enum import Enum, auto
from urllib.parse import urlsplit

from livekit import api

from app.core.observability import (
    get_observability,
    instrument_provider,
    validated_error_class,
)
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    RecordingEgressSnapshot,
    RecordingProvider,
    StartOutcome,
)


class _LocalStartValidationError(ValueError):
    pass


class _FileInfoPathState(Enum):
    ABSENT = auto()
    UNPROVABLE = auto()


def _nonempty_string(value: object) -> str | None:
    if type(value) is str and value:
        return value
    return None


def _location_object_key(
    location: str,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> str | None:
    try:
        parsed = urlsplit(location)
    except ValueError:
        return None
    if not parsed.scheme and not parsed.netloc:
        if (
            parsed.path != location
            or parsed.query
            or parsed.fragment
            or location.startswith("/")
        ):
            return None
        return location

    if parsed.scheme == "s3":
        if parsed.netloc != bucket_name or not parsed.path.startswith("/"):
            return None
        object_key = parsed.path[1:]
        return object_key if object_key and not object_key.startswith("/") else None

    try:
        endpoint = urlsplit(endpoint_url)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.scheme != endpoint.scheme
        or parsed.netloc != endpoint.netloc
    ):
        return None
    endpoint_path = endpoint.path.rstrip("/")
    object_prefix = f"{endpoint_path}/{bucket_name}/"
    if not parsed.path.startswith(object_prefix):
        return None
    object_key = parsed.path[len(object_prefix) :]
    return object_key or None


def _file_info_path(
    file_info: object,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> str | _FileInfoPathState:
    filename = _nonempty_string(getattr(file_info, "filename", None))
    if filename is not None:
        return filename
    location = _nonempty_string(getattr(file_info, "location", None))
    if location is None:
        return _FileInfoPathState.ABSENT
    object_key = _location_object_key(
        location,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
    )
    if object_key is None:
        return _FileInfoPathState.UNPROVABLE
    return object_key


def normalized_egress_object_key(
    egress: object,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> str | None:
    """Return one primitive output path, failing closed on disagreement."""
    paths: set[str] = set()

    room_composite = getattr(egress, "room_composite", None)
    if room_composite is not None:
        singular_output = getattr(room_composite, "file", None)
        singular_path = _nonempty_string(
            getattr(singular_output, "filepath", None)
        )
        if singular_path is not None:
            paths.add(singular_path)
        for output in getattr(room_composite, "file_outputs", ()):
            path = _nonempty_string(getattr(output, "filepath", None))
            if path is not None:
                paths.add(path)

    legacy_path = _file_info_path(
        getattr(egress, "file", None),
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
    )
    if legacy_path is _FileInfoPathState.UNPROVABLE:
        return None
    if isinstance(legacy_path, str):
        paths.add(legacy_path)
    for file_result in getattr(egress, "file_results", ()):
        file_result_path = _file_info_path(
            file_result,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
        )
        if file_result_path is _FileInfoPathState.UNPROVABLE:
            return None
        if isinstance(file_result_path, str):
            paths.add(file_result_path)

    if len(paths) != 1:
        return None
    return next(iter(paths))


class LiveKitRecordingProviderError(Exception):
    def __init__(
        self,
        category: str,
        *,
        error_class: str | None = None,
        start_outcome: StartOutcome = "unknown",
    ) -> None:
        if category not in {"provider_retryable", "provider_terminal"}:
            raise ValueError("Unsafe LiveKit recording provider category")
        if start_outcome not in {"not_started", "unknown"}:
            raise ValueError("Unsafe LiveKit recording start outcome")
        super().__init__(category)
        self.category = category
        self.retryable = category == "provider_retryable"
        self.error_class = validated_error_class(
            error_class or ("unavailable" if self.retryable else "unknown")
        )
        self._start_outcome: StartOutcome = start_outcome

    @property
    def start_outcome(self) -> StartOutcome:
        return self._start_outcome


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
        object_key: str,
    ) -> RecordingEgressResult:
        try:
            if not room_name or not object_key:
                raise _LocalStartValidationError(
                    "Recording room and object key are required"
                )
            request = api.RoomCompositeEgressRequest(
                room_name=room_name,
                audio_only=True,
                file=api.EncodedFileOutput(
                    filepath=object_key,
                    s3=self._build_s3_upload(),
                ),
            )
            info = await self.egress_client.start_room_composite_egress(request)
        except Exception as exc:  # pragma: no cover - exercised by tests via wrapping
            category, error_class = self._exception_details(exc)
            raise LiveKitRecordingProviderError(
                category,
                error_class=error_class,
                start_outcome=self.start_outcome_for(exc),
            ) from None

        egress_id = _nonempty_string(getattr(info, "egress_id", None))
        if egress_id is None:
            raise LiveKitRecordingProviderError(
                "provider_retryable",
                error_class="unknown",
                start_outcome="unknown",
            )
        return RecordingEgressResult(
            egress_id=egress_id,
            object_key=object_key,
            url=f"{self.endpoint_url}/{self.bucket_name}/{object_key}",
        )

    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        try:
            response = await self.egress_client.list_egress(
                api.ListEgressRequest(room_name=room_name)
            )
        except Exception as exc:
            category, error_class = self._exception_details(exc)
            raise LiveKitRecordingProviderError(
                category,
                error_class=error_class,
            ) from None

        try:
            items = tuple(response.items)
        except (AttributeError, TypeError):
            raise LiveKitRecordingProviderError(
                "provider_retryable",
                error_class="unknown",
            ) from None

        snapshots: list[RecordingEgressSnapshot] = []
        for item in items:
            egress_id = _nonempty_string(getattr(item, "egress_id", None))
            item_room_name = _nonempty_string(getattr(item, "room_name", None))
            status = getattr(item, "status", None)
            if (
                egress_id is None
                or item_room_name is None
                or isinstance(status, bool)
                or not isinstance(status, int)
            ):
                raise LiveKitRecordingProviderError(
                    "provider_retryable",
                    error_class="unknown",
                )
            snapshots.append(
                RecordingEgressSnapshot(
                    egress_id=egress_id,
                    room_name=item_room_name,
                    status=int(status),
                    object_key=normalized_egress_object_key(
                        item,
                        bucket_name=self.bucket_name,
                        endpoint_url=self.endpoint_url,
                    ),
                )
            )
        return tuple(snapshots)

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
        await self._ensure_terminal_status(
            egress_id,
            accepted_terminal_statuses=self._SUCCESSFUL_TERMINAL_STATUSES,
        )

    @instrument_provider("livekit", "ensure_recording_not_running")
    async def ensure_not_running(self, egress_id: str) -> None:
        await self._ensure_terminal_status(
            egress_id,
            accepted_terminal_statuses=(
                self._SUCCESSFUL_TERMINAL_STATUSES
                | self._FAILED_TERMINAL_STATUSES
            ),
        )

    async def _ensure_terminal_status(
        self,
        egress_id: str,
        *,
        accepted_terminal_statuses: frozenset,
    ) -> None:
        info = await self._get_egress(egress_id)
        if info is None:
            raise LiveKitRecordingProviderError(
                "provider_retryable",
                error_class="unavailable",
            )
        if info.status in accepted_terminal_statuses:
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
        if refreshed.status in accepted_terminal_statuses:
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
        if isinstance(error, _LocalStartValidationError):
            return "provider_terminal", "validation"
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

    @staticmethod
    def start_outcome_for(error: Exception) -> StartOutcome:
        if isinstance(error, _LocalStartValidationError):
            return "not_started"
        if not isinstance(error, api.TwirpError):
            return "unknown"
        if error.code in {
            "invalid_argument",
            "not_found",
            "failed_precondition",
            "out_of_range",
            "unimplemented",
            "unauthenticated",
            "permission_denied",
        }:
            return "not_started"
        if error.status in {400, 401, 403, 404, 405, 415, 422}:
            return "not_started"
        return "unknown"
