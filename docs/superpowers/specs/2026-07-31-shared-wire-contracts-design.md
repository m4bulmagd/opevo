# Shared Agent/API Wire Contracts Design

**Date:** 2026-07-31
**Decision:** 2A
**Status:** Approved design pending written-spec review

## Purpose

Create one deliberately small, versioned Python package for the JSON contracts
that cross the API, agent, LiveKit, and Redis process seams.

This design replaces duplicated models, literals, validation, acknowledgement
inspection, and source-text synchronization tests with one tested contract
module. It does not move business logic or infrastructure adapters into a
shared package.

## Context

The review decision record identifies these concrete problems:

- `apps/agent/agent/schemas.py` independently defines dispatch, transcript, and
  completion payloads.
- `apps/api/app/schemas/livekit.py`,
  `apps/api/app/schemas/agent_runtime.py`, and
  `apps/api/app/schemas/calls.py` define overlapping payloads.
- The realtime Redis channel prefix is repeated in the API, agent, and
  `libs/shared`.
- `libs/shared/test_contract.py` compares source text rather than serialized
  behavior.
- API and agent acknowledgements are validated through repeated dictionary
  inspection.
- Two different payload shapes currently use the same `call_ended` realtime
  event name.

These are deployment seams. A producer can currently emit JSON that a consumer
cannot parse even though the repository's source-text test remains green.

## Confirmed Product and Delivery Constraints

- Nothing is currently deployed, so no unversioned production payload needs a
  compatibility adapter.
- Version 1 is strict from its first release. Missing `schema_version` is not
  treated as an implicit legacy version.
- API and agent will remain in this monorepo and will be built from a repository
  checkout.
- The applications retain independent `pyproject.toml` files and lockfiles.
- Consumers tolerate unknown additive JSON fields.
- Consumers continue to reject unknown semantic values, malformed payloads,
  missing required fields, and unsupported schema versions.
- Realtime remains disabled in this wave. Enabling it belongs to decisions 1A
  and 14A.
- Accepted risks 11C and 12C remain unchanged: real-model behavior evaluation
  and a real agent-process E2E are not added by this work.

## Goals

1. Make each current API/agent wire shape explicit and versioned.
2. Give producers and consumers one source of truth for fields, values, limits,
   and serialization.
3. Make additive evolution safe without requiring synchronized deployments.
4. Make future breaking changes follow a tested consumer-first N/N-1 sequence.
5. Replace source-text checks with golden JSON and behavioral compatibility
   tests.
6. Remove duplicated validation and acknowledgement logic aggressively.
7. Prevent validation failures from leaking tokens, transcripts, prompts, or
   raw provider payloads.
8. Keep the module small enough that deleting it would force meaningful
   contract logic back into several callers.

## Non-Goals

- Moving database models, repositories, domain services, provider adapters,
  HTTP clients, Redis clients, LiveKit clients, retry policy, or logging into
  the shared package.
- Creating a dependency-injection framework.
- Publishing an internal package registry artifact.
- Creating a repository-wide uv workspace or unified lockfile.
- Generating models from JSON Schema or OpenAPI.
- Enabling browser realtime.
- Adding durable event replay, socket backpressure, or API resynchronization.
- Supporting an unversioned or v0 payload.
- Implementing speculative v2 models.
- Changing accepted decisions 11C or 12C.

## Selected Approach

Use Pydantic v2 models in a local package named `presvo-contracts`.

This is preferred over standard-library dataclasses because both applications
already depend on Pydantic and require runtime validation. It is preferred over
schema/code generation because all current consumers are Python and a generator
would add build and review complexity without current leverage.

The package version and the wire schema version are separate:

- Distribution name: `presvo-contracts`
- Initial package version: `0.1.0`
- Python import package: `presvo_contracts`
- Python requirement: `>=3.13,<3.14`
- Runtime dependency: `pydantic>=2.12,<3`
- Build backend: Hatchling
- Initial wire schema version: integer `1`

## Module Structure

```text
libs/shared/
├── pyproject.toml
├── src/
│   └── presvo_contracts/
│       ├── __init__.py
│       ├── versioning.py
│       ├── dispatch.py
│       ├── transcript.py
│       ├── completion.py
│       └── realtime.py
└── tests/
    ├── fixtures/
    │   └── v1/
    └── test_*.py
```

Responsibilities:

- `versioning.py` owns the current version, supported-version set, safe contract
  error codes, and common parsing/serialization behavior.
- `dispatch.py` owns customer-call and forwarding-verification LiveKit
  metadata.
- `transcript.py` owns the speaker vocabulary, transcript segment, append
  request, and append acknowledgement.
- `completion.py` owns call and verification completion requests and
  acknowledgements.
- `realtime.py` owns the internal event union, event discriminators, channel
  prefix, and channel-name construction.
- `__init__.py` exposes only the intentional public interface. Internal helpers
  remain private.

No module may import from either application.

## Packaging and Build Design

Both applications declare `presvo-contracts` as a normal runtime dependency and
resolve it from `../../libs/shared` through `tool.uv.sources`. Each application
records the local source in its own lockfile.

The repository does not introduce a package registry. A source checkout is the
release source of truth, while each built API or agent image contains its own
installed copy of the package.

API and agent Docker builds use the repository root as their build context so
the application and `libs/shared` are both available. Their Dockerfiles retain
application-specific dependency installation and runtime images. Production
installation is non-editable so the runtime virtual environment does not
reference build-stage source paths.

A root `.dockerignore` excludes, at minimum:

- `.git`
- worktrees
- virtual environments
- coverage artifacts
- dependency caches
- `node_modules`
- environment files and secrets
- unrelated local directories such as `Presvo_frontend`

Docker Compose build declarations and the CI container-scan matrix identify the
root context and the application-specific Dockerfile explicitly. The web build
remains application-scoped.

## Versioning Rules

Every independently transmitted JSON document contains a required
`schema_version` field whose value is exactly `1`.

Nested value objects do not repeat the version. For example, a call-completion
request has one top-level version and contains unversioned transcript segment
values.

Version rules:

1. Missing, boolean, non-integer, negative, or unsupported versions fail as
   `unsupported_schema_version` or `missing_schema_version`, as applicable.
2. Version selection occurs before payload-specific validation.
3. Additive fields do not require a new schema version.
4. Removing a field, changing its meaning, tightening a previously accepted
   value, or changing a discriminator is a breaking version change.
5. A future v2 rollout is consumer-first:
   - deploy consumers that accept v1 and v2;
   - prove both fixture sets in CI;
   - deploy producers that emit v2;
   - observe until old producers are gone;
   - remove v1 only in a later reviewed change.
6. No v0 or missing-version fallback is implemented because no legacy
   deployment exists.

The package exposes the current and supported version values so tests and
tooling do not repeat them. Versioned model fields default to the current
version for producer construction, but consumer parsing checks that the raw
document actually contains `schema_version` before model validation. A missing
wire version therefore cannot acquire the producer default accidentally.

## Producer and Consumer Strictness

The same model definitions serve producers and consumers, but validation mode
is explicit at the seam:

- Producer paths validate input with `extra="forbid"`. A misspelled or
  undeclared field is an internal defect and must fail before transmission.
- Consumer paths validate input with `extra="ignore"`. Unknown additive fields
  are discarded after the known fields pass validation.
- Unknown discriminators, enum values, required fields, and schema versions are
  never ignored.
- Application code does not call `json.dumps()` on ad hoc dictionaries for a
  shared payload.
- Application code does not inspect acknowledgement dictionaries manually.

The public construction and serialization interface is:

```python
create_contract(model_type, /, **values)
parse_contract(model_type, value)
dump_contract(contract)
dump_contract_json(contract)
parse_dispatch(value)
parse_realtime_event(value)
```

- `create_contract` validates with `extra="forbid"` and supplies the current
  schema version.
- `parse_contract` accepts an already decoded JSON object, string, or bytes;
  checks the explicit version; and validates with `extra="ignore"`.
- `dump_contract` returns JSON-compatible declared fields.
- `dump_contract_json` returns the equivalent JSON string.
- The two union parsers select their model through the declared discriminator
  only after the version check.

Application producer and consumer code must use these entry points rather than
selecting a validation mode independently. Package tests may construct models
directly when testing model-level invariants.

## Contract Inventory

### Dispatch

`CustomerCallDispatch` contains:

- `schema_version`
- `job_type = "customer_call"`
- `call_id`
- `user_id`
- `agent_config_id`
- `agent_identity`
- `agent_name`
- `owner_name`
- optional `owner_context`
- `system_prompt`
- `knowledge_base`
- `pipeline_mode`
- `minutes_remaining`
- `allowed_duration_seconds`
- `dispatch_token`

`ForwardingVerificationDispatch` contains:

- `schema_version`
- `job_type = "forwarding_verification"`
- `verification_session_id`
- `user_id`
- `agent_identity`
- `completion_token`
- the bounded verification message
- `tts_provider`

Both variants require an explicit `job_type`. The agent's current implicit
customer-call fallback is deleted.

Dispatch identifiers that represent UUIDs are validated as UUIDs. Identity
strings and tokens are non-empty. Durations are positive, remaining minutes are
non-negative, and the existing content limits remain authoritative:

- agent name: 100 characters
- owner name: 255 characters
- owner context: 4,000 characters
- system prompt: 8,000 characters
- knowledge base: 32,000 characters

Sensitive token fields remain serializable but are excluded from ordinary model
representations.

### Transcript

`TranscriptSpeaker` contains exactly:

- `CALLER`
- `AGENT`

`TranscriptSegment` contains:

- positive `sequence_number`
- `speaker`
- stripped, non-empty `text` of at most 4,000 characters

`TranscriptAppendRequest` contains:

- `schema_version`
- `segment: TranscriptSegment`

`TranscriptAppendAcknowledgement` contains:

- `schema_version`
- `status`, exactly `stored` or `duplicate`
- the acknowledged positive sequence number

The client verifies that the acknowledgement sequence matches the submitted
segment. The shared model validates shape; the client retains correlation
validation because it compares two separate messages.

### Completion

`CallCompletionRequest` contains:

- `schema_version`
- non-negative `duration_seconds`
- `transcript`, containing at most 2,000 recovery transcript segments

The call ID remains in the authenticated HTTP path and the dispatch token
remains in its header. They are not duplicated in the JSON body.

`CallCompletionAcknowledgement` contains:

- `schema_version`
- `status = "accepted"`
- `queued = true`
- non-empty `job_id`

The agent retains the correlation rule that `job_id` must match the expected
call-finalization job for the path call ID.

`VerificationCompletionRequest` contains only `schema_version`. The session ID
remains in the path and the verification token remains in its header.

`VerificationCompletionAcknowledgement` contains:

- `schema_version`
- `status = "verified"`
- the verification session ID

The agent retains the correlation rule that the acknowledged session equals the
requested path session.

### Realtime

`TranscriptObservedEvent` contains:

- `schema_version`
- `type = "transcript_observed"`
- `user_id`
- `call_id`
- transcript `sequence_number`
- transcript `speaker`
- transcript `text`

`CallStartedEvent` contains:

- `schema_version`
- `type = "call_started"`
- `user_id`
- `call_id`
- `room_name`

`AgentSessionEndedEvent` contains:

- `schema_version`
- `type = "agent_session_ended"`
- `user_id`
- `call_id`
- non-negative `duration_seconds`

`CallFinalizedEvent` contains:

- `schema_version`
- `type = "call_finalized"`
- `user_id`
- `call_id`
- non-negative `minutes_charged`
- optional `summary_text` of at most 8,000 characters

The two distinct ending events replace the ambiguous `call_ended` name:

- `agent_session_ended` reports media-session termination and is not durable
  business completion.
- `call_finalized` reports API-authoritative durable completion facts.

`realtime_channel(user_id)` is the only channel-name constructor and uses the
single shared `realtime:user:` prefix.

The API fanout adapter verifies that the user ID encoded in the subscribed
channel equals the event's `user_id`. A mismatch is a tenant-isolation failure:
the event is discarded, a safe metric/log code is emitted, and no socket
receives it.

Realtime event models do not yet add replay cursors, gap identifiers,
backpressure fields, or browser resynchronization instructions. Those belong to
decisions 1A and 14A.

## Data Flow

```text
API
  -> strict shared dispatch model
  -> LiveKit metadata JSON
  -> additive-compatible shared dispatch parser
  -> Agent

Agent
  -> strict shared transcript/completion model
  -> authenticated HTTP JSON
  -> additive-compatible shared request parser
  -> API domain service

API
  -> strict shared acknowledgement model
  -> HTTP JSON
  -> additive-compatible shared acknowledgement parser
  -> Agent correlation check

API or Agent
  -> strict shared realtime event
  -> Redis JSON
  -> additive-compatible shared event parser
  -> API websocket fanout
```

Conversion into or out of database models and domain objects remains in the
owning application. A shared model may be passed to a domain function when its
interface already consumes the same immutable value, but the shared package
does not gain domain behavior.

## Error Model

The package exposes `ContractError`, a `ValueError` subclass with read-only
`contract_name` and `code` attributes. It is the only public exception raised
by the shared construction, parsing, and serialization helpers.

Its stable codes are:

- `malformed_json`
- `missing_schema_version`
- `unsupported_schema_version`
- `invalid_payload`

The exception interface contains:

- `contract_name`
- `code`

It does not contain:

- raw payload data
- Pydantic input rendering
- tokens
- transcript text
- prompts
- knowledge-base content

Underlying parsing and validation exceptions are suppressed at the public
contract seam rather than chained into logs.

Application mapping remains explicit:

- invalid LiveKit dispatch: reject the job and log only the safe code;
- invalid agent HTTP request: reject before domain or database work;
- invalid acknowledgement: classify as a permanent contract failure;
- invalid Redis event: record a bounded metric/log entry, discard the event,
  and continue the fanout loop;
- Redis channel/event user mismatch: classify as tenant isolation failure,
  discard, and continue without logging either identifier;
- producer validation failure: classify as an internal defect and do not send.

The shared package does not decide HTTP status codes, retries, alerts, or
shutdown policy.

The applications record invalid messages through a
`presvo.contract.invalid_messages` counter. Its bounded attributes are
`contract_name`, `code`, and `transport`; it never includes identifiers or
payload content.

## Limits and Edge Cases

The contract models own field and collection limits that are part of the wire
interface. Tests cover:

- exact minimum;
- one below minimum;
- exact maximum;
- one above maximum;
- empty and whitespace-only strings;
- booleans where integers are required;
- negative and zero values where prohibited;
- malformed and non-object JSON;
- invalid UUIDs;
- unknown discriminators and enum values;
- missing and unsupported schema versions;
- additive unknown fields at every nesting level;
- duplicate or reordered transcript sequence numbers at the application
  correlation seam;
- acknowledgement values that are valid in isolation but mismatch the request.

Resource-level HTTP body and Redis transport limits remain application
responsibilities. The shared package does not attempt to enforce a byte limit
after a framework has already buffered a request.

## Test Design

### Shared-Package Unit Tests

For every contract:

- construct at the minimum and maximum valid boundaries;
- serialize to JSON and parse back;
- compare to a reviewed golden v1 fixture;
- reject invalid semantic values;
- reject producer extras;
- accept consumer additive extras;
- reject missing and unsupported versions;
- prove safe exception messages and representations;
- prove serialized output contains only declared fields.

Tests use real serialized bytes or strings, not comparisons of source code.

### Golden Fixtures

`libs/shared/tests/fixtures/v1` contains one canonical JSON document for every
top-level contract. Fixture names identify the contract and version.

The fixture policy test verifies:

- every supported schema version has a directory;
- every top-level contract has a fixture in every supported version;
- every fixture parses through its declared consumer;
- serializing the canonical producer value matches the reviewed fixture.

Initially the supported-version set is `{1}`. Adding v2 must add its fixture
matrix before a producer may emit it.

### Cross-Application Compatibility Tests

The API suite proves:

- API dispatch serialization matches the shared golden fixture;
- agent request fixtures parse at each API route seam;
- API acknowledgement serialization matches its shared fixture;
- each realtime producer emits a shared event.

The agent suite proves:

- API dispatch fixtures parse at LiveKit request and entrypoint seams;
- transcript and completion serialization match shared fixtures;
- API acknowledgement fixtures parse and correlate;
- agent realtime producers emit shared events.

Neither application imports the other application. Both consume the installed
shared package and the same golden artifacts.

### Integration and Build Tests

- LiveKit customer and verification dispatches round-trip through JSON.
- Agent transcript append and recovery completion round-trip over the existing
  HTTP test transport.
- Malformed acknowledgements produce safe permanent failures.
- Redis publish/subscribe handles each event variant.
- One malformed Redis event does not terminate later valid fanout.
- API and agent lockfiles resolve independently.
- API and agent runtime images import `presvo_contracts`.
- Docker Compose configuration remains valid.
- Root build context exclusions prevent environment files, virtual
  environments, repository history, and unrelated local directories from
  entering images.
- Ruff, mypy, complete API and agent suites, and both independent coverage
  ratchets remain green.

## Implementation Sequence

1. Add golden fixtures and compatibility tests that fail against the current
   source-text-only setup.
2. Build the package structure, version/error primitives, and package tests.
3. Add both path dependencies, refresh both lockfiles, and update Docker/Compose
   build contexts.
4. Migrate customer-call and forwarding-verification dispatch producers and
   consumers.
5. Migrate transcript append requests and acknowledgements.
6. Migrate call and verification completion requests and acknowledgements.
7. Migrate realtime channel naming and all current event producers/consumers.
8. Delete duplicate wire models, local literals, manual acknowledgement shape
   checks, comments requiring synchronized edits, and the source-text contract
   test.
9. Run focused, full-suite, coverage, Compose, container-build, import-smoke,
   and context-hygiene verification.

Each slice uses a failing test first, a focused commit, and an independent review
checkpoint. No production source change starts until a detailed implementation
plan is approved.

## Documentation Changes

Implementation updates:

- the issue 2 status in the engineering decision record;
- contributor commands for shared-package tests and root-context image builds;
- CI/container documentation;
- a short contract-evolution policy beside the golden fixtures.

The documentation must continue to state that decisions 11C and 12C are
accepted risks and that realtime remains disabled until 1A and 14A are
implemented.

## Rollback

Because no legacy deployment exists, rollback is source-level:

- revert the complete 2A implementation as one dependency-consistent unit;
- restore the original app-local models and build contexts together;
- never remove only the shared package while leaving path dependencies or
  serialized v1 payloads in place.

Once any v1 producer is deployed in the future, rollback must retain a consumer
that can parse v1 until that producer is retired.

## Acceptance Criteria

The design is implemented only when:

1. Every current API/agent cross-process JSON payload in scope has one shared v1
   model.
2. Every independently transmitted JSON document requires
   `schema_version = 1`.
3. Producers reject undeclared fields and consumers accept additive unknown
   fields while remaining semantically strict.
4. Dispatch, transcript, completion, acknowledgement, and realtime duplication
   is removed.
5. Ambiguous `call_ended` events are replaced by explicit session-ended and
   API-finalized events.
6. Shared errors cannot expose sensitive values.
7. Golden fixtures and both app suites prove serialized compatibility.
8. Both independent lockfiles and production images contain the shared package.
9. Complete suites, static analysis, coverage gates, Compose validation, image
   builds, and import smoke tests pass.
10. Realtime remains disabled and accepted risks 11C/12C remain documented.

## References

- [Agent/API engineering review decisions](../../engineering/2026-07-30-agent-api-review-decisions.md)
- [uv local path dependencies](https://docs.astral.sh/uv/concepts/projects/dependencies/)
- [uv Docker integration and non-editable installs](https://docs.astral.sh/uv/guides/integration/docker/)
