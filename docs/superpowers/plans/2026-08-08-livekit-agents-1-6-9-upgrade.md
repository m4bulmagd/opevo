# LiveKit Agents 1.6.9 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Upgrade the Presvo voice agent from the coherent LiveKit Agents 1.4.4 package family to 1.6.9 while preserving its current voice behavior and replacing private or deprecated SDK integrations with supported public APIs.

**Architecture:** Keep the existing pipeline factories, provider selection, prompts, dispatch, and text-based multilingual endpointing. Move lifecycle ownership back to the LiveKit SDK, express endpointing through TurnHandlingOptions, and use the SDK's public asset downloader in the container build. Resolve and verify the dependency graph at a non-yanked 1.5.17 checkpoint before moving to 1.6.9.

**Tech stack:** Python 3.13, uv, LiveKit Agents and plugins, pytest, Ruff, mypy, pip-audit, Docker.

## Global Constraints

- Keep all seven LiveKit packages on one exact version at every checkpoint.
- Preserve MultilingualModel, endpointing values, providers, prompts, and dispatch behavior.
- Do not migrate to the audio inference.TurnDetector.
- Do not add or extend vulnerability exceptions.
- Do not modify deployment environments, provider accounts, secrets, or production services.
- Keep unrelated worktree changes untouched.

---

### Task 1: Establish the 1.5.17 dependency checkpoint

**Files:**

- Modify: apps/agent/pyproject.toml
- Modify: apps/agent/uv.lock
- Modify: apps/agent/tests/test_pipeline_factory.py

- [ ] **Step 1: Update the version assertion first**

Change the Deepgram plugin version assertion in test_pipeline_factory.py from 1.4.4 to 1.5.17.

- [ ] **Step 2: Run the focused test and confirm the expected failure**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_pipeline_factory.py

Expected: failure because the installed plugin still reports 1.4.4.

- [ ] **Step 3: Pin the coherent 1.5.17 family**

Set these exact dependencies to ==1.5.17 in pyproject.toml: livekit-agents, livekit-plugins-deepgram, livekit-plugins-elevenlabs, livekit-plugins-google, livekit-plugins-silero, livekit-plugins-speechmatics, and livekit-plugins-turn-detector.

- [ ] **Step 4: Regenerate and install the checkpoint lock**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv lock
    UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
    UV_CACHE_DIR=/tmp/uv-cache uv lock --check

Expected: all commands succeed and the resolved LiveKit family is 1.5.17.

- [ ] **Step 5: Verify the checkpoint**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_pipeline_factory.py tests/test_main.py tests/test_dispatch_compatibility.py tests/test_debug_streams.py
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent

Expected: all commands succeed.

- [ ] **Step 6: Commit the checkpoint**

    git add apps/agent/pyproject.toml apps/agent/uv.lock apps/agent/tests/test_pipeline_factory.py
    git commit -m "chore(agent): stage LiveKit Agents 1.5.17"

---

### Task 2: Move pipeline construction to public 1.6 lifecycle APIs

**Files:**

- Modify: apps/agent/tests/test_pipeline_factory.py
- Modify: apps/agent/agent/pipeline_factory.py
- Modify: apps/agent/tests/test_main.py
- Modify: apps/agent/agent/main.py

- [ ] **Step 1: Write failing public turn-handling tests**

Update runtime tests to require agent turn_handling endpointing values min_delay 0.25 and max_delay 1.5, require no min_endpointing_delay or max_endpointing_delay keyword, and require the text turn detector under session turn_handling rather than direct turn_detection.

Replace the executor-binding test with an assertion that build_agent_runtime has no inference_executor parameter. Add the same endpointing assertion for the speech-to-speech path. In test_main.py, capture pipeline factory arguments and assert that entrypoint does not pass inference_executor.

- [ ] **Step 2: Write a failing ElevenLabs public-option test**

Require the ElevenLabs STT fake to receive model="scribe_v2_realtime" and no model_id keyword.

- [ ] **Step 3: Run focused tests and confirm expected failures**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_pipeline_factory.py tests/test_main.py

Expected: failures reference legacy endpointing arguments, direct turn_detection, executor binding, and model_id.

- [ ] **Step 4: Implement public turn handling**

In pipeline_factory.py, import TurnHandlingOptions; remove _bind_turn_detector_executor and the inference_executor parameter; pass text turn detection through AgentSession turn_handling; pass endpointing through Agent turn_handling; apply the same endpointing configuration to speech-to-speech; and change ElevenLabs STT from model_id to model.

In main.py, stop passing context.inference_executor to the factory. Retain plugin import registration before worker startup.

- [ ] **Step 5: Run focused verification**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_pipeline_factory.py tests/test_main.py tests/test_dispatch_compatibility.py
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent

Expected: all commands succeed.

- [ ] **Step 6: Commit the public lifecycle migration**

    git add apps/agent/agent/pipeline_factory.py apps/agent/agent/main.py apps/agent/tests/test_pipeline_factory.py apps/agent/tests/test_main.py
    git commit -m "refactor(agent): use public LiveKit turn handling APIs"

---

### Task 3: Remove private Speechmatics prewarming

**Files:**

- Modify: apps/agent/tests/test_main.py
- Modify: apps/agent/agent/main.py
- Modify: apps/agent/pyproject.toml

- [ ] **Step 1: Replace the private prewarm test**

Install a fake speechmatics.voice._smart_turn.SmartTurnDetector that records calls. Assert that prewarm_assets does not import or initialize it and does not emit speechmatics_prewarm_failed.

- [ ] **Step 2: Run the focused test and confirm the expected failure**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_main.py -k prewarm

Expected: failure because current code still initializes SmartTurnDetector.

- [ ] **Step 3: Remove private integration code**

Delete the Speechmatics private import/setup block from prewarm_assets, remove its now-unused turn-mode helper import, and remove the mypy override for speechmatics.voice._smart_turn.

- [ ] **Step 4: Verify no application-owned private SDK imports remain**

    rg -n 'livekit\..*\._|speechmatics\.voice\._|_executor\s*=' apps/agent/agent apps/agent/tests apps/agent/pyproject.toml

Expected: no application integration match. Test doubles may use ordinary leading-underscore attributes only when unrelated to SDK internals.

- [ ] **Step 5: Run focused verification**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_main.py tests/test_pipeline_factory.py
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent

Expected: all commands succeed.

- [ ] **Step 6: Commit the private-API removal**

    git add apps/agent/agent/main.py apps/agent/tests/test_main.py apps/agent/pyproject.toml
    git commit -m "refactor(agent): remove private SDK prewarming"

---

### Task 4: Upgrade the coherent package family to 1.6.9

**Files:**

- Modify: apps/agent/pyproject.toml
- Modify: apps/agent/uv.lock
- Modify: apps/agent/tests/test_pipeline_factory.py

- [ ] **Step 1: Update the version assertion first**

Change the Deepgram plugin version assertion from 1.5.17 to 1.6.9.

- [ ] **Step 2: Run the version test and confirm the expected failure**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_pipeline_factory.py

Expected: failure because the installed plugin still reports 1.5.17.

- [ ] **Step 3: Pin and lock all seven packages at 1.6.9**

Set the same seven exact dependency pins to ==1.6.9, then run:

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv lock
    UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
    UV_CACHE_DIR=/tmp/uv-cache uv lock --check

Expected: all commands succeed and the resolved LiveKit family is 1.6.9.

- [ ] **Step 4: Inspect the resolved family**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv tree --frozen --depth 1

Expected: each direct LiveKit package is exactly 1.6.9 with no conflicting family version.

- [ ] **Step 5: Run compatibility verification**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q tests/test_pipeline_factory.py tests/test_main.py tests/test_dispatch_compatibility.py tests/test_debug_streams.py tests/test_composition.py tests/test_runtime_validation.py tests/test_verification_runtime.py
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent

Expected: all commands succeed. Any 1.6.9 API mismatch first receives a focused failing regression test before implementation changes.

- [ ] **Step 6: Commit the final package upgrade**

    git add apps/agent/pyproject.toml apps/agent/uv.lock apps/agent/tests/test_pipeline_factory.py
    git commit -m "chore(agent): upgrade LiveKit Agents to 1.6.9"

---

### Task 5: Use the public asset downloader in the container

**Files:**

- Modify: apps/agent/Dockerfile

- [ ] **Step 1: Replace the private downloader command**

Replace the _EUORunnerMultilingual._download_files() invocation with:

    RUN /app/.venv/bin/python -m livekit.agents download-files

- [ ] **Step 2: Verify the command locally**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m livekit.agents download-files

Expected: command exits successfully after discovering plugins and ensuring their assets are present.

- [ ] **Step 3: Build the agent container**

    docker build -f apps/agent/Dockerfile .

Expected: image build succeeds, including the public asset-download layer. If Docker is unavailable, record that environmental limitation without claiming container verification.

- [ ] **Step 4: Commit the container migration**

    git add apps/agent/Dockerfile
    git commit -m "build(agent): use public LiveKit asset downloader"

---

### Task 6: Reconcile security evidence with the final lock

**Files:**

- Modify if evidence requires: docs/security/dependency-exceptions.md
- Modify if evidence requires: .github/workflows/ci.yml
- Modify if evidence requires: .trivyignore.yaml

- [ ] **Step 1: Audit without exceptions**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/presvo-agent-requirements.txt
    UV_CACHE_DIR=/tmp/uv-cache uv run --with pip-audit pip-audit -r /tmp/presvo-agent-requirements.txt

Expected: capture the exact finding set for the 1.6.9 lock.

- [ ] **Step 2: Apply the evidence branch**

- If the audit is clean, remove obsolete agent audit ignores, documentation exceptions, and matching Trivy ignores.
- If the existing five vulnerability IDs and six rows remain, update only dependency paths and review evidence to the 1.6.9 graph; retain the existing expiry.
- If findings differ, do not add exceptions. Record the result as a release blocker for user review.

- [ ] **Step 3: Run the CI-equivalent audit**

Use the exact ignore arguments retained in .github/workflows/ci.yml, or none when the clean branch applies.

Expected: success only when repository policy exactly matches the final audited graph.

- [ ] **Step 4: Commit evidence-based security updates**

    git add docs/security/dependency-exceptions.md .github/workflows/ci.yml .trivyignore.yaml
    git commit -m "docs(security): reconcile LiveKit 1.6.9 audit evidence"

Skip the commit when no tracked file changed.

---

### Task 7: Run full verification and document the result

**Files:**

- Modify: docs/engineering/2026-07-30-agent-api-review-decisions.md
- Modify: docs/PROJECT_STATUS.md

- [ ] **Step 1: Run the complete agent quality gate**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv lock --check
    UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q --cov=agent --cov-report=term-missing --cov-report=json:coverage.json
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python ../../scripts/check_python_coverage.py check --report coverage.json --baseline coverage-baseline.json

Expected: lock, install, lint, types, tests, and coverage gate all succeed.

- [ ] **Step 2: Run credential-gated evaluations when available**

    cd apps/agent
    UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q -m livekit_eval

Expected: evaluations succeed when credentials are configured, or skip cleanly with their documented reason.

- [ ] **Step 3: Scan for old pins and private integrations**

    rg -n 'livekit-(agents|plugins-[a-z-]+)==(1\.4\.4|1\.5\.17)|_EUORunnerMultilingual|SmartTurnDetector|inference_executor=' apps/agent .github docs/security docs/engineering/2026-07-30-agent-api-review-decisions.md docs/PROJECT_STATUS.md

Expected: no current configuration or application integration matches. Historical documentation outside listed current-state files may retain historical versions.

- [ ] **Step 4: Update current-state documentation**

Mark the LiveKit API-review item complete with 1.6.9 evidence and update project status with the verification actually obtained. Distinguish local verification, skipped credential-gated evaluation, and unavailable Docker explicitly.

- [ ] **Step 5: Review the complete diff**

    git status --short
    git diff --check
    git diff --stat
    git diff HEAD~5 -- apps/agent .github docs/security docs/engineering/2026-07-30-agent-api-review-decisions.md docs/PROJECT_STATUS.md

Expected: no whitespace errors; only upgrade-related files are present.

- [ ] **Step 6: Perform the required code review**

Use the code-review skill against the pre-upgrade fixed point and address every actionable Standards or Spec finding. Re-run the affected focused test after each correction.

- [ ] **Step 7: Commit final documentation and review fixes**

    git add docs/engineering/2026-07-30-agent-api-review-decisions.md docs/PROJECT_STATUS.md
    git commit -m "docs(agent): record LiveKit 1.6.9 verification"

Include review fixes in an additional narrowly scoped commit.

- [ ] **Step 8: Re-run fresh final verification**

Repeat the complete agent quality gate from Step 1 and run:

    git status --short
    git log --oneline --decorate -8

Expected: all gates succeed and the only unrelated worktree entry remains the user's pre-existing Presvo_frontend/.
