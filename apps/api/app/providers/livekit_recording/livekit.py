from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
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


@dataclass(frozen=True)
class EgressObjectKeyEvidence:
    state: Literal["absent", "exact", "invalid"]
    object_key: str | None = None


_MISSING = object()
_ALIAS_CONFLICT = object()
_ALIAS_MAX_DEPTH = 12
_ALIAS_MAX_NODES = 256
_LIVEKIT_REPEATED_MAX_ITEMS = 64
_LIVEKIT_ALIAS_NAMES = {
    "egressInfo": "egress_info",
    "egressId": "egress_id",
    "roomName": "room_name",
    "roomComposite": "room_composite",
    "fileOutputs": "file_outputs",
    "fileResults": "file_results",
}


@dataclass(frozen=True)
class _CanonicalLeaf:
    value: object


@dataclass(frozen=True)
class _CanonicalSequence:
    items: tuple[object, ...]


@dataclass(frozen=True)
class _CanonicalMapping:
    items: tuple[tuple[object, object], ...]


class _AliasTraversal:
    def __init__(self) -> None:
        self.remaining_nodes = _ALIAS_MAX_NODES
        self.active_containers: set[int] = set()

    def consume(self, *, depth: int) -> bool:
        if depth > _ALIAS_MAX_DEPTH or self.remaining_nodes == 0:
            return False
        self.remaining_nodes -= 1
        return True


def _raw_values_agree(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    try:
        result = left == right
    except Exception:
        return False
    return type(result) is bool and result


def _canonical_values_equivalent(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, _CanonicalLeaf) and isinstance(right, _CanonicalLeaf):
        return _raw_values_agree(left.value, right.value)
    if isinstance(left, _CanonicalSequence) and isinstance(
        right, _CanonicalSequence
    ):
        left_items = left.items
        right_items = right.items
        return len(left_items) == len(right_items) and all(
            _canonical_values_equivalent(left_item, right_item)
            for left_item, right_item in zip(left_items, right_items, strict=True)
        )
    if isinstance(left, _CanonicalMapping) and isinstance(right, _CanonicalMapping):
        left_items = left.items
        right_items = right.items
        if len(left_items) != len(right_items):
            return False
        matched_right_indexes: set[int] = set()
        for left_key, left_value in left_items:
            for index, (right_key, right_value) in enumerate(right_items):
                if index in matched_right_indexes or not _canonical_values_equivalent(
                    left_key, right_key
                ):
                    continue
                if not _canonical_values_equivalent(left_value, right_value):
                    return False
                matched_right_indexes.add(index)
                break
            else:
                return False
        return True
    return False


def _canonical_alias_value(
    value: object,
    *,
    traversal: _AliasTraversal,
    depth: int,
) -> object:
    if not traversal.consume(depth=depth):
        return _ALIAS_CONFLICT
    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in traversal.active_containers:
            return _ALIAS_CONFLICT
        traversal.active_containers.add(container_id)
        try:
            normalized: list[tuple[object, object]] = []
            try:
                for key, item in value.items():
                    canonical_key_value = (
                        _LIVEKIT_ALIAS_NAMES.get(key, key)
                        if type(key) is str
                        else key
                    )
                    canonical_key = _canonical_alias_value(
                        canonical_key_value,
                        traversal=traversal,
                        depth=depth + 1,
                    )
                    if canonical_key is _ALIAS_CONFLICT:
                        return _ALIAS_CONFLICT
                    canonical_item = _canonical_alias_value(
                        item,
                        traversal=traversal,
                        depth=depth + 1,
                    )
                    if canonical_item is _ALIAS_CONFLICT:
                        return _ALIAS_CONFLICT
                    for existing_key, existing_item in normalized:
                        if not _canonical_values_equivalent(
                            existing_key, canonical_key
                        ):
                            continue
                        if not _canonical_values_equivalent(
                            existing_item, canonical_item
                        ):
                            return _ALIAS_CONFLICT
                        break
                    else:
                        normalized.append((canonical_key, canonical_item))
            except Exception:
                return _ALIAS_CONFLICT
            return _CanonicalMapping(tuple(normalized))
        finally:
            traversal.active_containers.remove(container_id)
    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in traversal.active_containers:
            return _ALIAS_CONFLICT
        traversal.active_containers.add(container_id)
        try:
            normalized_items: list[object] = []
            try:
                for item in value:
                    canonical_item = _canonical_alias_value(
                        item,
                        traversal=traversal,
                        depth=depth + 1,
                    )
                    if canonical_item is _ALIAS_CONFLICT:
                        return _ALIAS_CONFLICT
                    normalized_items.append(canonical_item)
            except Exception:
                return _ALIAS_CONFLICT
        finally:
            traversal.active_containers.remove(container_id)
        return _CanonicalSequence(tuple(normalized_items))
    return _CanonicalLeaf(value)


def livekit_alias_values_equivalent(left: object, right: object) -> bool:
    """Compare Presvo's bounded camel/snake vocabulary structurally."""
    traversal = _AliasTraversal()
    canonical_left = _canonical_alias_value(left, traversal=traversal, depth=0)
    canonical_right = _canonical_alias_value(right, traversal=traversal, depth=0)
    if canonical_left is _ALIAS_CONFLICT or canonical_right is _ALIAS_CONFLICT:
        return False
    return _canonical_values_equivalent(canonical_left, canonical_right)


def livekit_field_is_present(value: object, name: str) -> bool:
    """Honor protobuf message presence without probing proto3 scalars."""
    descriptor = getattr(value, "DESCRIPTOR", None)
    fields_by_name = getattr(descriptor, "fields_by_name", None)
    field = fields_by_name.get(name) if fields_by_name is not None else None
    if field is None or not getattr(field, "has_presence", False):
        return True
    has_field = getattr(value, "HasField", None)
    if not callable(has_field):
        return False
    try:
        presence = has_field(name)
    except (TypeError, ValueError):
        return False
    return type(presence) is bool and presence


def _field(value: object, *names: str) -> object:
    if isinstance(value, Mapping):
        candidates = [value[name] for name in names if name in value]
    else:
        candidates = []
        for name in names:
            candidate = getattr(value, name, _MISSING)
            if candidate is not _MISSING and livekit_field_is_present(value, name):
                candidates.append(candidate)
    if not candidates:
        return _MISSING
    first = candidates[0]
    if any(
        not livekit_alias_values_equivalent(first, candidate)
        for candidate in candidates[1:]
    ):
        return _ALIAS_CONFLICT
    return first


def _nonempty_string(value: object) -> str | None:
    if type(value) is str and value:
        return value
    return None


def _bounded_nonempty_string(value: object, *, max_length: int) -> str | None:
    candidate = _nonempty_string(value)
    if (
        candidate is None
        or not candidate.strip()
        or len(candidate) > max_length
        or "\x00" in candidate
    ):
        return None
    return candidate


def _is_record(value: object) -> bool:
    return isinstance(value, Mapping) or not isinstance(
        value,
        (str, bytes, int, float, bool, list, tuple, set, frozenset),
    )


def _location_object_key(
    location: str,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> str | None:
    if "\x00" in location:
        return None
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
        return location if len(location) <= 512 else None

    if parsed.scheme == "s3":
        if parsed.netloc != bucket_name or not parsed.path.startswith("/"):
            return None
        object_key = parsed.path[1:]
        return (
            object_key
            if object_key
            and len(object_key) <= 512
            and not object_key.startswith("/")
            and "\x00" not in object_key
            else None
        )

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
    return (
        object_key
        if object_key and len(object_key) <= 512 and "\x00" not in object_key
        else None
    )


def _file_info_path_evidence(
    file_info: object,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> EgressObjectKeyEvidence:
    if file_info is _MISSING or file_info is None:
        return EgressObjectKeyEvidence("absent" if file_info is _MISSING else "invalid")
    if file_info is _ALIAS_CONFLICT or not _is_record(file_info):
        return EgressObjectKeyEvidence("invalid")
    filename_value = _field(file_info, "filename")
    if filename_value is _ALIAS_CONFLICT:
        return EgressObjectKeyEvidence("invalid")
    filename = _bounded_nonempty_string(filename_value, max_length=512)
    if filename is not None:
        return EgressObjectKeyEvidence("exact", filename)
    if filename_value is not _MISSING and filename_value != "":
        return EgressObjectKeyEvidence("invalid")
    location_value = _field(file_info, "location")
    if location_value is _ALIAS_CONFLICT:
        return EgressObjectKeyEvidence("invalid")
    location = _bounded_nonempty_string(location_value, max_length=2048)
    if location is None:
        if location_value is not _MISSING and location_value != "":
            return EgressObjectKeyEvidence("invalid")
        return EgressObjectKeyEvidence("invalid")
    object_key = _location_object_key(
        location,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
    )
    if object_key is None:
        return EgressObjectKeyEvidence("invalid")
    return EgressObjectKeyEvidence("exact", object_key)


def _items(value: object) -> tuple[object, ...] | None:
    if value is _MISSING:
        return ()
    if value is None or value is _ALIAS_CONFLICT:
        return None
    if isinstance(value, (str, bytes, Mapping)):
        return None
    items: list[object] = []
    try:
        iterator = iter(value)  # type: ignore[call-overload]
        for index, item in enumerate(iterator):
            if index == _LIVEKIT_REPEATED_MAX_ITEMS:
                return None
            items.append(item)
    except Exception:
        return None
    return tuple(items)


def normalized_egress_object_key_evidence(
    egress: object,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> EgressObjectKeyEvidence:
    """Return exact, absent, or invalid path evidence without provider objects."""
    paths: set[str] = set()

    if egress is _ALIAS_CONFLICT or not _is_record(egress):
        return EgressObjectKeyEvidence("invalid")

    room_composite = _field(egress, "room_composite", "roomComposite")
    if room_composite is _ALIAS_CONFLICT:
        return EgressObjectKeyEvidence("invalid")
    if room_composite is not _MISSING and room_composite is not None:
        if not _is_record(room_composite):
            return EgressObjectKeyEvidence("invalid")
        singular_output = _field(room_composite, "file")
        if singular_output is _ALIAS_CONFLICT:
            return EgressObjectKeyEvidence("invalid")
        if singular_output is not _MISSING and (
            singular_output is None or not _is_record(singular_output)
        ):
            return EgressObjectKeyEvidence("invalid")
        singular_path_value = _field(singular_output, "filepath")
        if singular_path_value is _ALIAS_CONFLICT:
            return EgressObjectKeyEvidence("invalid")
        singular_path = _bounded_nonempty_string(
            singular_path_value,
            max_length=512,
        )
        if singular_path is not None:
            paths.add(singular_path)
        elif singular_output is not _MISSING:
            return EgressObjectKeyEvidence("invalid")

        outputs = _items(_field(room_composite, "file_outputs", "fileOutputs"))
        if outputs is None:
            return EgressObjectKeyEvidence("invalid")
        if singular_output is _MISSING and not outputs:
            return EgressObjectKeyEvidence("invalid")
        for output in outputs:
            if output is None or not _is_record(output):
                return EgressObjectKeyEvidence("invalid")
            path_value = _field(output, "filepath")
            if path_value is _ALIAS_CONFLICT:
                return EgressObjectKeyEvidence("invalid")
            path = _bounded_nonempty_string(path_value, max_length=512)
            if path is not None:
                paths.add(path)
            else:
                return EgressObjectKeyEvidence("invalid")
    elif room_composite is not _MISSING:
        return EgressObjectKeyEvidence("invalid")

    legacy_evidence = _file_info_path_evidence(
        _field(egress, "file"),
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
    )
    if legacy_evidence.state == "invalid":
        return legacy_evidence
    if legacy_evidence.object_key is not None:
        paths.add(legacy_evidence.object_key)

    results = _items(_field(egress, "file_results", "fileResults"))
    if results is None:
        return EgressObjectKeyEvidence("invalid")
    for file_result in results:
        result_evidence = _file_info_path_evidence(
            file_result,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
        )
        if result_evidence.state == "invalid":
            return result_evidence
        if result_evidence.object_key is not None:
            paths.add(result_evidence.object_key)

    if not paths:
        return EgressObjectKeyEvidence("absent")
    if len(paths) != 1:
        return EgressObjectKeyEvidence("invalid")
    return EgressObjectKeyEvidence("exact", next(iter(paths)))


def normalized_egress_object_key(
    egress: object,
    *,
    bucket_name: str,
    endpoint_url: str,
) -> str | None:
    """Return one primitive output path, failing closed on disagreement."""
    return normalized_egress_object_key_evidence(
        egress,
        bucket_name=bucket_name,
        endpoint_url=endpoint_url,
    ).object_key


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

        egress_id = _bounded_nonempty_string(
            getattr(info, "egress_id", None),
            max_length=255,
        )
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
            egress_id = _bounded_nonempty_string(
                _field(item, "egress_id", "egressId"),
                max_length=255,
            )
            item_room_name = _bounded_nonempty_string(
                _field(item, "room_name", "roomName"),
                max_length=255,
            )
            status = _field(item, "status")
            if (
                egress_id is None
                or item_room_name is None
                or type(status) is not int
                or status not in range(7)
            ):
                raise LiveKitRecordingProviderError(
                    "provider_retryable",
                    error_class="unknown",
                )
            snapshots.append(
                RecordingEgressSnapshot(
                    egress_id=egress_id,
                    room_name=item_room_name,
                    status=status,
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
                self._SUCCESSFUL_TERMINAL_STATUSES | self._FAILED_TERMINAL_STATUSES
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
