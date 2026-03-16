import logging
from types import SimpleNamespace

from agent.debug_streams import StreamDebugLogger


def test_stream_debug_logger_emits_structured_stage_logs(caplog) -> None:
    debug_logger = StreamDebugLogger(enabled=True, call_id="call_123", user_id="user_123")

    with caplog.at_level(logging.INFO):
        debug_logger.log_stt_event(
            SimpleNamespace(
                type="interim_transcript",
                alternatives=[SimpleNamespace(text="hello there")],
            )
        )
        debug_logger.log_llm_start()
        debug_logger.log_llm_delta("Hello")
        debug_logger.log_llm_complete("Hello", elapsed_ms=42)
        debug_logger.log_tts_start("Hello, this is a test")
        debug_logger.log_tts_first_frame(elapsed_ms=18)
        debug_logger.log_tts_complete(
            "Hello, this is a test",
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
