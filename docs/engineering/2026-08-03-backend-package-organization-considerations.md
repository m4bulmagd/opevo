# Backend Package Organization: Future Considerations

- **Date:** 2026-08-03
- **Scope:** `apps/agent`, `apps/api`, and the shared Python libraries used by
  both processes
- **Status:** Architecture debt and candidate direction recorded; no
  reorganization is approved or required by this document

## Purpose

This note preserves why Opevo should reconsider its backend package
organization as the product grows. It is intentionally not a binding directory
specification or an implementation plan. The exact package names should be
re-evaluated against the code and upcoming feature when work begins.

The immediate concern is the flat `apps/agent/agent` package. The broader
concern is that both Python applications increasingly mix process startup,
feature policy, orchestration, persistence, and external adapters. A local
agent-only cleanup would help, but it would not fully address responsibilities
that cross the API/agent seam, especially the planned conversation-flow
runtime.

The preferred direction is an incremental, feature-led migration across both
applications. Do not perform a big-bang rewrite solely to match the example
trees in this note.

## Current Responsibility Map

Opevo currently has three important backend ownership areas:

- `apps/api` is the control plane and durable authority. It owns authenticated
  product operations, PostgreSQL state, lifecycle policy, outbox delivery,
  background reconciliation, and dispatch admission.
- `apps/agent` is the real-time voice runtime. It owns LiveKit job admission,
  media/session construction, provider composition, live conversation event
  handling, call limits, transcript forwarding, and runtime finalization.
- `libs/shared/opevo_contracts` owns typed wire contracts exchanged across
  process seams. It should not become a home for application orchestration.

That process-level split is sound. The main issue is the locality of
responsibilities inside each process and the clarity of their interfaces.

## Why Reconsider the Structure

### `apps/agent` is flat while responsibilities are no longer small

At this snapshot, `apps/agent/agent` contains 15 Python files and approximately
2,903 lines. The largest files are:

| File | Approximate lines | Responsibilities currently concentrated there |
|---|---:|---|
| `session_runtime.py` | 713 | Transcript buffering and persistence, task ownership, call limits, realtime events, completion, and finalization |
| `observability.py` | 511 | OpenTelemetry setup, exporter safety, spans, shutdown, normalization, and failure reporting |
| `main.py` | 406 | Worker configuration, request admission, dispatch routing, dependency construction, event handlers, greetings, call limits, prewarm, and the CLI entrypoint |
| `api_client.py` | 303 | Agent-to-API transport, retry behavior, and request/response handling |
| `pipeline_factory.py` | 280 | Provider configuration, plugin loading, speech component construction, session construction, and agent construction |

Line count alone is not a reason to split a module. For example,
`observability.py` may remain cohesive even if it is large. The stronger signal
is the number of independent reasons a file changes and the breadth of
knowledge its callers require.

Specific maintainability issues include:

- `main.py` is both the executable entrypoint and the primary orchestration and
  composition module. Changes to worker boot, call behavior, providers, or
  LiveKit event handling can collide there.
- `SessionRuntime` exposes one class while coordinating several independently
  changing policies. It is a useful facade, but its implementation now needs
  internal modules with narrower ownership.
- `pipeline_factory.py` combines configuration policy, dynamic third-party
  imports, provider construction, voice-session construction, and agent
  construction.
- `main.py` imports the private
  `_resolve_speechmatics_turn_detection_mode` helper from
  `pipeline_factory.py`, which is evidence that the existing interface does not
  cleanly contain its implementation.
- `EventPublisher` is a shallow wrapper and its default path creates its Redis
  dependency implicitly. This hides resource lifetime and makes isolation more
  difficult.
- `agent_scripts.py` contains unused message constants and has no known
  consumers. Keeping abandoned placeholders makes ownership less clear.
- Many tests patch module internals because process wiring and behavior live in
  the same modules. This increases the chance that a behavior-preserving move
  causes broad test churn.

### `apps/api` has directories, but they are mostly technical layers

`apps/api/app` is more structured, but a feature commonly spans `routers`,
`schemas`, `services`, `repositories`, `models`, `providers`, and `workers`.
That layout is understandable today, yet locality decreases as each domain
grows: one product change may require navigating and changing many distant
directories.

Current pressure points include:

- `workers/jobs/outbox_topics.py` is approximately 1,625 lines and handles
  phone provisioning, phone routing, LiveKit call dispatch, verification
  dispatch, summaries, and recordings. This was already accepted as a
  cross-domain god-module issue in the July 2026 architecture review.
- Dependency construction is spread across API lifespan setup, request
  dependencies, webhook paths, worker jobs, and the agent process. Resource
  ownership and lifetime are not always visible from one composition root.
- `routers/agent.py` contains both customer-facing agent configuration and
  internal agent-runtime endpoints for authentication, transcript append, and
  call completion. Those surfaces have different callers and reasons to
  change.
- Large lifecycle modules are distributed by technical role. The individual
  files can be cohesive, but understanding a complete capability such as
  activation, recording, or dispatch requires reconstructing the feature
  across many directories.
- Worker responsibilities are grouped under one process-oriented tree even
  where critical call-lifecycle work and slower provider/background work have
  different operational requirements.

The July 2026 review already selected two compatible directions: split outbox
topics by cohesive family, and establish one thin manual composition root per
process. This note extends those decisions into a possible long-term package
shape without superseding them.

### Planned conversation flows increase the cost of unclear ownership

The Phase 4 conversation-flow builder will add a typed flow model, validation,
versioning, simulation, transitions, runtime state, trace events, and a LiveKit
execution adapter. Putting those responsibilities directly into `main.py`,
`SessionRuntime`, `pipeline_factory.py`, or API technical-layer folders would
make already broad modules harder to reason about.

Before or during that feature, the codebase needs clear answers to these
questions:

- Which module owns the pure state-machine semantics?
- Which process owns draft, published version, and immutable call-version
  selection?
- Which adapter translates flow actions into LiveKit session behavior?
- Where are flow run state and trace events persisted?
- Can the prompt-only agent and flow-driven agent share call lifecycle behavior
  without branching through the entire worker?

Package reorganization is useful only if it makes those interfaces explicit.

## Risks If the Structure Remains Unchanged

- New voice modes will add conditionals to central runtime files rather than
  entering through a stable strategy interface.
- API features will continue to spread policy across technical layers and large
  worker modules, increasing change collisions and review surface.
- Hidden construction and global settings access will keep unit tests dependent
  on monkeypatching internal names.
- Internal imports will become de facto public interfaces, making later moves
  progressively more expensive.
- A conversation-flow feature may accidentally couple the pure state machine
  to LiveKit, FastAPI, SQLAlchemy, or Redis, reducing simulation and testing
  options.
- Onboarding new maintainers will require knowledge of file history rather than
  package ownership and explicit dependency direction.

## Non-goals

This future work should not:

- combine the API and agent into one deployable process;
- replace the existing typed cross-process contracts with direct imports;
- introduce a dependency-injection framework or service locator;
- create generic `utils`, `common`, `helpers`, or `core` dumping grounds;
- split every large file by size when its responsibility is cohesive;
- create empty directories for speculative features;
- change runtime behavior, database schemas, wire contracts, or provider
  semantics merely as part of moving packages;
- attempt a strict hexagonal rewrite where no real alternative adapter or test
  seam exists.

## Approaches Considered

### 1. Incremental feature-led migration across both apps — recommended

Define responsibility and dependency rules now. Move code only when a feature
or an accepted debt item touches it, beginning with low-risk leaves and process
composition roots. New conversation-flow code starts in the intended modules
instead of first entering legacy central files.

This approach limits regression risk, allows each move to retain focused
behavioral evidence, and avoids pausing product delivery for a large rewrite.
Its cost is a temporary mixed structure and the need for disciplined migration
rules.

### 2. Reorganize only `apps/agent`

This directly addresses the flattest package and would make the voice runtime
easier to extend. It is a reasonable first implementation phase, but not a
complete long-term solution: flow versioning, publishing, dispatch, durable
traces, and worker responsibilities remain in the API.

### 3. Big-bang backend reorganization

Move both applications into their final package trees in one program. This
produces consistency sooner but has the highest import, deployment, migration,
and test-regression risk. It also encourages speculative abstractions before
the conversation-flow interfaces are proven. It is not recommended without a
separate, measured reason that incremental migration cannot satisfy.

## Candidate Responsibility Shape

The trees below illustrate ownership. They are not an instruction to create
every package or retain every name.

### Candidate `apps/agent` shape

```text
apps/agent/agent/
├── __init__.py
├── main.py                         # Stable, tiny CLI shim
├── worker/
│   ├── app.py                      # Composition root and WorkerOptions
│   ├── dispatch.py                 # Job validation and dispatch-type routing
│   └── prewarm.py                  # Model/plugin asset prewarming
├── calls/
│   ├── customer/
│   │   ├── runner.py               # Orchestrates one customer call
│   │   ├── runtime.py              # Deep call-lifecycle facade
│   │   ├── transcripts.py          # Buffering and persistence policy
│   │   ├── limits.py               # Duration/minute limit behavior
│   │   ├── handlers.py             # LiveKit event translation
│   │   ├── prompt_agent.py         # Prompt-only conversation strategy
│   │   └── prompts.py              # Prompt assembly
│   └── verification.py             # Forwarding-verification call behavior
├── voice/
│   ├── config.py                   # Provider-independent voice choices
│   ├── session_factory.py          # LiveKit AgentSession construction
│   ├── agent_factory.py            # Agent/strategy construction
│   └── instrumentation.py          # Voice-pipeline diagnostics
├── integrations/
│   ├── opevo_api.py               # Agent-to-API adapter
│   └── realtime.py                 # Optional realtime event adapter
└── platform/
    ├── settings.py
    ├── validation.py
    ├── safe_logging.py
    └── telemetry.py
```

`main.py` should remain as a stable CLI shim because the Docker image and local
Compose configuration invoke `python -m agent.main start`. The executable
contract can remain stable while its implementation moves behind the worker
module.

The customer call runtime should be a deep module: callers should be able to
record caller speech, record agent speech, start/enforce limits, and finalize a
call without learning transcript sequencing, background task ownership,
completion retries, or event-publication details.

### Candidate `apps/api` shape

```text
apps/api/app/
├── main.py                         # FastAPI construction shim
├── bootstrap/
│   ├── api.py                      # API composition root and lifespan
│   └── worker.py                   # Worker composition root
├── platform/                       # Database, auth, config, logging, telemetry
├── accounts/                       # Account lifecycle and access policy
├── activation/                     # Readiness, onboarding, go-live, forwarding
├── receptionist/                   # Agent configuration and future flow authoring
├── calls/                          # Call admission, lifecycle, history, transcripts
├── billing/                        # Subscriptions, usage, checkout, billing queries
├── telephony/                      # Number ownership, provisioning, routing
├── recordings/                     # Egress, storage lifecycle, reconciliation
├── notifications/                  # Notification policy and delivery
├── integrations/                   # Clerk, Stripe, Telnyx, LiveKit, storage, LLM adapters
└── workers/
    ├── critical/                   # Correctness-critical call lifecycle work
    ├── background/                 # Slower provider and reconciliation work
    └── outbox/
        ├── delivery.py
        ├── registry.py
        ├── failures.py
        ├── phone.py
        ├── livekit.py
        └── post_call.py
```

Each feature package should contain only the technical files it needs, such as
`router.py`, `schemas.py`, `models.py`, `repository.py`, `service.py`, or
`policy.py`. Do not reproduce every layer in every package. Public package
interfaces should expose supported feature operations; callers should not
reach through to repository or provider internals.

Some capabilities cross the example names. For example, activation coordinates
telephony and receptionist readiness, while calls coordinate dispatch and
recording. Those cases should use narrow feature interfaces rather than merge
all coordinated behavior into one package.

### Shared libraries

Shared libraries should be limited to logic that genuinely runs in more than
one process or must remain technology-independent:

```text
libs/shared/opevo_contracts/        # Versioned wire contracts only
libs/flow/opevo_flow/               # Future pure flow model and state machine
```

The future flow library should not import FastAPI, LiveKit, SQLAlchemy, Redis,
or provider SDKs. The API would own draft/published flow persistence and
immutable version selection. The agent would own the LiveKit adapter that
executes a published flow during a call. Both processes may use the same pure
validation and transition semantics.

## Intended Dependency Direction

The package layout should be backed by enforceable dependency rules:

- `platform` imports no product feature package.
- Agent `voice` may depend on agent `platform` and voice provider SDKs, but not
  on customer call orchestration or API transport implementations.
- Agent `integrations` may depend on platform and shared contracts, but not on
  worker entrypoints.
- Agent `calls` depends on narrow injected interfaces for persistence and event
  publication, not concrete HTTP or Redis construction.
- API feature packages may depend on platform primitives and their own models,
  but should call other features through explicit interfaces rather than
  importing their repositories.
- External provider implementations live in adapters selected by a process
  composition root.
- Worker registries compose feature-owned handlers; feature modules do not
  import worker startup code.
- `main.py` files import only their composition modules and framework CLI/app
  entrypoints.
- Cross-process communication uses `opevo_contracts`; neither application
  imports the other application's implementation.

These rules should eventually be checked with an import-boundary tool such as
`import-linter`, plus ordinary type checking and tests. Add the tool only when
the first real rules are ready to enforce.

## Conversation-Flow Placement Under This Direction

The planned advanced agent can fit without replacing the prompt-only agent:

```text
apps/api/app/receptionist/flows/     # Drafts, versions, publishing, validation entrypoint
apps/api/app/calls/flow_runs/        # Durable run and trace persistence
apps/agent/agent/calls/customer/flow/
├── runner.py                        # Call-facing flow strategy
├── livekit_adapter.py               # Flow actions to LiveKit behavior
└── checkpointing.py                 # Runtime state handoff/persistence adapter
libs/flow/opevo_flow/               # Pure model, validator, executor, simulator
```

At dispatch time, the API should select either the existing prompt-only
configuration or an immutable published flow version. The agent call runner
should then select a conversation strategy behind one narrow interface. Common
call lifecycle behavior—transcripts, limits, completion, telemetry, and
disconnect handling—should remain outside both conversation strategies.

This separation is the main reason to address package ownership before the
flow runtime spreads across current central modules.

## Safe Migration Strategy

1. **Record and enforce invariants first.** Characterize entrypoints, startup
   and shutdown, call lifecycle, transcript ordering, dispatch contracts,
   retry behavior, and provider failure mapping before moves.
2. **Move low-risk leaves.** Start with settings, validation, safe logging,
   API transport, and optional realtime adapters. Avoid behavior changes in the
   same commit.
3. **Create explicit composition roots.** Centralize construction and resource
   lifetime for the API, worker, and LiveKit agent processes using manual typed
   dependencies.
4. **Split cohesive agent behavior behind the existing runtime interface.**
   Extract transcripts, limits, and handlers internally before changing the
   external call-runner interface.
5. **Apply the already accepted outbox split.** Preserve one explicit registry
   and move handlers by cohesive topic family with characterization tests.
6. **Migrate API features when touched.** New work enters the feature package;
   nearby existing code moves only when it improves the same change. Avoid a
   repository-wide mechanical relocation.
7. **Introduce the pure flow module before the visual editor.** Prove the flow
   model, validator, simulator, versioning, and runtime adapter before adding a
   canvas or reusable tool nodes.
8. **Add import rules after real seams exist.** Prevent dependency regression
   without freezing a speculative tree.
9. **Remove temporary compatibility imports promptly.** Long-lived forwarding
   modules would create two apparent homes for the same responsibility.

Tests should mirror feature ownership and exercise observable behavior through
module interfaces. Once replacement tests cover a deepened module, delete
tests that exist only to lock old private structure.

## When to Start This Work

Package movement should be scheduled when at least one of these conditions is
true:

- implementation of the conversation-flow runtime is about to begin;
- an accepted architecture item such as the outbox split or process
  composition roots is scheduled;
- a feature repeatedly changes four or more technical-layer directories;
- another conversation mode or call type would add central branching to
  `main.py`, `SessionRuntime`, or `pipeline_factory.py`;
- tests increasingly require patching private module names or global settings;
- circular imports or unclear resource ownership begin blocking safe changes.

The reorganization should not start merely because a file crosses an arbitrary
line-count threshold.

## Success Criteria for a Future Reorganization

A future implementation is successful when:

- the API, API worker, and agent each have one visible composition root;
- the existing Docker and CLI entry contracts remain stable or have an
  explicitly planned migration;
- a maintainer can locate a feature's policy, persistence, transport, and
  background behavior from one package entrypoint;
- prompt-only and flow-driven conversations plug into the same call-lifecycle
  facade without spreading mode checks through unrelated modules;
- shared flow execution can be simulated without LiveKit, a database, Redis,
  or network credentials;
- cross-process data remains versioned and validated through shared wire
  contracts;
- import direction is statically checked;
- startup/shutdown, behavior, typing, coverage, and process-level tests remain
  green after every migration step;
- the migration does not silently change provider, transaction, retry,
  redaction, or call-finalization semantics.

## Decisions to Revisit Before Implementation

- Final feature terminology, especially `receptionist`, `agent`, `calls`, and
  `voice`, so package names match the product domain language.
- Whether the API should migrate feature-by-feature at its root or retain some
  current technical-layer directories during a longer transition.
- The smallest stable conversation-strategy interface shared by prompt-only
  and flow-driven calls.
- Ownership of flow checkpoints and trace persistence under process restarts.
- Whether API and background workers remain one deployment with separate queue
  classes or become separately scaled processes.
- Which import rules provide value without forcing seams that have only one
  implementation.

## Related Records

- [`PROJECT_STATUS.md`](../PROJECT_STATUS.md), especially Phase 4 —
  Conversation-flow builder
- [`2026-07-30-agent-api-review-decisions.md`](2026-07-30-agent-api-review-decisions.md),
  especially issues 4–6 and 8
- [`agent-config-api.md`](../architecture/agent-config-api.md)
- [`integration-endpoints.md`](../architecture/integration-endpoints.md)
