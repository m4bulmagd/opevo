from types import SimpleNamespace

import pytest

from agent.composition import (
    AgentProcessRuntime,
    AgentRuntimeConfigurationError,
    require_agent_process_runtime,
)
from agent.config import AgentSettings
from agent.main import prewarm_assets


def _settings(**overrides: object) -> AgentSettings:
    configured = AgentSettings(
        livekit_silero_vad_enabled=False,
        livekit_turn_detector_enabled=False,
        speechmatics_turn_detection_mode="adaptive",
    )
    return configured.model_copy(update=overrides)


@pytest.mark.parametrize("userdata", [None, {}, object()])
def test_require_agent_process_runtime_rejects_missing_or_wrong_process_data(
    userdata: object,
) -> None:
    proc = SimpleNamespace(userdata=userdata)

    with pytest.raises(
        AgentRuntimeConfigurationError,
        match="agent process runtime is not initialized",
    ):
        require_agent_process_runtime(proc)


def test_require_agent_process_runtime_returns_typed_process_data() -> None:
    runtime = AgentProcessRuntime(settings=_settings(), silero_vad=object())

    assert require_agent_process_runtime(SimpleNamespace(userdata=runtime)) is runtime


def test_prewarm_publishes_complete_runtime_with_exact_settings_and_no_vad() -> None:
    settings = _settings()
    original_userdata = object()
    proc = SimpleNamespace(userdata=original_userdata)

    prewarm_assets(proc, settings=settings)

    assert isinstance(proc.userdata, AgentProcessRuntime)
    assert proc.userdata.settings is settings
    assert proc.userdata.silero_vad is None


def test_prewarm_publishes_loaded_vad_in_complete_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from livekit import plugins

    settings = _settings(livekit_silero_vad_enabled=True)
    vad = object()
    original_userdata = object()
    proc = SimpleNamespace(userdata=original_userdata)

    def load_vad() -> object:
        assert proc.userdata is original_userdata
        return vad

    fake_silero = SimpleNamespace(VAD=SimpleNamespace(load=load_vad))
    monkeypatch.setattr(plugins, "silero", fake_silero, raising=False)

    prewarm_assets(proc, settings=settings)

    assert isinstance(proc.userdata, AgentProcessRuntime)
    assert proc.userdata.settings is settings
    assert proc.userdata.silero_vad is vad
