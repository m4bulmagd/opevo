"""
WebSocket lifecycle integration tests.

These tests use Starlette's synchronous TestClient to drive real WebSocket
connections through the FastAPI application.

Design constraints
------------------
* TestClient runs the ASGI app in a background thread with its own anyio event
  loop.  Using ``with TestClient(app):`` from inside an already-running anyio
  loop (i.e. from a @pytest.mark.anyio test) deadlocks because anyio refuses
  to run nested loops.
* The existing ``test_app`` fixture is async (pytest_asyncio), so we cannot
  use it directly from synchronous tests.
* Solution: a dedicated *synchronous* ``ws_app`` fixture (plain
  ``@pytest.fixture``) installs an on-demand async session override. The
  override creates and disposes its SQLite engine inside TestClient's event
  loop, while the fixture installs a fake RealtimeService directly on app
  state. TestClient is used *without* the ``with TestClient(app):`` context
  manager so lifespan does not replace those focused test resources.

WebSocket connections are keyed by the provider-neutral internal user UUID.
"""

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path
import threading
from uuid import UUID

import pytest
from conftest import install_test_api_runtime
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect as StarletteWebSocketDisconnect

from app.auth.domain import AuthenticatedUser
from app.auth.failures import UserNotProvisioned
from app.composition.lifecycle import RuntimeCleanup
from app.composition.runtime import ApiRuntime
from app.core.auth_failures import AuthenticationUnavailable, TokenRejected
from app.core.database import get_session
from app.services.realtime_service import RealtimeService
from app.websockets.manager import WebSocketManager


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


WS_USER_ID = UUID("00000000-0000-0000-0000-000000000123")


class FakeAuthenticator:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure

    async def authenticate(self, token: str) -> AuthenticatedUser:
        if self.failure is not None:
            raise self.failure
        if token != "valid-token":
            raise TokenRejected("signature")
        return AuthenticatedUser(internal_user_id=WS_USER_ID)


class FakeEventBus:
    """Stub event bus — never touches Redis."""

    async def publish(self, event: object) -> None:  # noqa: D102
        pass

    async def subscribe(self):
        # Yields nothing; tests drive broadcasts via ws_manager.broadcast() directly.
        return
        yield  # pragma: no cover — makes this an async generator


class FakeObservability:
    def record_invalid_contract(self, **_attributes: str) -> None:
        pass


def _runtime_with_realtime(settings, realtime_service) -> ApiRuntime:
    return ApiRuntime(
        settings=settings,
        engine=object(),
        session_factory=object(),
        redis_client=object(),
        observability=object(),
        auth_provider=object(),
        readiness_checks=object(),
        storage_provider=object(),
        arq_pool=None,
        call_finalization_queue=None,
        realtime_service=realtime_service,
        livekit_webhook_receiver=None,
        livekit_recording_service=None,
        _cleanup=RuntimeCleanup(AsyncExitStack()),
    )


# ---------------------------------------------------------------------------
# Synchronous fixture
# ---------------------------------------------------------------------------


class ManagerSpy(WebSocketManager):
    def __init__(self) -> None:
        super().__init__()
        self.disconnect_calls: list[tuple[str, object]] = []

    async def disconnect(self, user_id: str, websocket) -> None:
        self.disconnect_calls.append((user_id, websocket))
        await super().disconnect(user_id, websocket)


@pytest.fixture()
def ws_app_factory(
    settings_env,
    tmp_path: Path,
) -> Callable[..., tuple[object, WebSocketManager]]:
    """
    Stand-alone synchronous fixture.

    Gives every app a ``tmp_path``-scoped SQLite session override, installs a
    ``RealtimeService`` on ``app.state``, and returns an (app, ws_manager) tuple.
    It does **not** use the async ``test_app`` fixture, avoiding cross-loop
    conflicts with Starlette's TestClient.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import Settings
    from app.main import create_app

    apps: list[object] = []

    def _factory(
        *,
        auth_provider: FakeAuthenticator | None = None,
        websocket_manager: WebSocketManager | None = None,
    ) -> tuple[object, WebSocketManager]:
        configured_settings = Settings().model_copy(
            update={"realtime_enabled": True}
        )
        app = create_app(configured_settings)
        db_path = tmp_path / f"ws_test_{len(apps)}.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"

        async def _override_get_session():
            engine = create_async_engine(db_url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                yield session
            await engine.dispose()

        app.dependency_overrides[get_session] = _override_get_session
        manager = websocket_manager or WebSocketManager()
        realtime_service = RealtimeService(
            authenticator=auth_provider or FakeAuthenticator(),
            event_bus=FakeEventBus(),
            websocket_manager=manager,
            observability=FakeObservability(),
        )
        app.state.runtime = _runtime_with_realtime(
            configured_settings, realtime_service
        )
        apps.append(app)
        return app, manager

    yield _factory

    for app in apps:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture()
def ws_app(ws_app_factory) -> tuple[object, WebSocketManager]:
    return ws_app_factory()


def test_disabled_app_does_not_register_or_accept_websocket(settings_env) -> None:
    from app.core.config import Settings
    from app.main import create_app

    configured_settings = Settings().model_copy(
        update={"realtime_enabled": False}
    )
    app = create_app(configured_settings)
    install_test_api_runtime(app, settings=configured_settings)
    websocket_paths = {
        route.path
        for route in app.routes
        if route.__class__.__name__ == "APIWebSocketRoute"
    }
    client = TestClient(app, raise_server_exceptions=True)

    assert "/ws" not in websocket_paths
    assert client.get("/ws").status_code == 404
    with pytest.raises(StarletteWebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass


def test_disabled_app_lifespan_does_not_construct_realtime_redis(
    settings_env,
) -> None:
    from app.composition.api import build_api_runtime
    from app.core.config import Settings
    from app import main as main_module

    class ForbiddenRealtimeDependency:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("disabled realtime must not construct Redis or service")

    class Engine:
        async def dispose(self) -> None:
            return None

    class Resource:
        async def aclose(self) -> None:
            return None

    async def runtime_builder(settings):
        return await build_api_runtime(
            settings,
            engine_factory=lambda _url: Engine(),
            redis_factory=lambda _url: Resource(),
            observability_factory=lambda **_kwargs: Resource(),
            auth_factory=lambda **_kwargs: Resource(),
            readiness_factory=lambda **_kwargs: object(),
            storage_factory=lambda **_kwargs: Resource(),
            arq_pool_factory=lambda _url: None,
            realtime_service_factory=ForbiddenRealtimeDependency,
            webhook_receiver_factory=lambda **_kwargs: object(),
            recording_service_factory=lambda **_kwargs: object(),
        )

    app = main_module.create_app(
        Settings().model_copy(update={"realtime_enabled": False}),
        runtime_builder=runtime_builder,
    )

    with TestClient(app, raise_server_exceptions=True):
        assert app.state.runtime.realtime_service is None


# ---------------------------------------------------------------------------
# 1. Successful auth + ping/pong
# ---------------------------------------------------------------------------


def test_successful_auth_and_ping_pong(ws_app) -> None:
    """
    A client that sends a valid auth token is registered; subsequent ping
    messages are echoed back as pong.
    """
    app, _ = ws_app
    client = TestClient(app, raise_server_exceptions=True)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "valid-token"})
        ws.send_json({"type": "ping"})
        response = ws.receive_json()

    assert response == {"type": "pong"}


# ---------------------------------------------------------------------------
# 2a. Missing token — server sends error frame + closes with 1008
# ---------------------------------------------------------------------------


def test_missing_token_receives_error_and_close(ws_app) -> None:
    """
    An auth message without a ``token`` field causes the server to reply with
    an error frame and close the connection with code 1008.
    """
    app, _ = ws_app
    client = TestClient(app, raise_server_exceptions=True)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth"})  # token key absent

        error_msg = ws.receive_json()
        assert error_msg == {"type": "error", "detail": "auth_required"}

        # The server calls websocket.close(code=1008); the next read propagates
        # that as a WebSocketDisconnect.
        with pytest.raises(StarletteWebSocketDisconnect) as exc_info:
            ws.receive_json()

    assert exc_info.value.code == 1008


# ---------------------------------------------------------------------------
# 2b. Wrong first message type — same error path as missing token
# ---------------------------------------------------------------------------


def test_wrong_message_type_receives_error_and_close(ws_app) -> None:
    """
    Any first message whose ``type`` is not ``"auth"`` is rejected with the
    same auth_required error frame.
    """
    app, _ = ws_app
    client = TestClient(app, raise_server_exceptions=True)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})  # wrong first message type

        error_msg = ws.receive_json()
        assert error_msg == {"type": "error", "detail": "auth_required"}

        with pytest.raises(StarletteWebSocketDisconnect) as exc_info:
            ws.receive_json()

    assert exc_info.value.code == 1008


# ---------------------------------------------------------------------------
# 2c. Typed verifier failures — safe frame and transport-specific close
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "detail", "close_code"),
    [
        (TokenRejected("authorized_party"), "invalid_token", 1008),
        (UserNotProvisioned(), "invalid_token", 1008),
        (AuthenticationUnavailable("jwks_timeout"), "auth_unavailable", 1013),
    ],
)
def test_websocket_maps_typed_auth_failure_to_safe_frame_and_close(
    ws_app_factory, failure: Exception, detail: str, close_code: int
) -> None:
    app, _ = ws_app_factory(auth_provider=FakeAuthenticator(failure))
    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "auth", "token": "TOKEN_SENTINEL"})
        assert websocket.receive_json() == {"type": "error", "detail": detail}
        with pytest.raises(StarletteWebSocketDisconnect) as exc_info:
            websocket.receive_json()
    assert exc_info.value.code == close_code


def test_authenticated_client_disconnects_from_service_manager_exactly_once(
    ws_app_factory,
) -> None:
    manager = ManagerSpy()
    app, _ = ws_app_factory(websocket_manager=manager)

    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "auth", "token": "valid-token"})
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}

    assert len(manager.disconnect_calls) == 1
    assert manager.disconnect_calls[0][0] == str(WS_USER_ID)


def test_auth_failure_does_not_disconnect_before_identity_is_established(
    ws_app_factory,
) -> None:
    manager = ManagerSpy()
    app, _ = ws_app_factory(
        auth_provider=FakeAuthenticator(TokenRejected("signature")),
        websocket_manager=manager,
    )

    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "auth", "token": "TOKEN_SENTINEL"})
        assert websocket.receive_json() == {"type": "error", "detail": "invalid_token"}
        with pytest.raises(StarletteWebSocketDisconnect):
            websocket.receive_json()

    assert manager.disconnect_calls == []


# ---------------------------------------------------------------------------
# 3. Broadcast delivery
# ---------------------------------------------------------------------------


def test_broadcast_delivered_to_authenticated_client(ws_app) -> None:
    """
    After successful authentication, a broadcast published via
    ``WebSocketManager.broadcast()`` is delivered to the connected client.

    Strategy
    --------
    TestClient runs the ASGI app in a background thread with its own asyncio
    event loop.  We capture a reference to that loop from inside the WebSocket
    handler by monkey-patching ``ws_manager.connect``, then use
    ``asyncio.run_coroutine_threadsafe()`` to schedule ``broadcast()`` into
    that loop from the test thread.  Meanwhile, a second thread is blocked
    waiting on ``ws.receive_json()`` so that the WebSocket message queue is
    being consumed.
    """
    app, ws_manager = ws_app
    client = TestClient(app, raise_server_exceptions=True)

    broadcast_payload = {
        "type": "call_started",
        "room_name": "room-42",
        "call_id": "call-99",
    }

    # Capture the ASGI event loop from inside the WebSocket handler.
    asgi_loop_holder: list[asyncio.AbstractEventLoop] = []
    loop_ready = threading.Event()

    original_connect = ws_manager.connect

    async def _connect_and_capture(user_id: str, websocket) -> None:
        if not asgi_loop_holder:
            asgi_loop_holder.append(asyncio.get_running_loop())
            loop_ready.set()
        await original_connect(user_id, websocket)

    ws_manager.connect = _connect_and_capture  # type: ignore[method-assign]

    received_messages: list[dict] = []
    recv_error: list[Exception] = []
    recv_done = threading.Event()

    def _recv_thread(ws) -> None:
        try:
            received_messages.append(ws.receive_json())
        except Exception as exc:  # noqa: BLE001
            recv_error.append(exc)
        finally:
            recv_done.set()

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "valid-token"})

        # Block in a side thread waiting for the broadcast.
        t = threading.Thread(target=_recv_thread, args=(ws,), daemon=True)
        t.start()

        # Wait until the handler has registered the connection and we have
        # the ASGI loop reference.
        assert loop_ready.wait(timeout=5), "ASGI loop not captured in time"

        asgi_loop = asgi_loop_holder[0]
        future = asyncio.run_coroutine_threadsafe(
            ws_manager.broadcast(str(WS_USER_ID), broadcast_payload),
            asgi_loop,
        )
        future.result(timeout=5)

        assert recv_done.wait(timeout=5), "Broadcast message not received in time"
        t.join(timeout=2)

    assert recv_error == [], f"Unexpected receive error: {recv_error}"
    assert received_messages == [broadcast_payload]
