# Gemini STS Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `pipeline_mode="sts"` runtime that uses Gemini Live native audio while preserving `stt_llm_tts` as the default path.

**Architecture:** Keep backend selection unchanged and branch only inside the agent runtime factory. The standard `stt_llm_tts` path remains intact; the new `sts` path builds a Gemini realtime session, adapts its events into the existing `SessionRuntime`, and keeps queue-backed finalization unchanged.

**Tech Stack:** Python 3.11+, LiveKit Agents 1.4.4, LiveKit Google plugin 1.4.4, pytest

---

## File Structure

- Modify: `apps/agent/agent/pipeline_factory.py`
  - Normalize pipeline selection, add Gemini STS runtime construction, keep the standard runtime intact.
- Modify: `apps/agent/agent/main.py`
  - Keep one entrypoint, but make event registration work for both standard and STS runtime shapes.
- Modify: `apps/agent/agent/providers.py`
  - Keep enum definitions aligned with supported STS provider values.
- Modify: `apps/agent/tests/test_pipeline_factory.py`
  - Expand runtime-construction tests for STS success and failure cases.
- Create or modify: `apps/agent/tests/test_main.py`
  - Add worker entrypoint tests for mode-specific event wiring if this file already exists; otherwise create it.
- Modify: `apps/agent/.env.example`
  - Document `GEMINI_API_KEY` as the preferred Gemini credential and note `GEMINI_API_KEY` fallback if still supported.
- Modify: `docs/architecture/backend-context.md`
  - Record that `pipeline_mode="sts"` is now an optional runtime path once implemented.
- Modify: `docs/architecture/staging-smoke-runbook.md`
  - Add a focused STS smoke section for one inbound test call after rollout.

## Chunk 1: Runtime Factory And Tests

### Task 1: Make `pipeline_mode="sts"` a valid configuration

**Files:**
- Modify: `apps/agent/agent/pipeline_factory.py`
- Modify: `apps/agent/tests/test_pipeline_factory.py`

- [ ] **Step 1: Write the failing config test**

Add or replace the current rejection test with a success-path assertion in `apps/agent/tests/test_pipeline_factory.py`:

```python
def test_pipeline_factory_accepts_sts_mode() -> None:
    config = build_pipeline_config({"pipeline_mode": "sts"})

    assert config["pipeline_mode"] == "sts"
    assert config["sts_provider"] == "gemini"
```

- [ ] **Step 2: Run the targeted test to confirm current failure**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_pipeline_factory.py::test_pipeline_factory_accepts_sts_mode -v
```

Expected: FAIL because `build_pipeline_config()` still raises `ValueError("sts pipeline mode is not enabled yet")`.

- [ ] **Step 3: Implement the minimal config change**

In `apps/agent/agent/pipeline_factory.py`:
- remove the hard rejection for `PipelineMode.STS`
- return `pipeline_mode` as requested when it is one of the supported values
- keep default `pipeline_mode` as `stt_llm_tts`
- keep `sts_provider` defaulting to `gemini`
- raise a clear `ValueError` only for unsupported pipeline modes

- [ ] **Step 4: Run the targeted test again**

Run the same command from Step 2.

Expected: PASS

- [ ] **Step 5: Commit the config acceptance change**

```bash
git add apps/agent/agent/pipeline_factory.py apps/agent/tests/test_pipeline_factory.py
git commit -m "feat: accept gemini sts pipeline mode"
```

### Task 2: Add failing tests for the Gemini STS runtime branch

**Files:**
- Modify: `apps/agent/tests/test_pipeline_factory.py`

- [ ] **Step 1: Write the STS runtime success test**

Add a fake Google realtime plugin and a runtime test:

```python
class FakeGoogleRealtimePlugin:
    class realtime:
        class RealtimeModel:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs


def test_pipeline_factory_builds_sts_runtime_with_gemini_realtime(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    agent, session = build_agent_runtime(
        {
            "agent_name": "Ava",
            "owner_name": "Sam",
            "system_prompt": "Be helpful.",
            "knowledge_base": "Hours 9-5",
            "pipeline_mode": "sts",
            "sts_provider": "gemini",
        },
        plugin_modules={"google": FakeGoogleRealtimePlugin},
        agent_cls=FakeAgent,
        session_cls=FakeSession,
    )

    assert "llm" in session.kwargs
    assert session.kwargs["llm"].kwargs["api_key"] == "test-key"
    assert "stt" not in session.kwargs
    assert "tts" not in session.kwargs
    assert "vad" not in session.kwargs
    assert "turn_detection" not in session.kwargs
```

- [ ] **Step 2: Write the STS missing-credentials failure test**

```python
def test_pipeline_factory_rejects_sts_without_google_credentials(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="Gemini credentials"):
        build_agent_runtime(
            {
                "agent_name": "Ava",
                "owner_name": "Sam",
                "pipeline_mode": "sts",
                "sts_provider": "gemini",
            },
            plugin_modules={"google": FakeGoogleRealtimePlugin},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )
```

- [ ] **Step 3: Write the unsupported STS provider failure test**

```python
def test_pipeline_factory_rejects_unsupported_sts_provider(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    with pytest.raises(ValueError, match="Unsupported STS provider"):
        build_agent_runtime(
            {
                "agent_name": "Ava",
                "owner_name": "Sam",
                "pipeline_mode": "sts",
                "sts_provider": "other",
            },
            plugin_modules={"google": FakeGoogleRealtimePlugin},
            agent_cls=FakeAgent,
            session_cls=FakeSession,
        )
```

- [ ] **Step 4: Run the new STS tests to confirm failure**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest \
  tests/test_pipeline_factory.py::test_pipeline_factory_builds_sts_runtime_with_gemini_realtime \
  tests/test_pipeline_factory.py::test_pipeline_factory_rejects_sts_without_google_credentials \
  tests/test_pipeline_factory.py::test_pipeline_factory_rejects_unsupported_sts_provider -v
```

Expected: FAIL because the runtime factory still only knows the standard STT/LLM/TTS path.

- [ ] **Step 5: Commit the failing-test scaffold**

```bash
git add apps/agent/tests/test_pipeline_factory.py
git commit -m "test: cover gemini sts runtime branch"
```

### Task 3: Implement the Gemini STS runtime branch

**Files:**
- Modify: `apps/agent/agent/pipeline_factory.py`
- Modify: `apps/agent/agent/providers.py`
- Modify: `apps/agent/tests/test_pipeline_factory.py`

- [ ] **Step 1: Add small helper functions for STS runtime construction**

In `apps/agent/agent/pipeline_factory.py`, add focused helpers rather than a large inline branch:
- `_resolve_gemini_api_key()`
- `_build_sts_model(config, plugins, instructions)`
- `_build_sts_session(config, plugins, instructions, session_cls)`

The STS helpers should:
- accept only `sts_provider="gemini"`
- prefer `GEMINI_API_KEY`, then fall back to `GEMINI_API_KEY`
- raise `ValueError` if no credential is present
- instantiate `google.realtime.RealtimeModel(...)`
- pass the built prompt as `instructions`

- [ ] **Step 2: Split `build_agent_runtime()` into explicit standard vs STS branches**

Implementation requirements:
- keep the standard branch behavior byte-for-byte as close as possible
- for `sts`, skip `_build_stt`, `_build_tts`, `_build_vad`, and `_build_turn_detection`
- create `session = session_cls(llm=realtime_model)` for the STS path
- keep agent construction using the same prompt and endpointing settings

- [ ] **Step 3: Run targeted STS and standard-path tests**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_pipeline_factory.py -v
```

Expected: PASS for the full file, including the existing standard-path tests and the new STS tests.

- [ ] **Step 4: Commit the runtime implementation**

```bash
git add apps/agent/agent/pipeline_factory.py apps/agent/agent/providers.py apps/agent/tests/test_pipeline_factory.py
git commit -m "feat: add gemini sts runtime branch"
```

## Chunk 2: Worker Wiring, Docs, And Verification

### Task 4: Add entrypoint tests for mode-specific event wiring

**Files:**
- Modify: `apps/agent/tests/test_main.py`
- Modify: `apps/agent/agent/main.py`

- [ ] **Step 1: Write the failing worker-wiring tests**

Extend `apps/agent/tests/test_main.py` with local, deterministic tests around extracted registration helpers. Use fake session objects with `.on(...)` that store handlers in a dict and a fake runtime that records received caller and agent text.

Add a concrete standard-path test:

```python
@pytest.mark.asyncio
async def test_register_standard_session_handlers_forwards_final_caller_and_agent_text() -> None:
    session = FakeSession()
    runtime = FakeRuntime()
    metadata = {"call_id": "call-1", "user_id": "user-1"}

    _register_standard_session_handlers(session, runtime, metadata)

    await session.handlers["user_input_transcribed"](FakeTranscriptEvent("Hello", is_final=True))
    await session.handlers["conversation_item_added"](FakeConversationEvent("assistant", "Hi there"))

    assert runtime.caller_text == ["Hello"]
    assert runtime.agent_text == ["Hi there"]
```

Add a concrete STS test:

```python
@pytest.mark.asyncio
async def test_register_sts_session_handlers_forwards_caller_and_agent_text() -> None:
    session = FakeSession()
    runtime = FakeRuntime()
    metadata = {"call_id": "call-1", "user_id": "user-1"}

    _register_sts_session_handlers(session, runtime, metadata)

    await session.handlers["conversation_item_added"](FakeConversationEvent("user", "Need help"))
    await session.handlers["conversation_item_added"](FakeConversationEvent("assistant", "Sure"))

    assert runtime.caller_text == ["Need help"]
    assert runtime.agent_text == ["Sure"]
```

Keep the existing `build_worker_options` tests in the same file.

- [ ] **Step 2: Run the new entrypoint tests to confirm failure**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_main.py -v
```

Expected: FAIL because `main.py` currently assumes the standard callback shape only.

- [ ] **Step 3: Refactor `main.py` into small event-registration helpers**

Add focused helpers such as:
- `_register_standard_session_handlers(session, runtime, metadata)`
- `_register_sts_session_handlers(session, runtime, metadata)`
- `_register_session_handlers(session, runtime, metadata, pipeline_mode)`

Requirements:
- keep shutdown finalization logic unchanged
- route both branches into `runtime.handle_caller_transcript(...)` and `runtime.handle_agent_utterance(...)`
- avoid embedding Gemini-specific construction logic in `main.py`

- [ ] **Step 4: Run the entrypoint tests again**

Run the same command from Step 2.

Expected: PASS

- [ ] **Step 5: Commit the worker wiring change**

```bash
git add apps/agent/agent/main.py apps/agent/tests/test_main.py
git commit -m "feat: wire sts events through agent entrypoint"
```

### Task 5: Update docs and environment examples

**Files:**
- Modify: `apps/agent/.env.example`
- Modify: `docs/architecture/backend-context.md`
- Modify: `docs/architecture/staging-smoke-runbook.md`

- [ ] **Step 1: Document the STS credential contract**

In `apps/agent/.env.example`:
- add `GEMINI_API_KEY=replace-me`
- keep a short note or comment that `GEMINI_API_KEY` remains a compatibility fallback if the code still supports it

- [ ] **Step 2: Document the runtime-selection behavior**

In `docs/architecture/backend-context.md`:
- note that `pipeline_mode` now selects either `stt_llm_tts` or `sts`
- state that `stt_llm_tts` remains default
- state that `sts` uses Gemini native audio and Gemini turn detection

- [ ] **Step 3: Add a focused STS smoke checklist**

In `docs/architecture/staging-smoke-runbook.md`, add:
- one config step for a user with `pipeline_mode="sts"`
- one inbound-call verification step
- expected evidence in logs or DB:
  - dispatch created
  - agent joins room
  - caller/agent transcripts persisted
  - finalization job succeeds

- [ ] **Step 4: Review docs locally**

Run:

```bash
git diff -- apps/agent/.env.example docs/architecture/backend-context.md docs/architecture/staging-smoke-runbook.md
```

Expected: only STS-related documentation changes.

- [ ] **Step 5: Commit the docs**

```bash
git add apps/agent/.env.example docs/architecture/backend-context.md docs/architecture/staging-smoke-runbook.md
git commit -m "docs: add gemini sts rollout guidance"
```

### Task 6: Final verification

**Files:**
- Verify only

- [ ] **Step 1: Run the full agent test suite**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: all agent tests PASS.

- [ ] **Step 2: Spot-check the API suite for regressions at the boundary**

Run:

```bash
cd /home/i933k/code/ai/bmad-opevo/apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_health.py -v
```

Expected: PASS, confirming the backend boundary remains intact.

- [ ] **Step 3: Review the final diff**

Run:

```bash
git status --short
git diff --stat HEAD~4..HEAD
```

Expected:
- only the intended agent runtime, test, and docs files are changed
- `.env` files remain untracked

- [ ] **Step 4: Final implementation commit if needed**

If any verification-driven cleanup was required after earlier task commits:

```bash
git add apps/agent/agent apps/agent/tests apps/agent/.env.example docs/architecture
git commit -m "chore: finalize gemini sts rollout"
```

- [ ] **Step 5: Record staging follow-up**

Open a follow-up note in the implementation summary or commit message that the next manual verification is one real inbound STS call using a user configured with `pipeline_mode="sts"` and a valid `GEMINI_API_KEY`.
