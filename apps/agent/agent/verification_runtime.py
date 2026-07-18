import asyncio
import inspect
import sys
from collections.abc import Callable
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AutoSubscribe, JobContext, room_io

from agent.api_client import AgentApiClient
from agent.pipeline_factory import build_verification_session
from agent.schemas import ForwardingVerificationDispatchMetadata


SIP_PARTICIPANT_KIND = rtc.ParticipantKind.Value("PARTICIPANT_KIND_SIP")
NO_RECORDING = {
    "audio": False,
    "transcript": False,
    "traces": False,
    "logs": False,
}


class VerificationRuntimeCleanupError(RuntimeError):
    """The isolated verification runtime could not finish cleanup."""


def _build_verification_agent() -> Agent:
    return Agent(instructions="")


async def _await_if_needed(result: object) -> None:
    if inspect.isawaitable(result):
        await result


async def _close_session(session: Any) -> None:
    public_close = getattr(session, "aclose", None)
    if callable(public_close):
        await _await_if_needed(public_close())
        return
    fallback_shutdown = getattr(session, "shutdown", None)
    if not callable(fallback_shutdown):
        raise VerificationRuntimeCleanupError(
            "verification runtime cleanup failed"
        )
    await _await_if_needed(fallback_shutdown(drain=True))


async def _cleanup_runtime(
    session: Any | None,
    api_client: AgentApiClient | None,
    *,
    close_api_client: bool,
) -> None:
    cleanup_failed = False
    cancellation: asyncio.CancelledError | None = None
    try:
        if session is not None:
            await _close_session(session)
    except asyncio.CancelledError as exc:
        cancellation = exc
    except BaseException:
        cleanup_failed = True

    try:
        if close_api_client and api_client is not None:
            await api_client.aclose()
    except asyncio.CancelledError as exc:
        if cancellation is None:
            cancellation = exc
    except BaseException:
        cleanup_failed = True

    if cancellation is not None:
        raise cancellation
    if cleanup_failed:
        raise VerificationRuntimeCleanupError(
            "verification runtime cleanup failed"
        ) from None


async def run_forwarding_verification(
    context: JobContext,
    metadata: ForwardingVerificationDispatchMetadata,
    *,
    session_factory: Callable[[str], Any] = build_verification_session,
    agent_factory: Callable[[], Any] = _build_verification_agent,
    api_client: AgentApiClient | None = None,
    api_client_factory: Callable[[], AgentApiClient] = AgentApiClient,
) -> None:
    session = None
    resolved_api_client = api_client
    owns_api_client = api_client is None
    try:
        await context.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_NONE)
        sip_participant = await context.wait_for_participant(
            kind=SIP_PARTICIPANT_KIND
        )

        session = session_factory(metadata.tts_provider)
        agent = agent_factory()
        if resolved_api_client is None:
            resolved_api_client = api_client_factory()

        session.input.set_audio_enabled(False)
        await session.start(
            agent=agent,
            room=context.room,
            room_options=room_io.RoomOptions(
                participant_identity=sip_participant.identity,
                participant_kinds=[SIP_PARTICIPANT_KIND],
                close_on_disconnect=True,
                delete_room_on_close=True,
            ),
            record=NO_RECORDING.copy(),
        )
        speech = session.say(
            metadata.message,
            allow_interruptions=False,
        )
        await _await_if_needed(speech)
        await resolved_api_client.complete_verification(
            metadata.verification_session_id,
            metadata.completion_token,
        )
    finally:
        primary_error_active = sys.exc_info()[0] is not None
        try:
            await _cleanup_runtime(
                session,
                resolved_api_client,
                close_api_client=owns_api_client,
            )
        except BaseException:
            if not primary_error_active:
                raise
