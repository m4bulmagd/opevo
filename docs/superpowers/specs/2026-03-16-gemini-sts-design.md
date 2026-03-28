# Gemini STS Design

## Summary

This spec adds an optional speech-to-speech runtime to the existing LiveKit agent by using the Google Gemini Live API through the LiveKit Google plugin. The current `stt_llm_tts` path remains the default and stays behaviorally intact. The new path is selected only when backend dispatch metadata sets `pipeline_mode` to `sts`.

The design is intentionally narrow. It introduces a native-audio Gemini runtime branch inside the agent application, preserves the existing API-side lifecycle and persistence flows, and avoids broad backend or product-scope changes while the team evaluates whether Gemini Live improves perceived latency.

## Goal

Add an opt-in `sts` runtime mode that uses Gemini Live native audio end to end, while preserving the existing `stt_llm_tts` runtime as the default and keeping current API-side call persistence, usage charging, and post-call processing intact.

## Non-Goals

- Replacing the default `stt_llm_tts` runtime.
- Adding a global env flag to enable or disable STS.
- Changing backend selection away from `agent_config.pipeline_mode`.
- Reworking call finalization, billing, or notification flows.
- Introducing separate-TTS Gemini text mode.
- Adding new product features unrelated to runtime selection or latency evaluation.

## Current State

- The agent currently models two pipeline modes in [providers.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/providers.py): `stt_llm_tts` and `sts`.
- [pipeline_factory.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/pipeline_factory.py) currently rejects `sts` with `ValueError("sts pipeline mode is not enabled yet")`.
- The active runtime constructs a session with separate STT, LLM, and TTS providers plus optional Silero VAD and the LiveKit multilingual turn detector.
- The backend already forwards `pipeline_mode` in dispatch metadata, so runtime selection can stay agent-local.
- Queue-backed call finalization, transcript persistence, usage charging, and notification creation are already functioning on `main`.

## Proposed Approaches

### Approach A: Optional Gemini native-audio STS branch

Add a second runtime branch in the agent for `pipeline_mode="sts"` using the LiveKit Google realtime model with native audio output and Gemini built-in turn detection.

Pros:
- Matches the latency goal most directly
- Minimizes risk to the current default path
- Keeps runtime selection aligned with existing backend config

Cons:
- Requires a second runtime construction path in the agent
- Event adaptation may differ from the current STT/TTS-driven hooks

### Approach B: Hybrid Gemini realtime text plus existing TTS

Use Gemini realtime in text-only mode and keep the existing TTS layer.

Pros:
- Smaller change from the current architecture
- More output control than native audio

Cons:
- Does not satisfy the "fully Gemini" requirement
- Likely smaller latency improvement

### Approach C: Replace the current default runtime

Switch all calls to Gemini Live native audio and retire the current pipeline.

Pros:
- Simplest long-term architecture if Gemini becomes the standard

Cons:
- Unnecessary rollout risk
- No safe comparison period against the current path
- Too broad for this iteration

## Recommendation

Use Approach A. The codebase already carries pipeline-mode selection and a functioning production-style `stt_llm_tts` path. The lowest-risk design is to add Gemini STS as an opt-in runtime branch so latency can be evaluated without destabilizing the working default.

## Design

### Runtime Selection

Runtime selection continues to come from backend dispatch metadata, specifically `agent_config.pipeline_mode`. No new backend API contract is required. The agent worker reads the existing metadata and passes it into [build_agent_runtime()](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/pipeline_factory.py).

`build_pipeline_config()` should stop rejecting `sts` and instead normalize two valid modes:

- `stt_llm_tts`: current behavior, default when the field is absent
- `sts`: Gemini Live native audio runtime

The configuration object should still include provider fields for the standard `stt_llm_tts` path, but the STS path should ignore separate STT and TTS providers and require `sts_provider="gemini"`.

### Agent Runtime Structure

The runtime factory should split into two clear branches:

1. `stt_llm_tts` branch
   - Keep the current session construction with separate STT, LLM, TTS, optional Silero VAD, and optional LiveKit turn detection.

2. `sts` branch
   - Build a session around `google.realtime.RealtimeModel`.
   - Use Gemini native audio output rather than text-only mode.
   - Use Gemini built-in turn detection rather than external Silero and LiveKit turn-detection models.
   - Pass prompt text from [prompt_builder.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/prompt_builder.py) into Gemini realtime `instructions`.

The worker entrypoint in [main.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/main.py) should remain structurally the same: connect to LiveKit, build the runtime from metadata, register conversation callbacks, start the session, and keep the same shutdown finalization callback.

### Unit Boundaries

The change should stay split across small, explicit units:

- [providers.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/providers.py): declares valid pipeline and provider enums.
- [pipeline_factory.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/pipeline_factory.py): owns runtime selection and session construction for both branches.
- [main.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/main.py): owns worker entrypoint wiring and event registration, but not provider-specific construction logic.
- [session_runtime.py](/home/i933k/code/ai/bmad-opevo/apps/agent/agent/session_runtime.py): remains the backend-facing adapter for transcript, utterance, and finalization behavior.
- [test_pipeline_factory.py](/home/i933k/code/ai/bmad-opevo/apps/agent/tests/test_pipeline_factory.py): remains the primary runtime-construction test surface.

That boundary keeps Gemini-specific logic from leaking into the worker lifecycle and avoids mixing backend persistence concerns into the runtime factory.

### Provider And Auth Contract

The STS runtime uses the existing LiveKit Google plugin dependency family already declared in [pyproject.toml](/home/i933k/code/ai/bmad-opevo/apps/agent/pyproject.toml). For authentication, the STS branch should support the Google Gemini API key path and validate credentials before session startup:

- preferred env: `GEMINI_API_KEY`

If `pipeline_mode="sts"` is selected and no Gemini credential is present, the worker should fail fast with a clear configuration error.

This design does not add Vertex AI support in this iteration.

### Event Adaptation And Persistence

The backend should continue to see the same logical events regardless of runtime mode:

- caller utterances forwarded into `SessionRuntime.handle_caller_transcript(...)`
- assistant utterances forwarded into `SessionRuntime.handle_agent_utterance(...)`
- shutdown still triggers queued completion through `SessionRuntime.finalize(...)`

For `sts`, the agent code should adapt Gemini/LiveKit conversation events into those same runtime calls instead of assuming the current separate-STT event shape is the only source of truth. This preserves the current API-side persistence model and prevents the STS experiment from leaking changes into billing, notifications, or queue-backed finalization.

### Error Handling

The STS branch should fail explicitly rather than silently degrading:

- If `pipeline_mode="sts"` is selected but Gemini credentials are missing, raise a clear error before starting the session.
- If the STS provider value is unsupported, raise a clear configuration error.
- Do not silently fall back from `sts` to `stt_llm_tts`, because that would hide rollout and latency issues.

The standard `stt_llm_tts` branch should retain its current behavior.

### Testing Strategy

At minimum, tests should cover:

- `build_pipeline_config()` accepts `sts` and still defaults to `stt_llm_tts`.
- `build_agent_runtime()` builds the standard `stt_llm_tts` branch unchanged.
- `build_agent_runtime()` builds the STS branch with the Google realtime model and without separate STT/TTS/VAD/turn-detector wiring.
- selecting `sts` without Gemini credentials fails clearly.
- selecting an unsupported STS provider fails clearly.

The existing agent tests in [test_pipeline_factory.py](/home/i933k/code/ai/bmad-opevo/apps/agent/tests/test_pipeline_factory.py) should be expanded rather than creating a parallel test style.

## Acceptance Criteria

This work is complete when all of the following are true:

- `pipeline_mode="sts"` no longer raises the current "not enabled" error.
- `pipeline_mode` remains optional and still defaults to `stt_llm_tts`.
- The STS branch constructs a Gemini native-audio realtime session using backend-provided prompt/instructions.
- The STS branch does not depend on the external STT/TTS pipeline or the current external turn detector.
- Missing Gemini credentials for STS fail with a clear runtime/config error.
- Existing `stt_llm_tts` tests still pass.
- New agent tests cover both branches and key STS failure cases.

## Rollout Notes

- Default behavior stays on `stt_llm_tts`.
- STS is enabled per user or per number only by setting `agent_config.pipeline_mode` to `sts`.
- This makes latency comparison possible without a broad migration.
- If Gemini STS proves better in production-style evaluation, a later spec can decide whether to expand usage or replace the default runtime.
