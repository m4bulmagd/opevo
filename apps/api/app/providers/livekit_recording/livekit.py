from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, cast
from urllib.parse import urlsplit

from livekit import api

from app.core.observability import (
    Observability,
    instrument_provider,
)
from app.core.provider_failures import ProviderFailure, ProviderFailureClass
from app.providers.livekit_recording.base import (
    RecordingEgressResult,
    RecordingEgressSnapshot,
    RecordingProvider,
)
from app.providers.livekit_failures import (
    livekit_failure_from_exception,
    livekit_start_failure_context,
)


class _LocalStartValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EgressObjectKeyEvidence:
    state: Literal["absent", "exact", "invalid"]
    object_key: str | None = None


@dataclass(frozen=True)
class _EgressLookupRecord:
    egress_id: str
    status: int


_MISSING = object()
_ALIAS_CONFLICT = object()
_ALIAS_MAX_DEPTH = 12
_ALIAS_MAX_NODES = 256
_LIVEKIT_REPEATED_MAX_ITEMS = 64
_SAFE_ALIAS_LEAF_TYPES = frozenset({type(None), bool, int, float, str, bytes})
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
    value_type = type(left)
    if value_type is not type(right):
        return False
    if value_type not in _SAFE_ALIAS_LEAF_TYPES:
        return left is right
    return left == right


def _canonical_values_equivalent(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is _CanonicalLeaf:
        assert type(right) is _CanonicalLeaf
        return _raw_values_agree(left.value, right.value)
    if type(left) is _CanonicalSequence:
        assert type(right) is _CanonicalSequence
        left_items = left.items
        right_items = right.items
        return len(left_items) == len(right_items) and all(
            _canonical_values_equivalent(left_item, right_item)
            for left_item, right_item in zip(left_items, right_items, strict=True)
        )
    if type(left) is _CanonicalMapping:
        assert type(right) is _CanonicalMapping
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
    try:
        is_mapping = isinstance(value, Mapping)
    except Exception:
        return _ALIAS_CONFLICT
    if is_mapping:
        mapping_value = cast(Mapping, value)
        container_id = id(value)
        if container_id in traversal.active_containers:
            return _ALIAS_CONFLICT
        traversal.active_containers.add(container_id)
        try:
            normalized: list[tuple[object, object]] = []
            try:
                for key, item in mapping_value.items():
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
    try:
        is_sequence = isinstance(value, (list, tuple))
    except Exception:
        return _ALIAS_CONFLICT
    if is_sequence:
        sequence_value = cast(list[object] | tuple[object, ...], value)
        container_id = id(value)
        if container_id in traversal.active_containers:
            return _ALIAS_CONFLICT
        traversal.active_containers.add(container_id)
        try:
            normalized_items: list[object] = []
            try:
                for item in sequence_value:
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
    if descriptor is None:
        return True
    fields_by_name = getattr(descriptor, "fields_by_name")
    field = fields_by_name.get(name)
    if field is None:
        return True
    has_presence = getattr(field, "has_presence")
    if type(has_presence) is not bool:
        raise TypeError("Invalid protobuf field presence metadata")
    if not has_presence:
        return True
    has_field = getattr(value, "HasField")
    if not callable(has_field):
        raise TypeError("Invalid protobuf presence probe")
    presence = has_field(name)
    if type(presence) is not bool:
        raise TypeError("Invalid protobuf presence result")
    return presence


def _field(value: object, *names: str) -> object:
    candidates: list[object] = []
    is_mapping = isinstance(value, Mapping)
    if is_mapping:
        mapping_value = cast(Mapping, value)
        for name in names:
            try:
                candidate = mapping_value[name]
            except KeyError:
                continue
            candidates.append(candidate)
    else:
        for name in names:
            candidate = getattr(value, name, _MISSING)
            if candidate is _MISSING:
                continue
            if not livekit_field_is_present(value, name):
                continue
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


def _plain_provider_field(value: object, name: str) -> object:
    """Read a plain dict or SDK field without swallowing accessor defects."""
    if type(value) is dict:
        return value.get(name, _MISSING)
    if value is None or type(value) is object:
        return _MISSING

    instance_values = getattr(value, "__dict__", None)
    if type(instance_values) is dict and name not in instance_values:
        descriptor = getattr(type(value), name, _MISSING)
        if descriptor is _MISSING:
            return _MISSING
    return getattr(value, name)


def _plain_provider_items(response: object) -> tuple[object, ...] | None:
    items = _plain_provider_field(response, "items")
    if (
        items is _MISSING
        or items is None
        or not isinstance(items, Iterable)
        or isinstance(items, (str, bytes, Mapping))
    ):
        return None
    return tuple(items)


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
    if filename_value is not _MISSING and not (
        type(filename_value) is str and filename_value == ""
    ):
        return EgressObjectKeyEvidence("invalid")
    location_value = _field(file_info, "location")
    if location_value is _ALIAS_CONFLICT:
        return EgressObjectKeyEvidence("invalid")
    location = _bounded_nonempty_string(location_value, max_length=2048)
    if location is None:
        if location_value is not _MISSING and not (
            type(location_value) is str and location_value == ""
        ):
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
    iterator = iter(value)  # type: ignore[call-overload]
    for index, item in enumerate(iterator):
        if index == _LIVEKIT_REPEATED_MAX_ITEMS:
            return None
        items.append(item)
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
        observability: Observability,
    ) -> None:
        self.egress_client = egress_client
        self.bucket_name = bucket_name
        self.endpoint_url = endpoint_url.rstrip("/")
        self.access_key = access_key or ""
        self.secret_key = secret_key or ""
        self.region = region
        self.observability = observability

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
        if not room_name or not object_key:
            raise ProviderFailure(
                provider="livekit",
                operation="start_recording",
                disposition="terminal",
                error_class="validation",
                context={"start_outcome": "not_started"},
            )
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
        except (api.TwirpError, TimeoutError, ConnectionError, OSError) as error:
            raise livekit_failure_from_exception(
                error,
                operation="start_recording",
                context=livekit_start_failure_context(error),
            ) from error

        egress_id = _bounded_nonempty_string(
            getattr(info, "egress_id", None),
            max_length=255,
        )
        if egress_id is None:
            raise ProviderFailure(
                provider="livekit",
                operation="start_recording",
                disposition="terminal",
                error_class="validation",
                context={"start_outcome": "unknown"},
            )
        return RecordingEgressResult(
            egress_id=egress_id,
            object_key=object_key,
            url=f"{self.endpoint_url}/{self.bucket_name}/{object_key}",
        )

    @instrument_provider("livekit", "list_recording_egresses")
    async def list_room_egresses(
        self,
        *,
        room_name: str,
    ) -> tuple[RecordingEgressSnapshot, ...]:
        try:
            response = await self.egress_client.list_egress(
                api.ListEgressRequest(room_name=room_name)
            )
        except (api.TwirpError, TimeoutError, ConnectionError, OSError) as error:
            raise livekit_failure_from_exception(
                error,
                operation="list_recording_egresses",
            ) from error
        items_value = getattr(response, "items", _MISSING)
        if (
            items_value is _MISSING
            or items_value is None
            or not isinstance(items_value, Iterable)
            or isinstance(items_value, (str, bytes, Mapping))
        ):
            raise self._validation_failure("list_recording_egresses")
        items: tuple[object, ...] = tuple(items_value)

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
                raise self._validation_failure("list_recording_egresses")
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
        except (api.TwirpError, TimeoutError, ConnectionError, OSError) as error:
            raise livekit_failure_from_exception(
                error,
                operation="stop_recording",
            ) from error

    @instrument_provider("livekit", "ensure_recording_stopped")
    async def ensure_stopped(self, egress_id: str) -> None:
        await self._ensure_terminal_status(
            egress_id,
            accepted_terminal_statuses=self._SUCCESSFUL_TERMINAL_STATUSES,
            operation="ensure_recording_stopped",
        )

    @instrument_provider("livekit", "ensure_recording_not_running")
    async def ensure_not_running(self, egress_id: str) -> None:
        await self._ensure_terminal_status(
            egress_id,
            accepted_terminal_statuses=(
                self._SUCCESSFUL_TERMINAL_STATUSES | self._FAILED_TERMINAL_STATUSES
            ),
            operation="ensure_recording_not_running",
        )

    async def _ensure_terminal_status(
        self,
        egress_id: str,
        *,
        accepted_terminal_statuses: frozenset,
        operation: str,
    ) -> None:
        info = await self._get_egress(egress_id, operation=operation)
        if info is None:
            raise self._retryable_failure(operation, "unavailable")
        if info.status in accepted_terminal_statuses:
            return
        self._raise_for_failed_terminal(info.status, operation=operation)
        if info.status not in self._STOPPABLE_STATUSES:
            raise self._retryable_failure(operation, "unknown")

        await self._stop_room_recording(egress_id=egress_id)
        refreshed = await self._get_egress(egress_id, operation=operation)
        if refreshed is None:
            raise self._retryable_failure(operation, "unavailable")
        if refreshed.status in accepted_terminal_statuses:
            return
        self._raise_for_failed_terminal(refreshed.status, operation=operation)
        raise self._retryable_failure(operation, "unavailable")

    def _raise_for_failed_terminal(self, status, *, operation: str) -> None:
        if status in self._FAILED_TERMINAL_STATUSES:
            if status == api.EgressStatus.EGRESS_ABORTED:
                error_class: ProviderFailureClass = "conflict"
            elif status == api.EgressStatus.EGRESS_LIMIT_REACHED:
                error_class = "rate_limited"
            else:
                error_class = "unknown"
            raise ProviderFailure(
                provider="livekit",
                operation=operation,  # type: ignore[arg-type]
                disposition="terminal",
                error_class=error_class,
            )

    async def _get_egress(self, egress_id: str, *, operation: str):
        try:
            response = await self.egress_client.list_egress(
                api.ListEgressRequest(egress_id=egress_id)
            )
        except (api.TwirpError, TimeoutError, ConnectionError, OSError) as error:
            raise livekit_failure_from_exception(
                error,
                operation=operation,  # type: ignore[arg-type]
            ) from error
        items = _plain_provider_items(response)
        if items is None:
            raise self._validation_failure(operation)

        matches: list[_EgressLookupRecord] = []
        for item in items:
            item_egress_id = _bounded_nonempty_string(
                _plain_provider_field(item, "egress_id"),
                max_length=255,
            )
            status = _plain_provider_field(item, "status")
            if (
                item_egress_id is None
                or type(status) is not int
                or status not in range(7)
            ):
                raise self._validation_failure(operation)
            if item_egress_id == egress_id:
                matches.append(_EgressLookupRecord(item_egress_id, status))
        if not matches:
            return None
        if len(matches) != 1:
            raise ProviderFailure(
                provider="livekit",
                operation=operation,  # type: ignore[arg-type]
                disposition="terminal",
                error_class="conflict",
            )
        return matches[0]

    @staticmethod
    def _validation_failure(operation: str) -> ProviderFailure:
        return ProviderFailure(
            provider="livekit",
            operation=operation,  # type: ignore[arg-type]
            disposition="terminal",
            error_class="validation",
        )

    @staticmethod
    def _retryable_failure(operation: str, error_class: str) -> ProviderFailure:
        return ProviderFailure(
            provider="livekit",
            operation=operation,  # type: ignore[arg-type]
            disposition="retryable",
            error_class=error_class,  # type: ignore[arg-type]
        )
