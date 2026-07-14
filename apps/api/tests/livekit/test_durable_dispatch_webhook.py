from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.webhook_event import WebhookEvent
from app.webhooks.livekit import (
    convert_livekit_event,
    get_webhook_receiver,
    handle_livekit_webhook,
)


class _Request:
    headers = {"authorization": "Bearer signed"}

    def __init__(self, *, arq_pool=None) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(arq_pool=arq_pool))

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
