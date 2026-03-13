from agent.pipeline_factory import build_pipeline_config


def test_pipeline_factory_defaults_to_stt_llm_tts() -> None:
    config = build_pipeline_config({})

    assert config["pipeline_mode"] == "stt_llm_tts"
    assert config["stt_provider"] == "deepgram"
    assert config["llm_provider"] == "openai"
    assert config["tts_provider"] == "openai"


def test_pipeline_factory_rejects_sts_when_not_enabled() -> None:
    try:
        build_pipeline_config({"pipeline_mode": "sts"})
    except ValueError as exc:
        assert "not enabled" in str(exc)
    else:
        raise AssertionError("Expected sts mode to raise")
