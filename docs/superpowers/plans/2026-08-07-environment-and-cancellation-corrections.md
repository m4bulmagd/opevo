# Environment and API Cancellation Corrections Plan

> Owner-approved corrections: **51A + 52A**. Execute test-first with fresh
> independent Spec and Standards reviews before the complete final gate.

**Goal:** Make runtime environment selection one canonical, fail-closed
settings contract in both executables and directly prove API partial-startup
cancellation cleanup.

**Scope:** `apps/api`, `apps/agent`, their tests, and durable review evidence.
Realtime enablement, deployment, provider accounts, queue policy, database
contracts, worker extraction, coverage thresholds, and protected paths remain
out of scope.

## Task 20: Implement 51A at both settings boundaries

**Production files:**

- `apps/api/app/core/config.py`
- `apps/agent/agent/config.py`

**Test files:** use the existing API settings/composition/router suites and
agent settings/runtime-validation suites; do not create a broad new abstraction.

1. Add RED tests proving:
   - `development`, `test`, `staging`, and `production` are accepted;
   - case and surrounding whitespace canonicalize to those exact lowercase
     values from constructor and process sources;
   - empty, whitespace-only, misspelled, and custom values fail settings
     construction;
   - API test variants ignore dotenv and remain hermetic through composition,
     including no ARQ pool or realtime fanout;
   - a normalized development variant registers development-only routes; and
   - the agent cannot bypass production validation through case, whitespace, or
     a misspelling.
2. In each executable, define a local explicit `Literal` environment domain and
   a `mode="before"` Pydantic field validator that applies `strip().casefold()`
   to strings. Reuse the API normalization helper in pre-dotenv source
   selection. Do not add a cross-app package or normalize again in composition.
3. Run the focused API and agent settings, validation, composition, and router
   suites; then Ruff, mypy, diff, protected-path, and zero-stale-reference
   checks. Commit only 51A.
4. Require fresh Spec and Standards approvals with zero findings.

## Task 21: Implement 52A as a focused contract test

**Test file:** `apps/api/tests/composition/test_api_composition.py`

1. Add a RED/characterization test that raises one retained
   `asyncio.CancelledError` from a late construction seam after all preceding
   owned resources have been registered.
2. Assert the exact exception object and absent cause escape. Assert every
   earlier resource closes exactly once in reverse registration order. Avoid
   message-only assertions and sleeps.
3. Do not change production code unless the test exposes a real defect; if it
   does, stop and return the finding to the owner before editing production.
4. Run the focused composition suite, Ruff, mypy, diff, and protected-path
   checks. Commit only 52A and require fresh Spec and Standards approvals with
   zero findings.

## Task 22: Reverify and record the final endpoint

1. Run frozen lock, full Ruff, and full mypy gates for API and agent.
2. Use uniquely named disposable PostgreSQL and Redis containers on preflight-
   free ports. Capture the original seven local services before startup and
   remove only the exact disposable pair afterward.
3. Run the complete API and agent suites with unchanged strict line/branch
   ratchets, exactly the four approved agent credential skips, cross-runtime
   tests, and API/agent architecture guards.
4. Confirm protected paths, real `.env`, fixed `/tmp` overrides, deploy state,
   provider accounts, and non-isolated databases were untouched. Retain all six
   approved local ignored Task 3-8 reports.
5. Record 51A/52A, commit hashes, exact counts, coverage, cleanup, and review
   results in the original plan, correction plan, and engineering ledger.
6. Run fresh complete-range Spec and Standards reviews. Both must report zero
   findings before integration choices are offered.

If any gate fails, stop and present the concrete issue and options. Never lower
coverage thresholds, add coverage-only assertions, or silently broaden scope.

## Task 22 final6 execution evidence

The approved design/plan is commit `7624f34`. Task 20 used test-first RED
coverage for canonical accepted environments, constructor/process invalid
values, dotenv hermeticity, composition, development routes, and production
validation. **51A** GREEN is commit
`e591478f7f23e8dad621b6197fff2a55eed6a7a2`; independent Spec and Standards
reviews both approved it with zero findings.

Task 21 added the late-construction cancellation characterization before any
production edit. Existing behavior already retained the exact
`asyncio.CancelledError`, left its cause absent, and closed all earlier owned
resources exactly once in reverse order, so no production change was required.
**52A** is test-only commit
`219155c674cab3d5cc3f64d587c76cd2b68d9bfb`; independent Spec and
Standards/test-quality reviews both approved it with zero findings.

The first full post-52A API gate was the corrective RED for **53A**: 3,106 tests
passed, while the single dashboard reference-time `preview` parameter failed
because 51A correctly rejects `preview` before dashboard-specific validation.
Commit `7c93cb521b8e98bbf51b5e4c2226da943b5142e5` removed only that test overlap;
explicit API and agent constructor/process invalid-environment matrices still
cover `preview`. Independent Spec and Standards/test-quality reviews approved
53A with zero findings.

Fresh final6 GREEN evidence at the 53A code endpoint:

- frozen lock and complete Ruff gates passed for API and agent; mypy passed 187
  API and 16 agent source files;
- API: 3,106 passed, zero skipped/failed, one dependency warning;
  11,826/12,862 statements (91.945265%) and 2,688/3,342 branches
  (80.430880%); stored and stricter 91.93%/80.39% ratchets passed;
- agent: 732 passed and exactly four approved credentialed skips;
  1,360/1,517 statements (89.650626%) and 299/400 branches (74.75%); stored and
  stricter 89.44%/74.62% ratchets passed;
- cross-runtime: 104 passed; architecture: 38 API and seven agent passed; and
  explicit canonical/invalid APP_ENV slices: 16 API and 16 agent passed.

Exact ports 55472/56402 and final6 names were clear before startup. Both
isolated containers became healthy and only those exact containers were
removed. No matching container, network, volume, name, or listener remained;
the original seven running services and the pre-existing exited-success
one-shots matched preflight by ID/state. Obsolete references/files were absent,
locks and coverage baselines retained their hashes, protected/diff/status and
tracked-report audits were clean, and all six ignored Task 3-8 reports remain.
No protected path, real `.env`, fixed `/tmp` override, deployment, provider
account, non-isolated database, lock, baseline, or threshold was touched.

All Python pytest and coverage-check evidence above ran outside the filesystem
sandbox. The earlier in-sandbox timeout attempt is discarded execution-context
evidence and is not counted as a product failure. The corrected code endpoint
is `c56187794d3c12e0daca833f5f8f2e729e98eead...7c93cb521b8e98bbf51b5e4c2226da943b5142e5`.
This documentation-only evidence commit follows it. Task 22's definitive fresh
complete-range Spec and Standards reviews remain pending; integration must not
be offered until both approve the range including the evidence commit with zero
findings.

## Task 23: Implement post-merge dotenv isolation decision 54A

Post-merge verification exposed that direct agent settings construction can
inherit a developer dotenv file. The owner approved **54A**: direct
`AgentSettings(...)` construction is hermetic, and only the executable-root
`get_settings()` function explicitly requests `.env` loading.

1. Add RED tests in the existing agent settings/runtime-validation suite using
   a synthetic poison `.env` under `tmp_path`. Never inspect or copy a real
   dotenv file.
2. Prove direct `AgentSettings(...)` ignores the synthetic file while retaining
   constructor and process-environment precedence. Prove cached `get_settings()`
   explicitly loads the synthetic file and clean its cache in `finally` so test
   order cannot leak state.
3. Remove implicit `env_file` from `AgentSettings.model_config`; keep encoding,
   ignored-extra, canonicalization, and all other fields unchanged. Change only
   `get_settings()` to call `AgentSettings(_env_file=".env")`.
4. Do not add pytest detection, cwd hooks, global model mutation, a cross-app
   abstraction, or repeated `_env_file=None` arguments across tests.
5. Run focused RED/GREEN, full agent Ruff/mypy, source scans proving production
   construction remains centralized, and exact protected/status checks. Commit
   54A separately and require fresh Spec and Standards approvals with zero
   findings.

## Task 24: Complete merged-main verification and cleanup

1. Rerun the complete agent and API suites with coverage on merged `main` using
   fresh isolated PostgreSQL/Redis dependencies and the required outside-sandbox
   Python execution context. Keep all existing ratchets and the exact four agent
   credential skips.
2. Rerun focused environment and architecture checks affected by 54A. Stop on
   any genuine failure; do not weaken validation, thresholds, or assertions.
3. Record 54A, its commit, reviews, exact merged-main counts/coverage, and exact
   cleanup in this plan and the engineering ledger.
4. Preserve the six ignored Task 3-8 reports at their corresponding ignored
   paths in the main worktree before removing the owned feature worktree.
5. Remove only the owned `issue-6a-runtime-composition` worktree, prune its
   registration, and delete only the merged local feature branch. Preserve the
   protected frontend worktree and the untracked `Presvo_frontend/` directory.

The failed final8 agent run is valid RED evidence for test isolation only:
`730 passed`, four approved skips, and two production-variant failures whose
expected default validation was contaminated by an implicit dotenv value. The
API run was intentionally interrupted and is not completion evidence. Exact
final8 disposable resources were removed and all original services, locks,
baselines, protected paths, feature artifacts, and repository state were
preserved.

## Task 23–24 completion evidence

The owner approved **54A** and the design/continuation plan was committed as
`521206942420570040bfb0a094edb13569e711ab`. Test-first RED used only a
synthetic poison dotenv under `tmp_path`: direct settings construction consumed
the poison environment and API URL in two failing cases, while the companion
explicit-root loader case passed. No developer dotenv file was opened.

Commit `898f98f6306f29901afb12d244c252b6172a44ca` implements 54A in exactly
`apps/agent/agent/config.py` and
`apps/agent/tests/test_runtime_validation.py`. `AgentSettings` no longer names
an implicit dotenv file; cached `get_settings()` alone supplies
`_env_file=".env"`. Constructor, process-environment, and file-secret sources,
environment canonicalization, validation, Compose, credentials, and runtime
ownership remain unchanged. The owner separately approved **55A**, one live and
documented `# type: ignore[call-arg]` on that explicit loader call because
Pydantic accepts `_env_file` at runtime while mypy's synthesized subclass
signature omits it. No `Any`, cast, plugin, custom constructor, or settings
source override was added.

GREEN passed the three focused dotenv contracts, all 34 runtime-validation
tests, the formerly contaminated production variants, seven agent architecture
tests, the frozen lock check, full Ruff, and mypy across 16 agent source files.
Independent Spec and Standards reviews both approved 54A/55A with zero
findings.

Fresh merged-main final9 verification at `898f98f` produced:

- API: 3,106 passed with zero skips/failures and one known dependency
  deprecation warning; 92.01523868760691% statements and
  80.43087971274686% branches;
- agent: 735 passed with exactly the four approved credentialed skips and zero
  failures; 89.58470665787739% statements and 74.75% branches;
- stored ratchets and the stricter API 91.93%/80.39% and agent 89.44%/74.62%
  endpoints passed;
- frozen locks, complete Ruff, and mypy passed for 187 API and 16 agent source
  files; and
- focused API settings/composition/outbox architecture passed 69 tests, while
  agent runtime-validation/composition architecture passed 41 tests.

The prior exact cross-runtime slice was not repeated because none of its seven
selected files imports or constructs agent settings; the affected boundaries
are covered directly by the agent and API architecture gates above.

Final9 used only healthy `opevo-issue6a-final9-postgres` and
`opevo-issue6a-final9-redis` on preflight-free ports 55475 and 56405. Only those
exact containers were removed; name, listener, network, and volume filters were
empty afterward. The original seven running services and two exited-success
one-shots matched preflight by container ID and state. Locks and coverage
baselines retained their hashes. Tracked main remained clean except for the
opaque protected `Presvo_frontend/`, which was never inspected or touched. The
protected frontend worktree, feature worktree/branch, and six ignored Task 3-8
reports remained intact pending the final preservation and cleanup step.

## Finishing coverage decision — 56A and 56B-C

Exact-HEAD final10 verification passed all 3,106 API tests and all 735 agent
tests with exactly the four approved agent skips. The API stored ratchet passed,
but fresh branch coverage was 80.25134649910234%, below the temporary stricter
80.39% finishing endpoint reached by final9. Cleanup was exact and branch/
worktree removal stopped while the difference was diagnosed.

Approved read-only diagnosis **56A** forced Coverage.py's CTracer and ran three
explicit billing scenarios three times without contexts and three times with
per-test contexts. All six runs passed and produced identical normalized line
and arc sets. Contexts did not change attribution. The explicit reactivation
and final-cancellation bodies executed, but their controlling async condition
arcs were not credited. Existing tests already assert cancellation-operation
creation/reuse and persisted outbox events; wakeup signaling itself is not
directly asserted. The experiment ruled out dynamic contexts but did not locate
which other full-suite integration path accounts for the final9/final10
aggregate difference.

The owner selected **56B-C for now**: accept the committed coverage ratchets as
the finishing authority and do not add speculative tests, refactor production,
or change coverage configuration merely to reproduce the transient 80.39%
endpoint. This is an explicit accepted risk, not a claim that the variation is
explained. The recommended future follow-up remains expanded read-only
attribution (**56B-A**), followed—if justified—by focused service-level wakeup
characterization (**56B-B**). No threshold file or coverage baseline changed.

Final10 completion evidence at exact pre-decision documentation HEAD
`e22e43eb64f462bfc386995478cb3963e9254ad9`:

- API: 3,106 passed, zero skips/failures, one known dependency warning;
  91.96081480329653% statements and 80.25134649910234% branches; committed
  90.04%/77.76% ratchets passed;
- agent: 735 passed, exactly four approved skips, zero failures;
  89.58470665787739% statements and 74.75% branches; committed
  88.35%/71.07% ratchets passed; and
- exact final10 and diagnostic final11/final12 containers were removed, original
  services matched preflight, and protected paths, locks, baselines, reports,
  feature branch, and worktrees remained unchanged.
