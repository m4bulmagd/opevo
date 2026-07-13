import logging
from types import SimpleNamespace

from agent.debug_streams import StreamDebugLogger


def test_stream_debug_logger_emits_structured_stage_logs(caplog) -> None:
    debug_logger = StreamDebugLogger(enabled=True, call_id="call_123", user_id="user_123")
    transcript_sentinels = [
        "STT_TRANSCRIPT_SENTINEL_SECRET",
        "LLM_DELTA_SENTINEL_SECRET",
        "LLM_COMPLETE_SENTINEL_SECRET",
        "TTS_START_SENTINEL_SECRET",
        "TTS_COMPLETE_SENTINEL_SECRET",
    ]

    with caplog.at_level(logging.INFO):
        debug_logger.log_stt_event(
            SimpleNamespace(
                type="interim_transcript",
                alternatives=[SimpleNamespace(text=transcript_sentinels[0])],
            )
        )
        debug_logger.log_llm_start()
        debug_logger.log_llm_delta(transcript_sentinels[1])
        debug_logger.log_llm_complete(transcript_sentinels[2], elapsed_ms=42)
        debug_logger.log_tts_start(transcript_sentinels[3])
        debug_logger.log_tts_first_frame(elapsed_ms=18)
        debug_logger.log_tts_complete(
            transcript_sentinels[4],
            frame_count=4,
            elapsed_ms=120,
            audio_seconds=0.8,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("agent.debug stt.interim_transcript" in message for message in messages)
    assert any("agent.debug llm.start" in message for message in messages)
    assert any("agent.debug llm.delta" in message for message in messages)
    assert any("agent.debug llm.complete" in message for message in messages)
    assert any("agent.debug tts.start" in message for message in messages)
    assert any("agent.debug tts.first_frame" in message for message in messages)
    assert any("agent.debug tts.complete" in message for message in messages)
    assert any("elapsed_ms=42" in message for message in messages)
    assert any("frame_count=4" in message for message in messages)
    rendered_records = "\n".join(
        f"{record.getMessage()} {record.__dict__!r}" for record in caplog.records
    )
    for sentinel in transcript_sentinels:
        assert sentinel not in rendered_records


def test_stream_debug_logger_tts_error_does_not_render_text_or_exception_message(caplog) -> None:
    debug_logger = StreamDebugLogger(enabled=True, call_id="call_123", user_id="user_123")
    transcript_sentinel = "TTS_ERROR_TRANSCRIPT_SENTINEL"
    authorization_sentinel = "TTS_ERROR_AUTHORIZATION_SENTINEL"

    with caplog.at_level(logging.INFO):
        debug_logger.log_tts_error(
            transcript_sentinel,
            error=RuntimeError(authorization_sentinel),
        )

    assert transcript_sentinel not in caplog.text
    assert authorization_sentinel not in caplog.text
    assert "agent.debug tts.error" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
