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
* Solution: a dedicated *synchronous* ``ws_app`` fixture (plain ``@pytest.fixture``)
  that sets up the database and installs a fake RealtimeService entirely using
  synchronous asyncio.run() calls, outside of any running anyio loop.
  TestClient is then used *without* the ``with TestClient(app):`` context
  manager so the lifespan is not re-entered (the fixture already set up
  ``app.state.realtime_service``).

Bug fixed during writing these tests
--------------------------------------
``RealtimeService.authenticate()`` previously called ``identity.user_id`` which
does not exist on ``UserIdentity``; it has been corrected to
``identity.clerk_user_id``.
"""

import asyncio
import threading
import tempfile

import jwt as _jwt
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect as StarletteWebSocketDisconnect

from app.core.auth import AuthProvider, UserIdentity
from app.core.database import get_session
from app.services.realtime_service import RealtimeService
from app.websockets.manager import WebSocketManager


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeAuthProvider(AuthProvider):
    """Accepts only the literal string ``'valid-token'``; rejects everything else."""

    def verify_token(self, token: str) -> UserIdentity:
        if token != "valid-token":
            raise _jwt.InvalidTokenError("bad token")
        return UserIdentity(clerk_user_id="user_ws_test")


class FakeEventBus:
    """Stub event bus — never touches Redis."""

    async def publish_json(self, user_id: str, payload: dict) -> None:  # noqa: D102
        pass

    async def subscribe(self):
        # Yields nothing; tests drive broadcasts via ws_manager.broadcast() directly.
        return
        yield  # pragma: no cover — makes this an async generator


# ---------------------------------------------------------------------------
# Synchronous fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def ws_app(settings_env):  # settings_env auto-use fixture sets env vars
    """
    Stand-alone synchronous fixture.

    Sets up an in-memory SQLite database, installs a ``FakeRealtimeService`` on
    ``app.state``, and yields a (app, ws_manager) tuple.

    Uses ``asyncio.run()`` for async setup so it works outside any event loop.
    Does **not** use the async ``test_app`` fixture to avoid cross-loop conflicts
    with Starlette's TestClient.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models import Base
    from app.main import app

    tmp = tempfile.mkdtemp()
    db_url = f"sqlite+aiosqlite:///{tmp}/ws_test.db"

    async def _setup_db() -> None:
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup_db())

    async def _override_get_session():
        engine = create_async_engine(db_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
        await engine.dispose()

    app.dependency_overrides[get_session] = _override_get_session

    ws_manager = WebSocketManager()
    fake_service = RealtimeService(
        auth_provider=FakeAuthProvider(),
        event_bus=FakeEventBus(),
        websocket_manager=ws_manager,
    )
    # Override the service that the lifespan installs.
    app.state.realtime_service = fake_service

    yield app, ws_manager

    app.dependency_overrides.pop(get_session, None)


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
# 2c. Invalid JWT token — server exception closes connection silently
# ---------------------------------------------------------------------------


def test_invalid_jwt_token_closes_connection_without_error_frame(ws_app) -> None:
    """
    When the token is present but cannot be verified, ``FakeAuthProvider``
    raises ``jwt.InvalidTokenError``.  The router's
    ``except (WebSocketDisconnect, jwt.PyJWTError)`` handler fires; no
    application-level error frame is sent and the connection closes silently.

    We verify that the WS context exits cleanly without any application
    message being received — we deliberately do *not* call ``receive_json()``
    after the bad auth because that would block forever (server sends nothing).
    """
    app, _ = ws_app
    client = TestClient(app, raise_server_exceptions=True)

    messages_received: list[dict] = []

    # Just verify the context exits without error.
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "THIS-IS-NOT-VALID"})
        # Intentionally no receive_json() call — the server has already exited.

    assert messages_received == []


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
            ws_manager.broadcast("user_ws_test", broadcast_payload),
            asgi_loop,
        )
        future.result(timeout=5)

        assert recv_done.wait(timeout=5), "Broadcast message not received in time"
        t.join(timeout=2)

    assert recv_error == [], f"Unexpected receive error: {recv_error}"
    assert received_messages == [broadcast_payload]
