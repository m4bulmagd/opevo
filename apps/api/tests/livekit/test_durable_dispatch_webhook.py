from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.call import Call
from app.models.recording_egress_operation import RecordingEgressOperation
from app.models.webhook_event import WebhookEvent
from app.providers.livekit_recording.base import RecordingEgressResult
from app.repositories.webhook_event_repository import WebhookEventRepository
from app.services.recording_lifecycle_service import RecordingLifecycleService
from app.webhooks import livekit as livekit_webhook_module
from app.webhooks.livekit import (
    convert_livekit_event,
    get_webhook_receiver,
    handle_livekit_webhook,
)


class _Request:
    headers = {"authorization": "Bearer signed"}

    def __init__(self, *, arq_pool=None, settings=None) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(arq_pool=arq_pool, settings=settings)
        )

    async def body(self) -> bytes:
        return b"{}"


class _Receiver:
    def __init__(self, event) -> None:
        self.event = event

    def receive(self, _body, _authorization):
        return self.event


class _SessionWithoutWrites:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Realtime:
    async def publish_call_started(self, *_args, **_kwargs) -> None:
        raise AssertionError("missing-id webhook must not run business logic")


class _Pool:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name: str, payload: dict) -> None:
        self.jobs.append((name, payload))
        if self.fail:
            raise RuntimeError("queue unavailable")


def _forbid_provider_and_storage_io(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls = {"recording": 0, "storage": 0}

    class ForbiddenRecordingService:
        def __init__(self, *_args, **_kwargs) -> None:
            calls["recording"] += 1
            raise AssertionError("egress webhook constructed recording provider")

    class ForbiddenStorageProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            calls["storage"] += 1
            raise AssertionError("egress webhook constructed storage provider")

    from app.providers.storage import s3 as storage_module
    from app.services import livekit_recording_service as recording_service_module
    from livekit import api as livekit_api_module

    monkeypatch.setattr(
        livekit_webhook_module,
        "LiveKitRecordingService",
        ForbiddenRecordingService,
    )
    monkeypatch.setattr(
        storage_module,
        "S3Storage",
        ForbiddenStorageProvider,
    )
    monkeypatch.setattr(
        recording_service_module,
        "LiveKitRecordingProvider",
        ForbiddenRecordingService,
    )
    monkeypatch.setattr(
        livekit_api_module,
        "LiveKitAPI",
        ForbiddenRecordingService,
    )
    return calls


async def _starting_recording(db_session, active_user):
    call = Call(
        user_id=active_user.id,
        status="connected",
        livekit_room_id="room-owned",
    )
    db_session.add(call)
    await db_session.flush()
    service = RecordingLifecycleService(db_session)
    operation = await service.prepare_start(call)
    assert await service.begin_start(operation.id) is not None
    await db_session.commit()
    return call, operation


def _egress_event(
    *,
    event_id: str = "EV_egress",
    event_type: str = "egress_started",
    egress_id: object = "EG_exact",
    room_name: object = "room-owned",
    egress_status: object = 1,
    object_key: str | None = None,
    location: str | None = None,
) -> dict:
    egress = {
        "egressId": egress_id,
        "roomName": room_name,
        "status": egress_status,
    }
    if object_key is not None:
        egress["roomComposite"] = {"file": {"filepath": object_key}}
    if location is not None:
        egress["fileResults"] = [{"location": location}]
    return {"id": event_id, "event": event_type, "egressInfo": egress}


def test_webhook_receiver_fallback_uses_app_bound_settings(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livekit import api as livekit_api_module

    configured = settings.model_copy(
        update={
            "livekit_api_key": "captured-livekit-key",
            "livekit_api_secret": "captured-livekit-secret",
        }
    )
    observed: dict[str, object] = {}

    class Verifier:
        def __init__(self, key: str, secret: str) -> None:
            observed["credentials"] = (key, secret)

    class Receiver:
        def __init__(self, verifier) -> None:
            observed["verifier"] = verifier

    monkeypatch.setattr(livekit_api_module, "TokenVerifier", Verifier)
    monkeypatch.setattr(livekit_api_module, "WebhookReceiver", Receiver)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=configured))
    )

    receiver = get_webhook_receiver(request)

    assert isinstance(receiver, Receiver)
    assert observed["credentials"] == (
        "captured-livekit-key",
        "captured-livekit-secret",
    )


def test_convert_dict_preserves_signed_event_id_and_normalizes_numeric_kind() -> None:
    assert convert_livekit_event(
        {
            "id": "EV_livekit_1",
            "event": "participant_joined",
            "room": {"name": "room-1"},
            "participant": {"identity": "caller", "kind": 3, "attributes": {}},
        }
    ) == {
        "id": "EV_livekit_1",
        "event": "participant_joined",
        "room": {"name": "room-1"},
        "participant": {"identity": "caller", "kind": "SIP", "attributes": {}},
    }


def test_convert_protoish_preserves_signed_event_id_and_agent_kind() -> None:
    event = SimpleNamespace(
        id="EV_livekit_2",
        event="participant_joined",
        room=SimpleNamespace(name="room-2"),
        participant=SimpleNamespace(
            identity="agent-call-1",
            kind=4,
            attributes={"untrusted": "kept for logic, never logged"},
        ),
    )

    assert convert_livekit_event(event) == {
        "id": "EV_livekit_2",
        "event": "participant_joined",
        "room": {"name": "room-2"},
        "participant": {
            "identity": "agent-call-1",
            "kind": "AGENT",
            "attributes": {"untrusted": "kept for logic, never logged"},
        },
    }


@pytest.mark.parametrize(
    "event",
    [
        {
            "id": "EV_egress_mapping",
            "event": "egress_started",
            "egressInfo": {
                "egressId": "EG_exact",
                "roomName": "room-owned",
                "status": 1,
                "roomComposite": {
                    "fileOutputs": [{"filepath": "calls/user-id/call-id.ogg"}]
                },
            },
        },
        SimpleNamespace(
            id="EV_egress_mapping",
            event="egress_started",
            egress_info=SimpleNamespace(
                egress_id="EG_exact",
                room_name="room-owned",
                status=1,
                file_results=[SimpleNamespace(filename="calls/user-id/call-id.ogg")],
            ),
        ),
    ],
)
def test_convert_egress_event_sanitizes_mapping_and_sdk_shapes(event) -> None:
    assert convert_livekit_event(event) == {
        "id": "EV_egress_mapping",
        "event": "egress_started",
        "egress": {
            "egress_id": "EG_exact",
            "room_name": "room-owned",
            "status": 1,
            "object_key": "calls/user-id/call-id.ogg",
        },
    }


def test_convert_mapping_missing_status_stays_missing() -> None:
    converted = convert_livekit_event(
        {
            "id": "EV_missing_status",
            "event": "egress_started",
            "egressInfo": {
                "egressId": "EG_exact",
                "roomName": "room-owned",
            },
        }
    )

    assert converted["egress"]["status"] is None


def test_convert_sdk_status_zero_is_valid_numeric_status() -> None:
    converted = convert_livekit_event(
        SimpleNamespace(
            id="EV_status_zero",
            event="egress_started",
            egress_info=SimpleNamespace(
                egress_id="EG_exact",
                room_name="room-owned",
                status=0,
            ),
        )
    )

    assert converted["egress"]["status"] == 0
    assert type(converted["egress"]["status"]) is int


@pytest.mark.parametrize(
    ("egress", "expected_object_key"),
    [
        ({"egressId": "EG", "roomName": "room", "status": 1}, None),
        (
            {
                "egress_id": "EG",
                "room_name": "room",
                "status": 1,
                "file_results": [
                    {"location": ("https://untrusted.example/recordings/call.ogg")}
                ],
            },
            None,
        ),
        (
            {
                "egressId": "EG",
                "roomName": "room",
                "status": 1,
                "roomComposite": {"file": {"filepath": "calls/user-id/call-id.ogg"}},
                "fileResults": [{"filename": "calls/user-id/different-call-id.ogg"}],
            },
            None,
        ),
    ],
)
def test_convert_egress_event_fails_closed_for_absent_or_unsafe_paths(
    egress,
    expected_object_key: str | None,
) -> None:
    converted = convert_livekit_event(
        {"id": "EV_path", "event": "egress_updated", "egress": egress}
    )

    assert converted == {
        "id": "EV_path",
        "event": "egress_updated",
        "egress": {
            "egress_id": "EG",
            "room_name": "room",
            "status": 1,
            "object_key": expected_object_key,
        },
    }


@pytest.mark.parametrize(
    "path_shape",
    [
        {"roomComposite": "not-an-object"},
        {"roomComposite": {"file": "not-an-object"}},
        {"roomComposite": {"file": {"filepath": None}}},
        {"fileResults": [None]},
        {"fileResults": ["not-an-object"]},
        {
            "room_composite": {"file": {"filepath": "calls/user/call.ogg"}},
            "roomComposite": {"file": {"filepath": "calls/user/other.ogg"}},
        },
    ],
)
def test_convert_egress_event_sanitizes_malformed_path_containers(
    path_shape: dict,
) -> None:
    converted = convert_livekit_event(
        {
            "id": "EV_malformed",
            "event": "egress_updated",
            "egressInfo": {
                "egressId": "EG_exact",
                "roomName": "room-owned",
                "status": 1,
                **path_shape,
            },
        }
    )

    assert converted["egress"]["object_key"] is None


def test_convert_egress_event_rejects_conflicting_identity_aliases() -> None:
    converted = convert_livekit_event(
        {
            "id": "EV_alias_conflict",
            "event": "egress_started",
            "egressInfo": {
                "egressId": "EG_first",
                "egress_id": "EG_second",
                "roomName": "room-first",
                "room_name": "room-second",
                "status": 1,
            },
        }
    )

    assert converted == {
        "id": "EV_alias_conflict",
        "event": "egress_started",
        "egress": {
            "egress_id": None,
            "room_name": None,
            "status": 1,
            "object_key": None,
        },
    }


def test_convert_egress_event_marks_conflicting_top_level_aliases_invalid() -> None:
    converted = livekit_webhook_module._convert_livekit_event(
        {
            "id": "EV_top_alias_conflict",
            "event": "egress_ended",
            "egressInfo": {
                "egressId": "EG_exact",
                "roomName": "room-owned",
                "status": 3,
            },
            "egress_info": {
                "egress_id": "EG_other",
                "room_name": "room-owned",
                "status": 3,
            },
        },
        bucket_name="recordings",
        endpoint_url="http://minio:9000",
    )

    assert converted.path_state == "invalid"
    assert converted.payload["egress"]["egress_id"] is None


def test_livekit_event_type_log_value_is_allow_listed() -> None:
    assert (
        livekit_webhook_module._safe_event_type("SENTINEL_PROVIDER_CONTROLLED_EVENT")
        == "unknown"
    )
    assert livekit_webhook_module._safe_event_type("participant_joined") == (
        "participant_joined"
    )
    assert livekit_webhook_module._safe_event_type("egress_ended") == ("egress_ended")


@pytest.mark.anyio
async def test_missing_event_id_fails_closed_without_commit_or_business_logic() -> None:
    session = _SessionWithoutWrites()
    response = await handle_livekit_webhook(
        _Request(),
        session=session,
        webhook_receiver=_Receiver(
            {
                "event": "participant_joined",
                "room": {"name": "room-1"},
                "participant": {"kind": "SIP", "attributes": {}},
            }
        ),
        realtime_service=_Realtime(),
    )

    assert response.status_code == 202
    assert session.commits == 0


@pytest.mark.anyio
async def test_duplicate_event_is_recorded_once_with_empty_payload(db_session) -> None:
    event = {
        "id": "EV_duplicate",
        "event": "room_finished",
        "room": {"name": "room-secret"},
        "participant": {"kind": "STANDARD", "attributes": {}},
    }

    first = await handle_livekit_webhook(
        _Request(),
        session=db_session,
        webhook_receiver=_Receiver(event),
        realtime_service=SimpleNamespace(),
    )
    second = await handle_livekit_webhook(
        _Request(),
        session=db_session,
        webhook_receiver=_Receiver(event),
        realtime_service=SimpleNamespace(),
    )

    assert first.status_code == second.status_code == 202
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
    stored = await db_session.scalar(select(WebhookEvent))
    assert stored is not None
    assert stored.provider == "livekit"
    assert stored.external_event_id == "EV_duplicate"
    assert stored.payload == {}


@pytest.mark.anyio
async def test_signed_exact_egress_is_atomic_empty_payload_and_wakes_once(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call, operation = await _starting_recording(db_session, active_user)
    pool = _Pool()
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)
    event = _egress_event(object_key=operation.expected_object_key)

    first = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(event),
        realtime_service=None,
    )
    second = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(event),
        realtime_service=None,
    )

    assert first.status_code == second.status_code == 202
    await db_session.refresh(call)
    await db_session.refresh(operation)
    assert operation.start_state == "started"
    assert operation.provider_egress_id == "EG_exact"
    assert call.recording_object_key == operation.expected_object_key
    assert call.recording_egress_id == "EG_exact"
    stored = await db_session.scalar(
        select(WebhookEvent).where(WebhookEvent.external_event_id == "EV_egress")
    )
    assert stored is not None
    assert stored.payload == {}
    assert pool.jobs == [("outbox_delivery_job", {})]
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event",
    [
        _egress_event(egress_id="", object_key="calls/ignored.ogg"),
        _egress_event(egress_id="bad\x00id", object_key="calls/ignored.ogg"),
        _egress_event(egress_id="E" * 256, object_key="calls/ignored.ogg"),
        _egress_event(egress_status=True, object_key="calls/ignored.ogg"),
        _egress_event(egress_status=7, object_key="calls/ignored.ogg"),
        _egress_event(room_name="wrong-room", object_key="calls/ignored.ogg"),
        _egress_event(object_key="calls/wrong.ogg"),
    ],
)
async def test_signed_missing_or_mismatched_egress_is_safe_and_still_acknowledged(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
    event: dict,
) -> None:
    call, operation = await _starting_recording(db_session, active_user)
    pool = _Pool()
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)

    response = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(event),
        realtime_service=None,
    )

    assert response.status_code == 202
    await db_session.refresh(call)
    await db_session.refresh(operation)
    assert operation.start_state == "starting"
    assert operation.provider_egress_id is None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    stored = await db_session.scalar(select(WebhookEvent))
    assert stored is not None
    assert stored.payload == {}
    assert pool.jobs == [("outbox_delivery_job", {})]
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path_shape",
    [
        {
            "fileResults": [
                {"location": "https://untrusted.example/recordings/call.ogg"}
            ]
        },
        {"roomComposite": "malformed"},
        {"roomComposite": {"file": "malformed"}},
        {"roomComposite": {"fileOutputs": [{"filepath": None}]}},
        {"fileResults": [None]},
        {"fileResults": ["malformed"]},
        {
            "room_composite": {"file": {"filepath": "calls/user/call.ogg"}},
            "roomComposite": {"file": {"filepath": "calls/user/other.ogg"}},
        },
    ],
)
async def test_signed_invalid_path_is_not_treated_as_absent_for_known_identity(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
    path_shape: dict,
) -> None:
    call, operation = await _starting_recording(db_session, active_user)
    service = RecordingLifecycleService(db_session)
    await service.record_start_success(
        operation.id,
        RecordingEgressResult(
            egress_id="EG_exact",
            object_key=operation.expected_object_key,
            url="s3://private/preserved",
        ),
    )
    await db_session.commit()
    pool = _Pool()
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)
    event = _egress_event(
        event_type="egress_ended",
        egress_status=3,
    )
    event["egressInfo"].update(path_shape)

    response = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(event),
        realtime_service=None,
    )

    assert response.status_code == 202
    await db_session.refresh(operation)
    assert operation.provider_terminal_at is None
    assert operation.last_error_code is None
    assert pool.jobs == [("outbox_delivery_job", {})]
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
async def test_signed_conflict_hides_projection_and_terminal_event_records_fact(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call, operation = await _starting_recording(db_session, active_user)
    service = RecordingLifecycleService(db_session)
    await service.record_start_success(
        operation.id,
        RecordingEgressResult(
            egress_id="EG_first",
            object_key=operation.expected_object_key,
            url="s3://private/hidden",
        ),
    )
    await db_session.commit()
    pool = _Pool()
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)

    conflict = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(
            _egress_event(
                event_id="EV_conflict",
                egress_id="EG_second",
                object_key=operation.expected_object_key,
            )
        ),
        realtime_service=None,
    )
    terminal = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(
            _egress_event(
                event_id="EV_terminal",
                event_type="egress_ended",
                egress_id="EG_first",
                egress_status=3,
            )
        ),
        realtime_service=None,
    )

    assert conflict.status_code == terminal.status_code == 202
    await db_session.refresh(call)
    await db_session.refresh(operation)
    assert operation.provider_egress_id == "EG_first"
    assert operation.last_error_code == "recording_identity_conflict"
    assert operation.provider_terminal_at is not None
    assert call.recording_object_key is None
    assert call.recording_egress_id is None
    assert call.recording_url is None
    stored = list((await db_session.scalars(select(WebhookEvent))).all())
    assert len(stored) == 2
    assert all(item.payload == {} for item in stored)
    assert pool.jobs == [
        ("outbox_delivery_job", {}),
        ("outbox_delivery_job", {}),
    ]
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
async def test_signed_egress_rolls_back_generic_and_business_mutation_together(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _call_row, operation = await _starting_recording(db_session, active_user)
    operation_id = operation.id
    expected_object_key = operation.expected_object_key
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)

    async def mutate_then_fail(self, _fact):
        stored_operation = await self.operation_repository.get_by_id(operation.id)
        assert stored_operation is not None
        stored_operation.last_error_code = "unavailable"
        await self.session.flush()
        raise RuntimeError("lifecycle failed")

    monkeypatch.setattr(
        RecordingLifecycleService,
        "accept_egress_event",
        mutate_then_fail,
    )

    with pytest.raises(RuntimeError, match="lifecycle failed"):
        await handle_livekit_webhook(
            _Request(arq_pool=_Pool(), settings=settings),
            session=db_session,
            webhook_receiver=_Receiver(_egress_event(object_key=expected_object_key)),
            realtime_service=None,
        )

    db_session.expire_all()
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored_operation is not None
    assert stored_operation.last_error_code is None
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
async def test_signed_egress_queue_failure_keeps_committed_sql_authoritative(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _call_row, operation = await _starting_recording(db_session, active_user)
    pool = _Pool(fail=True)
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)

    response = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(
            _egress_event(object_key=operation.expected_object_key)
        ),
        realtime_service=None,
    )

    assert response.status_code == 202
    await db_session.refresh(operation)
    assert operation.provider_egress_id == "EG_exact"
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 1
    assert pool.jobs == [("outbox_delivery_job", {})]
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
async def test_signed_egress_uses_app_bound_nondefault_storage_normalization(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call, operation = await _starting_recording(db_session, active_user)
    configured = settings.model_copy(
        update={
            "storage_bucket_name": "private-audio",
            "s3_endpoint_url": "https://objects.example/base",
        }
    )
    pool = _Pool()
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)

    response = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=configured),
        session=db_session,
        webhook_receiver=_Receiver(
            _egress_event(
                location=(
                    "https://objects.example/base/private-audio/"
                    f"{operation.expected_object_key}"
                )
            )
        ),
        realtime_service=None,
    )

    assert response.status_code == 202
    await db_session.refresh(call)
    await db_session.refresh(operation)
    assert operation.provider_egress_id == "EG_exact"
    assert call.recording_object_key == operation.expected_object_key
    assert pool.jobs == [("outbox_delivery_job", {})]
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
async def test_signed_egress_orders_generic_lifecycle_commit_then_wake(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _call_row, operation = await _starting_recording(db_session, active_user)
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)
    order: list[str] = []
    original_record = WebhookEventRepository.record_if_new
    original_accept = RecordingLifecycleService.accept_egress_event
    original_commit = db_session.commit

    async def ordered_record(repository, **kwargs):
        order.append("generic")
        return await original_record(repository, **kwargs)

    async def ordered_accept(service, fact):
        order.append("lifecycle")
        return await original_accept(service, fact)

    async def ordered_commit():
        order.append("commit")
        await original_commit()

    class OrderedPool(_Pool):
        async def enqueue_job(self, name: str, payload: dict) -> None:
            order.append("wake")
            await super().enqueue_job(name, payload)

    monkeypatch.setattr(WebhookEventRepository, "record_if_new", ordered_record)
    monkeypatch.setattr(
        RecordingLifecycleService,
        "accept_egress_event",
        ordered_accept,
    )
    monkeypatch.setattr(db_session, "commit", ordered_commit)
    pool = OrderedPool()

    response = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(
            _egress_event(object_key=operation.expected_object_key)
        ),
        realtime_service=None,
    )

    assert response.status_code == 202
    assert order == ["generic", "lifecycle", "commit", "wake"]
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
async def test_signed_egress_commit_failure_rolls_back_whole_transaction(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _call_row, operation = await _starting_recording(db_session, active_user)
    operation_id = operation.id
    expected_object_key = operation.expected_object_key
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)

    async def fail_commit() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        await handle_livekit_webhook(
            _Request(arq_pool=_Pool(), settings=settings),
            session=db_session,
            webhook_receiver=_Receiver(_egress_event(object_key=expected_object_key)),
            realtime_service=None,
        )

    db_session.expire_all()
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored_operation is not None
    assert stored_operation.start_state == "starting"
    assert stored_operation.provider_egress_id is None
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
async def test_signed_egress_lifecycle_flush_failure_rolls_back_whole_transaction(
    db_session,
    active_user,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _call_row, operation = await _starting_recording(db_session, active_user)
    operation_id = operation.id
    expected_object_key = operation.expected_object_key
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)
    original_flush = db_session.flush
    flush_count = 0

    async def fail_lifecycle_flush(*args, **kwargs) -> None:
        nonlocal flush_count
        flush_count += 1
        if flush_count == 2:
            raise RuntimeError("lifecycle flush failed")
        await original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", fail_lifecycle_flush)
    pool = _Pool()

    with pytest.raises(RuntimeError, match="lifecycle flush failed"):
        await handle_livekit_webhook(
            _Request(arq_pool=pool, settings=settings),
            session=db_session,
            webhook_receiver=_Receiver(_egress_event(object_key=expected_object_key)),
            realtime_service=None,
        )

    db_session.expire_all()
    stored_operation = await db_session.get(RecordingEgressOperation, operation_id)
    assert stored_operation is not None
    assert stored_operation.start_state == "starting"
    assert stored_operation.provider_egress_id is None
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert pool.jobs == []
    assert provider_calls == {"recording": 0, "storage": 0}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event",
    [
        {"id": "   ", "event": "room_finished"},
        {"id": "bad\x00id", "event": "room_finished"},
        {"id": "E" * 256, "event": "room_finished"},
        {"id": "EV_bad_type", "event": "bad\x00type"},
        {"id": "EV_bad_type", "event": "   "},
        {"id": "EV_bad_type", "event": "T" * 101},
    ],
)
async def test_unsafe_webhook_identity_is_rejected_without_write_or_wake(
    db_session,
    settings,
    monkeypatch: pytest.MonkeyPatch,
    event: dict,
) -> None:
    pool = _Pool()
    provider_calls = _forbid_provider_and_storage_io(monkeypatch)

    response = await handle_livekit_webhook(
        _Request(arq_pool=pool, settings=settings),
        session=db_session,
        webhook_receiver=_Receiver(event),
        realtime_service=None,
    )

    assert response.status_code == 202
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
    assert pool.jobs == []
    assert provider_calls == {"recording": 0, "storage": 0}
