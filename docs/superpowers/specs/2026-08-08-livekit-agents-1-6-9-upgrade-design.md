# LiveKit Agents 1.6.9 Upgrade Design

**Date:** 2026-08-08
**Status:** Approved

## Purpose

Upgrade the separately deployed Python voice worker from the coherent LiveKit
Agents `1.4.4` package family to the coherent `1.6.9` family before the first
production deployment. Remove the worker's LiveKit private-API dependencies
that make the upgrade unsafe, retain current customer-visible voice behavior,
and leave a reproducible dependency and verification boundary for future
upgrades.

## Context

The agent currently pins `livekit-agents` and the Deepgram, ElevenLabs, Google,
Silero, Speechmatics, and turn-detector plugins to `1.4.4`. The image build
downloads turn-detector assets through the private
`_EUORunnerMultilingual._download_files()` method, and runtime composition can
mutate a detector's private `_executor`. The Speechmatics smart-turn mode also
imports a private provider module. These seams have no compatibility promise.

The repository has no approved production deployment or production traffic.
That makes the current local/staging phase the lowest-risk migration window.
The existing architecture decision for Issue 8 already selects staged upgrade
and private-hook removal over freezing `1.4.4` or performing an
uncharacterized jump.

## Considered Approaches

### 1. Staged coherent-family migration (selected)

First establish the current `1.4.4` characterization baseline, then resolve and
validate the latest non-yanked `1.5` family, and finally resolve and validate
the exact `1.6.9` family. At each stage, every LiveKit Agents package used by
the worker stays on the same version. Replace private hooks with documented
public interfaces as soon as the target family provides them.

This costs an additional lock resolution and focused verification pass, but it
localizes whether incompatibility entered in the `1.5` or `1.6` family.

### 2. Direct coherent-family migration to 1.6.9

Update all seven packages to `1.6.9`, replace private hooks, and diagnose all
failures against the final target. This has fewer mechanical steps but mixes
two minor-version migrations and private-API removal into one failure surface.

### 3. Retain 1.4.4

Keep the known local runtime unchanged. This has no immediate migration cost,
but retains the private hooks, an aging dependency graph, and the overdue
security-exception review. It also moves the eventual migration into a more
expensive production window.

## Scope

### Included

- Pin these packages as one exact family, ending at `1.6.9`:
  - `livekit-agents`
  - `livekit-plugins-deepgram`
  - `livekit-plugins-elevenlabs`
  - `livekit-plugins-google`
  - `livekit-plugins-silero`
  - `livekit-plugins-speechmatics`
  - `livekit-plugins-turn-detector`
- Regenerate `apps/agent/uv.lock` under Python 3.13 and retain frozen installs.
- Replace the private Docker-time LiveKit asset downloader with the documented
  `python -m livekit.agents download-files` entry point.
- Remove LiveKit private executor mutation. Runtime construction must use only
  a documented constructor, lifecycle, or worker interface available in the
  selected family.
- Adapt public SDK signatures and event contracts where required without
  changing Presvo's domain or dispatch contracts.
- Re-run dependency auditing and update the security exception register only
  from the resolved final graph and fresh audit evidence.
- Update architecture/status documentation that names the old pin or private
  hooks.

### Excluded

- Migrating from the existing text `MultilingualModel` to LiveKit's built-in
  audio `TurnDetector`.
- Changing endpointing delays, interruption policy, VAD settings, STT/LLM/TTS
  provider selection, prompts, greetings, call-duration policy, or dispatch
  payloads.
- Enabling the hidden Gemini native-audio customer path.
- Deploying to production or claiming real-provider certification.
- Refactoring unrelated agent modules.

The audio detector is intentionally separate because current documentation
marks the text detector deprecated and gives the audio detector different
selection, fallback, CPU, and endpointing behavior. It needs its own latency,
false-endpoint, interruption, and French-language evaluation.

## Runtime Design

The agent remains a separately deployed LiveKit worker started through
`python -m agent.main start`. Its validated settings, process runtime,
dispatch parsing, session construction, lifecycle callbacks, transcript
delivery, and ordered shutdown remain Presvo-owned boundaries.

The dependency migration may change adapters at the LiveKit edge, but it must
not change Presvo's domain objects or internal API payloads. Compatibility
changes stay in the composition, pipeline, entrypoint, debug-stream, and image
build seams already responsible for the SDK.

The image builder invokes the public LiveKit download module after the final
dependencies are installed. A successful production-stage image build proves
the documented command can fetch every registered model asset and that the
non-root runtime image contains them.

The existing `MultilingualModel` remains explicitly configured when the
turn-detector flag is enabled. No implicit SDK default may silently substitute
a different turn detector during this migration.

## Error and Compatibility Policy

- Configuration failures continue to fail closed before accepting jobs.
- Provider setup failures retain their current sanitized Presvo error and
  logging boundaries.
- Cancellation and ordered shutdown must not be converted into ordinary
  provider failures.
- Removed or changed SDK symbols must be adapted through documented public
  APIs; compatibility shims may not reach into underscore-prefixed LiveKit
  members.
- The exact previous lockfile remains recoverable from Git history. Rollback is
  a code/image rollback, not a mixed-package downgrade at runtime.

## Testing Strategy

Before changing dependencies, run the focused compatibility, composition,
pipeline, entrypoint, debug-stream, and shutdown tests on `1.4.4`. Add a
characterization test only where a relevant Presvo boundary is not already
observable.

For production Python behavior changes, use red-green-refactor: write a test
that fails because the old private-hook contract remains, then implement the
public replacement and watch the focused test pass. Dependency pins, the
generated lockfile, and the Docker build command are configuration/build
artifacts; they are validated by frozen resolution, imports, test execution,
the asset-download command, and the container build rather than brittle tests
that grep source text.

Each dependency stage runs:

- frozen lock validation and synchronization;
- focused SDK compatibility and pipeline tests;
- Ruff and mypy for the agent;
- the noncredentialed agent test suite.

The final `1.6.9` state additionally runs:

- the coverage ratchet;
- a fresh dependency audit using the repository's exact audit procedure;
- the agent production image build and health/import smoke checks;
- a scan confirming application code no longer accesses private LiveKit
  members.

Credential-gated LiveKit behavioral evaluations and a manual real-provider
call matrix remain required before production promotion. Local verification
must report them as unexecuted when credentials or an approved staging run are
not available; it must not imply provider certification.

## Acceptance Criteria

1. The seven LiveKit packages are exactly `1.6.9` in `pyproject.toml` and the
   frozen lock resolves that coherent family on Python 3.13.
2. No application or Docker code imports `_EUORunnerMultilingual`, mutates a
   LiveKit `_executor`, or otherwise accesses an underscore-prefixed LiveKit
   API.
3. The worker keeps its explicit existing turn-detector/provider behavior and
   Presvo dispatch/session/shutdown contracts.
4. Focused tests, the complete noncredentialed agent suite, Ruff, mypy,
   coverage, lock validation, dependency audit, and container validation have
   fresh recorded outcomes.
5. Security and architecture documentation reflects the final resolved graph
   and distinguishes local verification from pending staging certification.
6. No production deployment, external provider mutation, push, or pull request
   is performed as part of this change.
