# Shared Wire Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace duplicated API/agent JSON shapes with one strict, versioned, comprehensively tested `presvo-contracts` package while keeping realtime disabled and preserving the existing independent application builds.

**Architecture:** A small Pydantic v2 package under `libs/shared` owns only cross-process values, safe parsing/serialization, schema-version policy, and golden fixtures. API and agent retain their own domain logic and infrastructure adapters, consume the package through independent uv path dependencies, and validate at every LiveKit, HTTP, and Redis seam. Producers are strict, consumers tolerate only additive unknown fields, and every top-level message requires schema version `1`.

**Tech Stack:** Python 3.13, Pydantic 2.12+, uv 0.11.19, Hatchling, FastAPI, Redis Pub/Sub, LiveKit dispatch metadata, pytest, Ruff, mypy, Docker, Docker Compose, GitHub Actions.

## Global Constraints

- Follow the approved design in
  `docs/superpowers/specs/2026-07-31-shared-wire-contracts-design.md`.
- Use test-driven development for every behavior change: write the focused
  failing test, run it and observe the expected failure, implement the smallest
  complete behavior, then run the focused green test.
- Do not enable realtime. `REALTIME_ENABLED` and
  `NEXT_PUBLIC_REALTIME_ENABLED` remain false.
- Do not add v0 compatibility, a missing-version fallback, speculative v2
  models, a root uv workspace, a unified lockfile, or a package registry.
- Keep API and agent lockfiles independent.
- Preserve accepted risks 11C and 12C. Credentialed LiveKit behavior evaluation
  and a real agent-process E2E remain explicitly out of scope.
- Do not move domain services, repositories, provider clients, retry policy,
  authentication, or infrastructure adapters into `libs/shared`.
- Do not expose raw Pydantic errors, payloads, transcript text, prompts,
  knowledge-base text, tokens, provider responses, user IDs, or call IDs in
  contract failure logs, metrics, or HTTP error bodies.
- Do not weaken either existing API or agent coverage ratchet. Update a
  baseline only if the measured result improves and the repository's baseline
  policy requires recording it.
- Preserve the user's untracked `Presvo_frontend/` directory. Never add,
  inspect recursively, modify, delete, or commit it.
- Use `UV_CACHE_DIR=/tmp/uv-cache` for local uv commands. It redirects uv's
  disposable download/build cache only; it does not alter project dependencies
  or runtime behavior.
- Commit after each task only when its focused tests and static checks pass.

## Public Contract Map

| Transport | Producer | Consumer | Shared model/parser |
|---|---|---|---|
| LiveKit metadata | API | Agent | `CustomerCallDispatch`, `ForwardingVerificationDispatch`, `parse_dispatch` |
| Transcript HTTP request | Agent | API | `TranscriptAppendRequest`, `parse_contract` |
| Transcript HTTP response | API | Agent | `TranscriptAppendAcknowledgement`, `parse_contract` |
| Call completion HTTP request | Agent | API | `CallCompletionRequest`, `parse_contract` |
| Call completion HTTP response | API | Agent | `CallCompletionAcknowledgement`, `parse_contract` |
| Verification completion request | Agent | API | `VerificationCompletionRequest`, `parse_contract` |
| Verification completion response | API | Agent | `VerificationCompletionAcknowledgement`, `parse_contract` |
| Redis realtime event | API or Agent | API fanout | Four event models, `parse_realtime_event`, `realtime_channel` |

---

### Task 1: Build the package and safe versioning seam

**Files:**

- Modify: `libs/shared/pyproject.toml`
- Add: `libs/shared/uv.lock`
- Add: `libs/shared/src/presvo_contracts/__init__.py`
- Add: `libs/shared/src/presvo_contracts/versioning.py`
- Add: `libs/shared/tests/test_versioning.py`
- Delete: `libs/shared/__init__.py`

**Consumes:**

- Python `>=3.13,<3.14`
- Pydantic `>=2.12,<3`

**Produces:**

- `CURRENT_SCHEMA_VERSION`
- `SUPPORTED_SCHEMA_VERSIONS`
- `ContractError`
- `VersionedContract`
- `create_contract`
- `parse_contract`
- `dump_contract`
- `dump_contract_json`

- [ ] **Step 1: Replace the package metadata and write failing version tests**

Use this package configuration:

```toml
[project]
name = "presvo-contracts"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = [
  "pydantic>=2.12,<3",
]

[dependency-groups]
dev = [
  "mypy>=2.3,<3",
  "pytest>=9.0.3,<10",
  "pytest-timeout>=2.4,<3",
  "ruff>=0.15.21,<0.16",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/presvo_contracts"]

[tool.pytest.ini_options]
testpaths = ["tests"]
required_plugins = ["pytest-timeout>=2.4,<3"]
timeout = 30

[tool.ruff]
target-version = "py313"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]

[tool.mypy]
python_version = "3.13"
plugins = ["pydantic.mypy"]
check_untyped_defs = true
warn_redundant_casts = true
warn_unused_configs = true
warn_unused_ignores = true
show_error_codes = true
```

In `test_versioning.py`, define a local probe contract and cover all public
versioning behavior:

```python
class ProbeContract(VersionedContract):
    value: str


def test_producer_injects_version_and_forbids_extras() -> None:
    contract = create_contract(ProbeContract, value="known")
    assert contract.schema_version == CURRENT_SCHEMA_VERSION
    with pytest.raises(ContractError) as caught:
        create_contract(ProbeContract, value="known", typo="rejected")
    assert caught.value.code == "invalid_payload"


@pytest.mark.parametrize("value", [{}, {"schema_version": True}, {"schema_version": 2}])
def test_consumer_requires_supported_integer_version(value: object) -> None:
    with pytest.raises(ContractError) as caught:
        parse_contract(ProbeContract, value)
    assert caught.value.code in {
        "missing_schema_version",
        "unsupported_schema_version",
    }


def test_consumer_ignores_additive_fields_but_producer_does_not() -> None:
    parsed = parse_contract(
        ProbeContract,
        {"schema_version": 1, "value": "known", "future": "ignored"},
    )
    assert dump_contract(parsed) == {"schema_version": 1, "value": "known"}
```

Also test malformed strings and bytes, non-object JSON, whitespace-only and
invalid values, safe `str()` and `repr()`, `__cause__ is None`, JSON-compatible
UUID-free output, and deterministic `dump_contract_json`.

- [ ] **Step 2: Run the test and verify the expected RED failure**

Run:

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv lock
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_versioning.py -q
```

Expected: collection fails because `presvo_contracts.versioning` and its public
symbols do not exist.

- [ ] **Step 3: Implement the explicit version/error boundary**

Implement these semantics in `versioning.py`:

```python
CURRENT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({CURRENT_SCHEMA_VERSION})
ContractErrorCode = Literal[
    "malformed_json",
    "missing_schema_version",
    "unsupported_schema_version",
    "invalid_payload",
]
NonBlankString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class ContractError(ValueError):
    def __init__(self, contract_name: str, code: ContractErrorCode) -> None:
        self._contract_name = contract_name
        self._code = code
        super().__init__(f"{contract_name} rejected: {code}")

    @property
    def contract_name(self) -> str:
        return self._contract_name

    @property
    def code(self) -> ContractErrorCode:
        return self._code


class WireValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionedContract(WireValue):

    schema_version: Literal[1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value
```

`WireValue` and `NonBlankString` are package-internal and are not re-exported.
Their shared definitions prevent nested model and non-blank validation from
drifting across modules. `WireValue`'s strict default makes direct
producer/nested-value construction reject undeclared fields. Consumer helpers
explicitly override that default with `extra="ignore"`, including recursively
nested values.

The helpers must follow this shape:

```python
ContractT = TypeVar("ContractT", bound=VersionedContract)


def create_contract(
    model_type: type[ContractT],
    /,
    **values: object,
) -> ContractT:
    payload = dict(values)
    payload.setdefault("schema_version", CURRENT_SCHEMA_VERSION)
    try:
        return model_type.model_validate(payload, extra="forbid")
    except (TypeError, ValidationError):
        raise ContractError(model_type.__name__, "invalid_payload") from None


def parse_contract(
    model_type: type[ContractT],
    value: object,
) -> ContractT:
    payload = _decode_versioned_object(model_type.__name__, value)
    try:
        return model_type.model_validate(payload, extra="ignore")
    except (TypeError, ValidationError):
        raise ContractError(model_type.__name__, "invalid_payload") from None
```

`_decode_versioned_object` must:

1. Decode `str` and `bytes` with `json.loads`.
2. Raise `malformed_json` without chaining on decode failure.
3. Reject non-mappings as `invalid_payload`.
4. Distinguish absent `schema_version` from a present invalid version.
5. Require `type(version) is int`; `True`, `1.0`, `"1"`, negative values, and
   unsupported integers are `unsupported_schema_version`.

`dump_contract` uses `model_dump(mode="json", exclude_none=False)`.
`dump_contract_json` serializes that dictionary with stable separators and
sorted keys. Both wrap unexpected validation/serialization failures in a safe
`ContractError` raised `from None`.

Export only the intentional public symbols from `__init__.py`.

- [ ] **Step 4: Run focused package checks**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_versioning.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src
```

Expected: all pass; the safe-error sentinel values appear in neither exception
text nor representation.

- [ ] **Step 5: Commit the package foundation**

```bash
git add libs/shared/pyproject.toml libs/shared/uv.lock \
  libs/shared/src/presvo_contracts libs/shared/tests/test_versioning.py \
  libs/shared/__init__.py
git commit -m "feat(contracts): add safe versioned package foundation"
```

---

### Task 2: Define strict dispatch contracts

**Files:**

- Add: `libs/shared/src/presvo_contracts/dispatch.py`
- Modify: `libs/shared/src/presvo_contracts/versioning.py`
- Modify: `libs/shared/src/presvo_contracts/__init__.py`
- Add: `libs/shared/tests/test_dispatch.py`

**Consumes:**

- `VersionedContract`
- `_decode_versioned_object`
- strict producer/additive consumer policy

**Produces:**

- Shared content constants and constrained string aliases
- `CustomerCallDispatch`
- `ForwardingVerificationDispatch`
- `DispatchContract`
- `parse_dispatch`

- [ ] **Step 1: Write dispatch boundary and discriminator tests**

Cover exact min/max and one-below/one-above boundaries for every bounded
content field; valid and invalid UUIDs; empty and whitespace-only identities
and tokens; negative minutes; zero duration; both pipeline modes; both TTS
providers; missing/unknown `job_type`; producer extras; and nested/additive
consumer fields.

Include the no-fallback assertion:

```python
def test_dispatch_requires_explicit_job_type() -> None:
    payload = valid_customer_dispatch()
    payload.pop("job_type")
    with pytest.raises(ContractError) as caught:
        parse_dispatch(payload)
    assert caught.value.code == "invalid_payload"
```

Verify token `repr()` values never contain token sentinels.

- [ ] **Step 2: Run the focused RED test**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_dispatch.py -q
```

Expected: collection fails because the dispatch models and parser do not exist.

- [ ] **Step 3: Implement dispatch models without app imports**

Use one shared constraint definition for both API configuration and dispatch:

```python
AGENT_NAME_MAX_LENGTH = 100
OWNER_NAME_MAX_LENGTH = 255
OWNER_CONTEXT_MAX_LENGTH = 4_000
SYSTEM_PROMPT_MAX_LENGTH = 8_000
KNOWLEDGE_BASE_MAX_LENGTH = 32_000
VERIFICATION_MESSAGE = (
    "Forwarding test successful. Return to Presvo to go live."
)

AgentName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=AGENT_NAME_MAX_LENGTH,
    ),
]
```

Define `OwnerName`, `OwnerContext`, `SystemPrompt`, and `KnowledgeBase` once
with the approved limits. Define both top-level models with required
`schema_version` inherited from `VersionedContract`, required explicit
discriminators, UUID fields, strict integer ranges, literal provider/mode
vocabularies, and token fields declared with `repr=False`.

```python
class CustomerCallDispatch(VersionedContract):
    job_type: Literal["customer_call"]
    call_id: UUID
    user_id: UUID
    agent_config_id: UUID
    agent_identity: NonBlankString
    agent_name: AgentName
    owner_name: OwnerName
    owner_context: OwnerContext | None = None
    system_prompt: SystemPrompt
    knowledge_base: KnowledgeBase
    pipeline_mode: Literal["stt_llm_tts", "sts"]
    minutes_remaining: StrictInt = Field(ge=0)
    allowed_duration_seconds: StrictInt = Field(gt=0)
    dispatch_token: str = Field(min_length=1, repr=False)


class ForwardingVerificationDispatch(VersionedContract):
    job_type: Literal["forwarding_verification"]
    verification_session_id: UUID
    user_id: UUID
    agent_identity: NonBlankString
    completion_token: str = Field(min_length=1, repr=False)
    message: Literal[
        "Forwarding test successful. Return to Presvo to go live."
    ]
    tts_provider: Literal["speechmatics", "elevenlabs"]
```

Before-validation token validators reject whitespace-only values without
normalizing or echoing them. Do not give `job_type`, message, or provider
fields defaults; producers must state semantic choices explicitly.

Add a private generic union helper to `versioning.py` so the two public union
parsers do not duplicate safe decoding and exception mapping:

```python
def _parse_contract_union(
    adapter: TypeAdapter[UnionT],
    contract_name: str,
    value: object,
) -> UnionT:
    payload = _decode_versioned_object(contract_name, value)
    try:
        return adapter.validate_python(payload, extra="ignore")
    except (TypeError, ValidationError):
        raise ContractError(contract_name, "invalid_payload") from None
```

Declare `UnionT = TypeVar("UnionT")` beside `ContractT`; the helper remains
private and is imported only by sibling contract modules.

`parse_dispatch` calls that helper with a discriminated `TypeAdapter`.

- [ ] **Step 4: Run focused checks**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_versioning.py tests/test_dispatch.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src
```

- [ ] **Step 5: Commit dispatch contracts**

```bash
git add libs/shared/src/presvo_contracts libs/shared/tests/test_dispatch.py
git commit -m "feat(contracts): define dispatch wire models"
```

---

### Task 3: Define transcript and completion contracts

**Files:**

- Add: `libs/shared/src/presvo_contracts/transcript.py`
- Add: `libs/shared/src/presvo_contracts/completion.py`
- Modify: `libs/shared/src/presvo_contracts/__init__.py`
- Add: `libs/shared/tests/test_transcript.py`
- Add: `libs/shared/tests/test_completion.py`

**Consumes:**

- `VersionedContract`
- `TranscriptSegment` as the single nested transcript value

**Produces:**

- `TranscriptSpeaker`
- `TRANSCRIPT_TEXT_MAX_LENGTH`, `TranscriptSequenceNumber`, and
  `TranscriptText`
- `TranscriptSegment`
- transcript request/acknowledgement models
- call and verification completion request/acknowledgement models

- [ ] **Step 1: Write exhaustive transcript tests**

Test sequence `1`, `0`, `True`, floats, and strings; exact 1/4,000 text
boundaries; stripping; empty/whitespace text; exact speaker vocabulary; nested
producer extras rejected; nested consumer extras ignored; immutable model
behavior; request/ack round trips; and exact acknowledgement status values.

Use this required request shape:

```python
{
    "schema_version": 1,
    "segment": {
        "sequence_number": 1,
        "speaker": "CALLER",
        "text": "Bonjour",
    },
}
```

- [ ] **Step 2: Write exhaustive completion tests**

Test duration `0`, negative, `True`, float, and string values; transcript sizes
0, 2,000, and 2,001; nested invalid segments; exact status/queued literals;
empty and whitespace-only job IDs; verification session UUIDs; producer extras;
consumer extras; and all round trips.

- [ ] **Step 3: Run the focused RED tests**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_transcript.py tests/test_completion.py -q
```

Expected: collection fails because both modules are absent.

- [ ] **Step 4: Implement the immutable transcript vocabulary**

```python
class TranscriptSpeaker(StrEnum):
    CALLER = "CALLER"
    AGENT = "AGENT"


TRANSCRIPT_TEXT_MAX_LENGTH = 4_000
TranscriptSequenceNumber = Annotated[StrictInt, Field(ge=1)]
TranscriptText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=TRANSCRIPT_TEXT_MAX_LENGTH,
    ),
]


class TranscriptSegment(WireValue):
    sequence_number: TranscriptSequenceNumber
    speaker: TranscriptSpeaker
    text: TranscriptText


class TranscriptAppendRequest(VersionedContract):
    segment: TranscriptSegment


class TranscriptAppendAcknowledgement(VersionedContract):
    status: Literal["stored", "duplicate"]
    sequence_number: TranscriptSequenceNumber
```

Do not normalize speaker case. Unknown semantic values remain invalid.

- [ ] **Step 5: Implement completion models using the same segment**

```python
class CallCompletionRequest(VersionedContract):
    duration_seconds: StrictInt = Field(ge=0)
    transcript: tuple[TranscriptSegment, ...] = Field(
        default=(),
        max_length=2_000,
    )


class CallCompletionAcknowledgement(VersionedContract):
    status: Literal["accepted"]
    queued: Literal[True]
    job_id: NonBlankString


class VerificationCompletionRequest(VersionedContract):
    pass


class VerificationCompletionAcknowledgement(VersionedContract):
    status: Literal["verified"]
    session_id: UUID
```

The tuple keeps the frozen request deeply immutable while JSON serialization
still emits an array. Do not put path IDs or authentication tokens into request
bodies.

- [ ] **Step 6: Run focused and package checks**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_versioning.py tests/test_dispatch.py \
  tests/test_transcript.py tests/test_completion.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src
```

- [ ] **Step 7: Commit transcript and completion contracts**

```bash
git add libs/shared/src/presvo_contracts \
  libs/shared/tests/test_transcript.py libs/shared/tests/test_completion.py
git commit -m "feat(contracts): define transcript and completion models"
```

---

### Task 4: Define realtime contracts and the complete golden v1 matrix

**Files:**

- Add: `libs/shared/src/presvo_contracts/realtime.py`
- Modify: `libs/shared/src/presvo_contracts/__init__.py`
- Add: `libs/shared/tests/contract_cases.py`
- Add: `libs/shared/tests/test_realtime.py`
- Add: `libs/shared/tests/test_golden_fixtures.py`
- Add: `libs/shared/tests/fixtures/README.md`
- Add: `libs/shared/tests/fixtures/v1/customer_call_dispatch.json`
- Add: `libs/shared/tests/fixtures/v1/forwarding_verification_dispatch.json`
- Add: `libs/shared/tests/fixtures/v1/transcript_append_request.json`
- Add: `libs/shared/tests/fixtures/v1/transcript_append_acknowledgement.json`
- Add: `libs/shared/tests/fixtures/v1/call_completion_request.json`
- Add: `libs/shared/tests/fixtures/v1/call_completion_acknowledgement.json`
- Add: `libs/shared/tests/fixtures/v1/verification_completion_request.json`
- Add: `libs/shared/tests/fixtures/v1/verification_completion_acknowledgement.json`
- Add: `libs/shared/tests/fixtures/v1/transcript_observed_event.json`
- Add: `libs/shared/tests/fixtures/v1/call_started_event.json`
- Add: `libs/shared/tests/fixtures/v1/agent_session_ended_event.json`
- Add: `libs/shared/tests/fixtures/v1/call_finalized_event.json`

**Consumes:**

- `TranscriptSpeaker`
- versioned union parser

**Produces:**

- Four explicit realtime event models
- `RealtimeEvent`
- `parse_realtime_event`
- `REALTIME_CHANNEL_PREFIX`
- `realtime_channel`
- One reviewed fixture for every top-level v1 contract

- [ ] **Step 1: Write realtime discriminator, version, and tenant-key tests**

Test all four event variants; `0`, negative, `True`, float, and string numeric
values; invalid UUIDs; empty room names; summary lengths 8,000 and 8,001;
unknown `type`; the old `transcript` and `call_ended` discriminators; producer
extras; consumer extras; malformed JSON; and round trips.

Test canonical channel construction:

```python
def test_realtime_channel_uses_canonical_uuid() -> None:
    user_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert realtime_channel(user_id) == f"realtime:user:{user_id}"
```

- [ ] **Step 2: Write the fixture-policy test before fixtures exist**

`contract_cases.py` owns one canonical producer instance and parser for each
top-level contract. `test_golden_fixtures.py` asserts:

```python
def test_every_supported_version_has_a_complete_fixture_matrix() -> None:
    fixture_versions = {
        int(path.name.removeprefix("v"))
        for path in FIXTURE_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("v")
    }
    assert fixture_versions == SUPPORTED_SCHEMA_VERSIONS
    assert {path.stem for path in (FIXTURE_ROOT / "v1").glob("*.json")} == {
        case.fixture_name for case in CONTRACT_CASES
    }


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=attrgetter("fixture_name"))
def test_v1_fixture_matches_producer_and_consumer(case: ContractCase) -> None:
    fixture = json.loads(
        (FIXTURE_ROOT / "v1" / f"{case.fixture_name}.json").read_text()
    )
    assert dump_contract(case.producer) == fixture
    assert dump_contract(case.parser(fixture)) == fixture
```

- [ ] **Step 3: Run the focused RED tests**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_realtime.py tests/test_golden_fixtures.py -q
```

Expected: realtime imports fail and the fixture matrix is absent.

- [ ] **Step 4: Implement explicit realtime events**

```python
REALTIME_CHANNEL_PREFIX = "realtime:user:"


class TranscriptObservedEvent(VersionedContract):
    type: Literal["transcript_observed"]
    user_id: UUID
    call_id: UUID
    sequence_number: TranscriptSequenceNumber
    speaker: TranscriptSpeaker
    text: TranscriptText


class CallStartedEvent(VersionedContract):
    type: Literal["call_started"]
    user_id: UUID
    call_id: UUID
    room_name: NonBlankString


class AgentSessionEndedEvent(VersionedContract):
    type: Literal["agent_session_ended"]
    user_id: UUID
    call_id: UUID
    duration_seconds: StrictInt = Field(ge=0)


class CallFinalizedEvent(VersionedContract):
    type: Literal["call_finalized"]
    user_id: UUID
    call_id: UUID
    minutes_charged: StrictInt = Field(ge=0)
    summary_text: Annotated[str, StringConstraints(max_length=8_000)] | None = None
```

Build `RealtimeEvent` as a discriminated union and implement
`parse_realtime_event` through the same private union helper used by dispatch.
`realtime_channel` accepts a UUID and returns the sole canonical prefix plus
the canonical UUID string.

- [ ] **Step 5: Add and review all 12 golden JSON fixtures**

Use stable UUIDs and obvious non-secret values. Every fixture must include
`"schema_version": 1`. Tokens use unmistakably fake values such as
`"fixture-dispatch-token"` and must never be copied to logs in tests.

The fixture README states:

- additive fields do not require a version bump;
- breaking semantics require a new version;
- v2 requires a complete `v2/` matrix before any producer emits it;
- rollout order is consumer v1+v2, then producer v2, then later v1 removal.

- [ ] **Step 6: Run the complete shared-package suite**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src
```

- [ ] **Step 7: Commit realtime contracts and golden fixtures**

```bash
git add libs/shared/src/presvo_contracts libs/shared/tests
git commit -m "feat(contracts): add realtime models and golden fixtures"
```

---

### Task 5: Install the local package in both independent applications and images

**Files:**

- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Modify: `apps/agent/pyproject.toml`
- Modify: `apps/agent/uv.lock`
- Add: `apps/api/tests/contracts/test_package_installation.py`
- Add: `apps/agent/tests/test_package_installation.py`
- Modify: `apps/api/Dockerfile`
- Modify: `apps/agent/Dockerfile`
- Add: `.dockerignore`
- Add: `tests/docker/root-context.Dockerfile`
- Modify: `compose.dev.yaml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/dependabot.yml`
- Modify: `docs/engineering/ci-and-branch-protection.md`
- Modify: `docs/security/dependency-exceptions.md`

**Consumes:**

- Local `../../libs/shared` source package
- Independent API and agent uv projects

**Produces:**

- Reproducible app lockfile entries
- Non-editable shared package in both runtime images
- Root-context API/agent builds with explicit Dockerfiles
- Independent `CI / Shared contracts` and dependency-audit coverage

- [ ] **Step 1: Write package metadata/import smoke tests**

Each application test imports the public package and verifies its distribution
metadata:

```python
def test_shared_contract_package_is_installed() -> None:
    assert importlib.metadata.version("presvo-contracts") == "0.1.0"
    assert presvo_contracts.CURRENT_SCHEMA_VERSION == 1
```

- [ ] **Step 2: Run both RED tests before declaring the dependency**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts/test_package_installation.py -q

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_package_installation.py -q
```

Expected: `ModuleNotFoundError` or missing distribution metadata in both apps.

- [ ] **Step 3: Add the same local runtime dependency to each app**

Add to each dependency list:

```toml
"presvo-contracts",
```

Add to each project:

```toml
[tool.uv.sources]
presvo-contracts = { path = "../../libs/shared" }
```

Refresh each lockfile independently:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv lock
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
```

Do not create a root `pyproject.toml`, `uv.lock`, or workspace stanza.

- [ ] **Step 4: Make API and agent Dockerfiles root-context aware**

Keep `WORKDIR /app`, because from `/app` the declared `../../libs/shared`
dependency resolves to `/libs/shared`. In both builder stages, use the
application-qualified paths and install non-editably:

```dockerfile
COPY apps/api/pyproject.toml apps/api/uv.lock ./
COPY libs/shared/pyproject.toml /libs/shared/pyproject.toml
COPY libs/shared/src /libs/shared/src

RUN python -m pip install --no-cache-dir uv==0.11.19 \
    && uv sync --frozen --no-dev --no-editable
```

The agent builder uses the corresponding exact paths:

```dockerfile
COPY apps/agent/pyproject.toml apps/agent/uv.lock ./
COPY libs/shared/pyproject.toml /libs/shared/pyproject.toml
COPY libs/shared/src /libs/shared/src

RUN python -m pip install --no-cache-dir uv==0.11.19 \
    && uv sync --frozen --no-dev --no-editable
```

Every later API source copy starts with `apps/api/`; every later agent source
copy starts with `apps/agent/`. The development stage also syncs with
`--no-editable`; shared-source changes require an intentional image rebuild
instead of a hidden source-path dependency.

Add a root `.dockerignore` covering at least `.git`, `.worktrees`, `.venv`,
`**/.venv`, Python caches, uv caches, coverage output, `node_modules`,
`.env`, `.env.*` except reviewed examples, test output, and
`Presvo_frontend/`.

- [ ] **Step 5: Update Compose and test Docker context hygiene**

API-derived services use:

```yaml
build:
  context: .
  dockerfile: apps/api/Dockerfile
  target: development
```

The agent uses the same root context with
`dockerfile: apps/agent/Dockerfile`. The web context stays `./apps/web`.

`tests/docker/root-context.Dockerfile` is:

```dockerfile
FROM scratch
COPY . /context
```

Use a `mktemp -d` output directory and create only one verified-absent
disposable root file named `.env.contract-context-probe`. Export the filtered
context through the probe Dockerfile and assert:

- API, agent, and shared manifests/sources are present;
- `.git`, `.env.contract-context-probe`, any existing `.env`/`.venv`,
  `node_modules`, coverage artifacts, and `Presvo_frontend` are absent.

Do not traverse or modify existing ignored directories to create sentinels.
Remove only `.env.contract-context-probe` and the exact `mktemp -d` output
directory created by the probe. If the sentinel file existed before the probe,
stop rather than overwrite it.

- [ ] **Step 6: Add shared-package CI, audit, and image smoke checks**

Add a `shared-contracts` job named `CI / Shared contracts` that runs, in
`libs/shared`:

```yaml
- run: uv lock --check
- run: uv sync --frozen --all-groups
- run: uv run --frozen --no-sync ruff check src tests
- run: uv run --frozen --no-sync mypy src
- run: uv run --frozen --no-sync python -m pytest -q
```

Add `/libs/shared` to Dependabot's uv directories and add `shared` to the
dependency-audit matrix. Add `--no-emit-local` to the Python `uv export`
command: the shared package is reviewed from repository source and has no
registry hash, while all of its third-party dependencies remain present and
hash-audited in each lock export. Keep `--require-hashes` and `--no-deps` on
`pip-audit`.

Change the container matrix to carry both context and Dockerfile:

```yaml
- application: api
  context: .
  dockerfile: apps/api/Dockerfile
- application: agent
  context: .
  dockerfile: apps/agent/Dockerfile
- application: web
  context: apps/web
  dockerfile: apps/web/Dockerfile
```

Build and tag each matrix entry with:

```yaml
- name: Build image
  run: >-
    docker build
    --file "${{ matrix.dockerfile }}"
    --tag "presvo-${{ matrix.application }}:${{ github.sha }}"
    "${{ matrix.context }}"
- name: Verify shared package in Python runtime images
  if: matrix.application != 'web'
  run: >-
    docker run --rm
    --entrypoint /app/.venv/bin/python
    "presvo-${{ matrix.application }}:${{ github.sha }}"
    -c "import presvo_contracts; assert presvo_contracts.CURRENT_SCHEMA_VERSION == 1"
```

Add `shared-contracts` to `CI / Required`'s `needs`, environment, and success
loop. The existing API and agent jobs continue to enforce their independent
coverage gates. Update `ci-and-branch-protection.md` with the new shared job,
shared dependency-audit check, root-context API/agent image builds, and required
check list. Update the documented agent audit reproduction command with
`--no-emit-local`.

- [ ] **Step 7: Run focused installation and configuration checks**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts/test_package_installation.py -q

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_package_installation.py -q

cd ../..
docker compose -f compose.dev.yaml config >/tmp/presvo-compose-contracts.yml
```

Expected: imports pass, both locks are current, and Compose renders without
enabling realtime.

- [ ] **Step 8: Build both runtime images and verify imports**

```bash
docker build --file apps/api/Dockerfile --tag presvo-api:contracts .
docker run --rm --entrypoint /app/.venv/bin/python presvo-api:contracts \
  -c "import presvo_contracts"

docker build --file apps/agent/Dockerfile --tag presvo-agent:contracts .
docker run --rm --entrypoint /app/.venv/bin/python presvo-agent:contracts \
  -c "import presvo_contracts"
```

- [ ] **Step 9: Commit packaging and build integration**

```bash
git add .dockerignore .github/dependabot.yml .github/workflows/ci.yml \
  apps/api/Dockerfile apps/api/pyproject.toml apps/api/uv.lock \
  apps/api/tests/contracts/test_package_installation.py \
  apps/agent/Dockerfile apps/agent/pyproject.toml apps/agent/uv.lock \
  apps/agent/tests/test_package_installation.py compose.dev.yaml \
  docs/engineering/ci-and-branch-protection.md \
  docs/security/dependency-exceptions.md \
  tests/docker/root-context.Dockerfile
git commit -m "build: install shared contracts in api and agent"
```

---

### Task 6: Migrate LiveKit dispatch producers and consumers

**Files:**

- Add: `apps/api/tests/contracts/test_dispatch_compatibility.py`
- Add: `apps/agent/tests/test_dispatch_compatibility.py`
- Modify: `apps/api/app/workers/jobs/outbox_topics.py`
- Modify: `apps/api/app/schemas/agent.py`
- Modify: `apps/api/app/services/customer_readiness_policy.py`
- Modify: `apps/api/app/services/receptionist_projection_service.py`
- Modify: `apps/api/tests/activation/test_receptionist_projection_service.py`
- Modify: `apps/api/tests/agent/test_agent_config_api.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_service.py`
- Modify: `apps/api/tests/services/test_customer_readiness_policy.py`
- Modify: `apps/api/tests/workers/test_livekit_dispatch_outbox.py`
- Modify:
  `apps/api/tests/workers/test_forwarding_verification_dispatch_outbox.py`
- Delete: `apps/api/app/schemas/livekit.py`
- Delete: `apps/api/app/schemas/agent_content.py`
- Modify: `apps/agent/agent/main.py`
- Modify: `apps/agent/agent/session_runtime.py`
- Modify: `apps/agent/agent/verification_runtime.py`
- Modify: `apps/agent/tests/test_call_limits.py`
- Modify: `apps/agent/tests/test_main.py`
- Modify: `apps/agent/tests/test_session_runtime.py`
- Modify: `apps/agent/tests/test_session_runtime_errors.py`
- Modify: `apps/agent/tests/test_verification_runtime.py`

**Consumes:**

- Dispatch golden fixtures
- API outbox snapshots and LiveKit metadata string

**Produces:**

- API metadata serialized only by shared producer helpers
- Agent parsing only through `parse_dispatch`
- No implicit customer-call discriminator fallback
- One shared definition of agent-content limits

- [ ] **Step 1: Write cross-application dispatch compatibility tests**

The API test captures customer and verification metadata from the existing
outbox tests, parses it through `parse_dispatch`, and compares its decoded
dictionary with the appropriate shared fixture.

The agent test loads both shared fixtures and proves the exact strings work at
both `handle_job_request` and `entrypoint` parsing seams. Add explicit tests for
missing `schema_version`, missing `job_type`, unknown `job_type`, additive
fields, malformed JSON, bad UUIDs, and safe logging with token/prompt
sentinels.

- [ ] **Step 2: Run the focused RED tests**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts/test_dispatch_compatibility.py \
  tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py -q

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_dispatch_compatibility.py tests/test_verification_runtime.py \
  tests/test_main.py -q
```

Expected: current metadata lacks version/customer discriminator, and the agent
still uses its local fallback parser.

- [ ] **Step 3: Replace both API dispatch producers**

In `outbox_topics.py`, replace local models and direct `.model_dump_json()`:

```python
metadata = dump_contract_json(
    create_contract(
        CustomerCallDispatch,
        job_type="customer_call",
        user_id=call.user_id,
        agent_config_id=agent_config.id,
        call_id=call.id,
        agent_identity=expected_agent_identity(call.id),
        minutes_remaining=balance,
        allowed_duration_seconds=calculate_allowed_duration(
            minutes_remaining=balance,
            maximum=settings.max_call_duration_seconds,
        ),
        agent_name=agent_config.agent_name,
        owner_name=owner_name,
        owner_context=agent_config.owner_context,
        system_prompt=agent_config.system_prompt,
        knowledge_base=agent_config.knowledge_base,
        pipeline_mode=agent_config.pipeline_mode,
        dispatch_token=dispatch_token,
    )
)
```

The verification producer explicitly supplies
`job_type="forwarding_verification"`, `message=VERIFICATION_MESSAGE`, and its
selected `tts_provider`. Continue mapping producer validation failures to the
existing safe non-retryable `dispatch_configuration` classification.

Replace all API `agent_content` imports with the shared public constants and
aliases, then delete both duplicated schema modules. This deliberately keeps
configuration readiness and dispatch limits synchronized.

- [ ] **Step 4: Replace agent parsing without compatibility aliases**

In `main.py`, remove `json.loads`, `pydantic.ValidationError`, and local schema
imports:

```python
try:
    metadata = parse_dispatch(request.job.metadata or "{}")
except ContractError as error:
    logger.warning(
        "job_request_rejected contract_name=%s code=%s transport=livekit",
        error.contract_name,
        error.code,
    )
    await request.reject(terminate=True)
    return

if isinstance(metadata, ForwardingVerificationDispatch):
    expected_identity = f"agent-verification-{metadata.verification_session_id}"
    display_name = "Presvo forwarding verification"
else:
    expected_identity = f"agent-call-{metadata.call_id}"
    display_name = metadata.agent_name
if metadata.agent_identity != expected_identity:
    logger.warning("job_request_rejected reason=invalid_agent_identity")
    await request.reject(terminate=True)
    return
```

Keep agent-identity correlation as a separate application rule. Use
`CustomerCallDispatch` directly for customer-call function annotations and
`ForwardingVerificationDispatch` for verification. Convert UUIDs to strings
only at provider interfaces that require strings. Use `dump_contract(metadata)`
when `build_agent_runtime` requires a JSON-compatible dictionary.

Do not retain `DispatchMetadata`, `CustomerCallDispatchMetadata`,
`ForwardingVerificationDispatchMetadata`, or `parse_job_metadata` aliases.

- [ ] **Step 5: Update fixtures and existing tests to valid UUID/versioned data**

Replace fake IDs such as `call_123` and `user_123` only in tests now exercising
UUID-valued wire fields. Assert missing `job_type` is rejected instead of
defaulted. Preserve tests for token/prompt redaction and both verification
providers.

- [ ] **Step 6: Run focused API and agent checks**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts/test_dispatch_compatibility.py \
  tests/livekit tests/workers/test_livekit_dispatch_outbox.py \
  tests/workers/test_forwarding_verification_dispatch_outbox.py \
  tests/agent/test_agent_config_api.py \
  tests/services/test_customer_readiness_policy.py \
  tests/activation/test_receptionist_projection_service.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_dispatch_compatibility.py tests/test_verification_runtime.py \
  tests/test_main.py tests/test_call_limits.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
```

- [ ] **Step 7: Commit the dispatch migration**

Stage only the changed files enumerated in Task 6, then verify
`git diff --cached --name-only` contains no unrelated path before committing:

```bash
git commit -m "refactor(contracts): migrate livekit dispatch boundary"
```

---

### Task 7: Migrate transcript and completion HTTP boundaries

**Files:**

- Add: `apps/api/app/core/contract_http.py`
- Add: `apps/api/tests/contracts/test_contract_http.py`
- Add: `apps/api/tests/contracts/test_http_compatibility.py`
- Modify: `apps/api/app/core/observability.py`
- Modify: `apps/api/tests/test_observability.py`
- Modify: `apps/api/app/routers/agent.py`
- Modify: `apps/api/app/routers/activation.py`
- Modify: `apps/api/app/services/transcript_service.py`
- Modify: `apps/api/app/schemas/calls.py`
- Rename: `apps/api/app/schemas/agent_runtime.py` to
  `apps/api/app/schemas/agent_identity.py`
- Modify: `apps/api/tests/activation/test_verification_completion_api.py`
- Modify: `apps/api/tests/agent/test_call_completion.py`
- Modify: `apps/api/tests/agent/test_transcript_append.py`
- Modify: `apps/api/tests/calls/test_call_history_api.py`
- Modify:
  `apps/api/tests/integration/test_agent_runtime_transcript_durability.py`
- Modify: `apps/api/tests/integration/test_transcript_concurrency.py`
- Modify:
  `apps/api/tests/services/test_transcript_service_authorization.py`
- Modify: `apps/agent/agent/api_client.py`
- Modify: `apps/agent/agent/session_runtime.py`
- Modify: `apps/agent/tests/test_api_client.py`
- Modify: `apps/agent/tests/test_session_runtime.py`
- Modify: `apps/agent/tests/test_session_runtime_errors.py`
- Modify: `apps/agent/tests/test_verification_runtime.py`
- Delete: `apps/agent/agent/schemas.py`

**Consumes:**

- Raw HTTP request bytes
- Shared request and acknowledgement models
- Existing path/header authentication and correlation rules

**Produces:**

- Safe API contract-body parser
- Typed request/response boundaries
- Typed agent client results
- No duplicate acknowledgement dictionary inspection

- [ ] **Step 1: Write safe HTTP parser tests**

Test valid bytes, additive fields, malformed JSON, non-object JSON, missing
version, unsupported version, invalid nested values, and safe sentinels.
Contract failures must return only:

```json
{"detail":{"code":"invalid_payload"}}
```

or the corresponding stable version/JSON code. Assert no raw request fragment
or Pydantic `input` value occurs in the response, logs, metric attributes, or
exception chain.

Test that the generated OpenAPI request body contains the shared model schema
and requires `schema_version`.

- [ ] **Step 2: Write API/agent golden HTTP compatibility tests**

Cover:

- transcript request fixture accepted and acknowledgement fixture emitted;
- call completion request fixture accepted and acknowledgement fixture emitted;
- verification request fixture accepted and acknowledgement fixture emitted;
- agent sends request JSON matching each request fixture;
- agent parses each acknowledgement fixture;
- additive response fields are accepted;
- malformed/unsupported acknowledgements are permanent failures;
- valid acknowledgement shapes with mismatched sequence, finalization job ID,
  or verification session ID are permanent correlation failures.

- [ ] **Step 3: Run the focused RED tests**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts/test_contract_http.py \
  tests/contracts/test_http_compatibility.py \
  tests/agent/test_transcript_append.py \
  tests/agent/test_call_completion.py \
  tests/activation/test_verification_completion_api.py -q

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_api_client.py tests/test_session_runtime.py \
  tests/test_session_runtime_errors.py -q
```

Expected: requests/responses are unversioned, responses are manually inspected,
and malformed FastAPI validation may expose raw inputs.

- [ ] **Step 4: Add one reusable raw-body parser and OpenAPI helper**

`contract_http.py` owns only the FastAPI adapter:

```python
ContractT = TypeVar("ContractT", bound=VersionedContract)


async def parse_contract_request(
    request: Request,
    model_type: type[ContractT],
) -> ContractT:
    try:
        return parse_contract(model_type, await request.body())
    except ContractError as error:
        get_request_observability(request).record_invalid_contract(
            contract_name=error.contract_name,
            code=error.code,
            transport="http",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code},
        ) from None


def contract_request_openapi(
    model_type: type[VersionedContract],
) -> dict[str, object]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": model_type.model_json_schema(),
                }
            },
        }
    }
```

This avoids FastAPI/Pydantic error bodies that may echo sensitive inputs while
retaining explicit OpenAPI. Authentication reads may occur before the route
body is parsed, but no domain service, mutation, or commit may run first.

- [ ] **Step 5: Add the bounded API invalid-contract metric**

In `Observability.__init__`:

```python
self.invalid_contract_messages = meter.create_counter(
    "presvo.contract.invalid_messages"
)
```

`record_invalid_contract` permits only enumerated contract names, stable
contract codes plus `channel_user_mismatch`, and transports
`http`, `livekit`, or `redis`; every unknown label becomes `"unknown"`.
Tests use sentinel labels and prove no high-cardinality/sensitive value is
recorded.

- [ ] **Step 6: Migrate API routes and transcript domain conversion**

Each internal-agent route reads its request through `parse_contract_request`
and declares `openapi_extra` through the shared helper. For transcript:

```python
payload = await parse_contract_request(request, TranscriptAppendRequest)
result = await service.append(
    call_id=call_id,
    item=payload.segment,
    expected_user_id=identity.user_id,
    expected_agent_config_id=identity.agent_config_id,
)
return create_contract(
    TranscriptAppendAcknowledgement,
    status=result.status,
    sequence_number=result.sequence_number,
)
```

Call completion passes `payload.transcript` and `payload.duration_seconds`, then
returns a strict `CallCompletionAcknowledgement`. Verification completion now
requires the version-only body and returns a strict
`VerificationCompletionAcknowledgement`.

`TranscriptService` accepts `TranscriptSegment`, removes raw-dictionary
normalization and implicit sequence assignment, and retains its existing
idempotency/conflict behavior for duplicate and reordered sequence numbers.

Keep `AuthenticatedAgentIdentity` application-local in renamed
`agent_identity.py`. Remove only the wire models from `calls.py`.

- [ ] **Step 7: Refactor the agent client to typed, explicit methods**

Use signatures that keep credentials out of bodies:

```python
async def append_transcript(
    self,
    call_id: UUID,
    dispatch_token: str,
    segment: TranscriptSegment,
) -> TranscriptAppendAcknowledgement:
    request = create_contract(TranscriptAppendRequest, segment=segment)
    response = await self._get_http_client().post(
        f"{self.base_url}/api/agent/calls/{call_id}/transcript",
        json=dump_contract(request),
        headers={"x-agent-token": dispatch_token},
    )
    acknowledgement = parse_contract(
        TranscriptAppendAcknowledgement,
        response.content,
    )
    if acknowledgement.sequence_number != segment.sequence_number:
        raise TranscriptAppendPermanentError(
            "transcript append acknowledgement sequence mismatch"
        )
    return acknowledgement
```

Change `complete_call` to accept `(call_id: UUID, dispatch_token: str,
request: CallCompletionRequest)` and return
`CallCompletionAcknowledgement`. Change `complete_verification` to accept
`(session_id: UUID, token: str)` and return
`VerificationCompletionAcknowledgement`. Keep their existing status/retry
classification loops; replace only each successful-response branch with:

```python
acknowledgement = parse_contract(
    CallCompletionAcknowledgement,
    response.content,
)
if acknowledgement.job_id != f"call-finalization:{call_id}":
    raise CallCompletionAcknowledgementError(
        "call completion acknowledgement correlation mismatch"
    )
return acknowledgement
```

and:

```python
acknowledgement = parse_contract(
    VerificationCompletionAcknowledgement,
    response.content,
)
if acknowledgement.session_id != session_id:
    raise VerificationCompletionAcknowledgementError(
        "verification completion acknowledgement correlation mismatch"
    )
return acknowledgement
```

The call client compares `job_id` with
`f"call-finalization:{call_id}"`. The verification client compares the returned
UUID with `session_id`. Catch `ContractError`, emit only its bounded fields in a
safe structured log, and map it to the existing permanent acknowledgement
exception raised `from None`.

`SessionRuntime` builds `TranscriptSegment` and `CallCompletionRequest`
instances, passes credentials separately, and trusts the client to return an
already parsed and correlated acknowledgement. Delete `_acknowledges` and
`is_completion_acknowledgement`; do not reimplement shape checks.

After all imports move, delete `agent/schemas.py` rather than leaving aliases.

- [ ] **Step 8: Update existing edge/failure tests**

Update expected JSON to include `schema_version`; wrap transcript append
segments under `segment`; require verification's version-only body; and make
test fakes return typed acknowledgement models.

Retain or add explicit tests for:

- retryable status codes and every 5xx;
- permanent 4xx;
- malformed JSON and valid non-object JSON responses;
- missing/unsupported versions;
- additive response fields;
- correlation mismatches;
- empty transcript and exactly 2,000 recovery segments;
- duplicate/reordered recovery segments;
- no API client;
- close/finalize failures;
- transcript/prompt/token sentinels absent from logs.

- [ ] **Step 9: Run focused API/agent checks**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts tests/agent/test_transcript_append.py \
  tests/agent/test_call_completion.py \
  tests/activation/test_verification_completion_api.py \
  tests/services/test_transcript_service_authorization.py \
  tests/integration/test_agent_runtime_transcript_durability.py \
  tests/integration/test_transcript_concurrency.py \
  tests/test_observability.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_api_client.py tests/test_session_runtime.py \
  tests/test_session_runtime_errors.py tests/test_main.py \
  tests/test_call_limits.py tests/test_verification_runtime.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
```

- [ ] **Step 10: Commit the HTTP migration**

Stage only the changed files enumerated in Task 7, then verify
`git diff --cached --name-only` contains no unrelated path before committing:

```bash
git commit -m "refactor(contracts): migrate agent http boundaries"
```

---

### Task 8: Migrate typed realtime publication and safe fanout

**Files:**

- Modify: `apps/api/app/core/redis.py`
- Modify: `apps/api/app/services/realtime_service.py`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/websockets/manager.py`
- Modify: `apps/api/tests/integration/test_forwarding_verification_privacy.py`
- Modify: `apps/api/tests/integration/test_livekit_dispatch_concurrency.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`
- Modify: `apps/api/tests/livekit/test_dispatch_webhook.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_service.py`
- Modify: `apps/api/tests/livekit/test_durable_dispatch_webhook.py`
- Modify: `apps/api/tests/livekit/test_forwarding_verification_dispatch.py`
- Modify: `apps/api/tests/realtime/test_redis_fanout.py`
- Modify: `apps/api/tests/realtime/test_runtime_resources.py`
- Modify: `apps/api/tests/realtime/test_websocket_lifecycle.py`
- Add: `apps/api/tests/contracts/test_realtime_compatibility.py`
- Modify: `apps/agent/agent/event_publisher.py`
- Modify: `apps/agent/agent/session_runtime.py`
- Modify: `apps/agent/tests/test_session_runtime.py`
- Modify: `apps/agent/tests/test_session_runtime_errors.py`
- Add: `apps/agent/tests/test_realtime_compatibility.py`
- Delete: `libs/shared/constants.py`
- Delete: `libs/shared/test_contract.py`

**Consumes:**

- Shared realtime event union and channel helper
- Redis Pub/Sub raw data

**Produces:**

- Typed API and agent event publication
- Explicit `agent_session_ended` versus `call_finalized`
- Safe malformed-event continuation
- Channel/event tenant correlation before broadcast
- No source-text synchronization test

- [ ] **Step 1: Write failing producer and fanout compatibility tests**

Test API `call_started` and `call_finalized` output against golden fixtures.
Test agent `transcript_observed` and `agent_session_ended` output against golden
fixtures.

Fanout tests must prove:

- all four valid variants reach the expected owner key;
- additive fields are accepted and discarded;
- malformed JSON does not kill the async loop;
- missing/unsupported version and unknown event type are discarded;
- a channel user/event user mismatch is discarded;
- a valid event after every invalid case is still broadcast;
- invalid-message metrics contain only bounded labels;
- mismatch logs/metrics contain neither user ID;
- the old `transcript` and `call_ended` types are never emitted.

- [ ] **Step 2: Run the focused RED tests**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts/test_realtime_compatibility.py \
  tests/realtime/test_redis_fanout.py \
  tests/realtime/test_runtime_resources.py -q

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_realtime_compatibility.py tests/test_session_runtime.py \
  tests/test_session_runtime_errors.py -q
```

Expected: unversioned dictionaries use ambiguous old discriminators and a
malformed Redis payload terminates iteration.

- [ ] **Step 3: Make both Redis adapters serialize typed events**

Keep infrastructure adapters app-local. Their shared behavior is only the
contract:

```python
async def publish(self, event: RealtimeEvent) -> None:
    await self.redis_client.publish(
        realtime_channel(event.user_id),
        dump_contract_json(event),
    )
```

The API subscriber yields `(channel_user_id, raw_data)` without calling
`json.loads`; safe parsing belongs to `RealtimeService`, which can discard one
message and continue. Parse the suffix as a canonical UUID string but do not
log it on failure.

The agent `EventPublisher.publish` accepts `RealtimeEvent`, not `dict`, and
does not rediscover `user_id` through dictionary access.

- [ ] **Step 4: Create every event through the strict producer helper**

API:

```python
event = create_contract(
    CallStartedEvent,
    type="call_started",
    user_id=user_id,
    call_id=call_id,
    room_name=room_name,
)
await self.event_bus.publish(event)
```

Rename `publish_call_ended` to `publish_call_finalized` and create
`CallFinalizedEvent`.

Agent transcript publication creates `TranscriptObservedEvent` including the
segment's sequence number. Finalization creates `AgentSessionEndedEvent`.
Rename private flags/methods from `call_ended` to `agent_session_ended` so
comments, logs, and behavior use the same explicit vocabulary.

- [ ] **Step 5: Parse, correlate, measure, and continue in API fanout**

Use one helper for `fanout_once` and `fanout_forever`:

```python
def _validated_event(
    self,
    channel_user_id: str,
    raw_payload: object,
) -> RealtimeEvent | None:
    try:
        event = parse_realtime_event(raw_payload)
    except ContractError as error:
        self.observability.record_invalid_contract(
            contract_name=error.contract_name,
            code=error.code,
            transport="redis",
        )
        return None
    if str(event.user_id) != channel_user_id:
        self.observability.record_invalid_contract(
            contract_name=type(event).__name__,
            code="channel_user_mismatch",
            transport="redis",
        )
        logger.error(
            "realtime_event_rejected code=channel_user_mismatch transport=redis"
        )
        return None
    return event
```

Broadcast `dump_contract(event)` to `str(event.user_id)` only after this check.
Invalid messages never return from or terminate the forever loop.
`fanout_once` returns after one valid broadcast, not after one discarded
message.

Pass the API's existing `Observability` instance into `RealtimeService` from
the composition root. Do not enable service construction when the feature flag
is false.

Remove the unused duplicate `WebSocketManager.channel_name`; socket
authentication remains unchanged and later 1A/3A/14A work owns durable
resynchronization and external-to-internal user mapping.

- [ ] **Step 6: Remove duplicated constants and source-text checks**

Delete `libs/shared/constants.py` and `libs/shared/test_contract.py`. Verify all
channel construction imports `realtime_channel`; do not leave comments telling
maintainers to edit several files together.

- [ ] **Step 7: Run realtime, privacy, and runtime-resource checks**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q

cd ../../apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/contracts/test_realtime_compatibility.py tests/realtime \
  tests/livekit/test_dispatch_service.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/livekit/test_forwarding_verification_dispatch.py \
  tests/test_observability.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_realtime_compatibility.py tests/test_session_runtime.py \
  tests/test_session_runtime_errors.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
```

- [ ] **Step 8: Commit the realtime migration**

Stage only the changed files enumerated in Task 8, then verify
`git diff --cached --name-only` contains no unrelated path before committing:

```bash
git commit -m "refactor(contracts): type realtime event boundaries"
```

---

### Task 9: Prove duplication removal and document the operational contract

**Files:**

- Modify: `CONTRIBUTING.md`
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`
- Modify: `docs/superpowers/specs/2026-07-31-shared-wire-contracts-design.md`

**Consumes:**

- Completed shared package and migrated application seams

**Produces:**

- Durable contributor commands and evolution policy
- Decision 2A marked implemented only after evidence exists
- Mechanical proof that app-local wire duplication is gone

- [ ] **Step 1: Run aggressive duplication scans**

Run:

```bash
rg -n \
  "CustomerCallDispatchMetadata|ForwardingVerificationDispatchMetadata|LiveKitDispatchMetadata|VerificationDispatchMetadata|CallTranscriptItem|CallCompletionPayload|TranscriptAppendResponse|AgentCallCompletionRequest|AgentCallCompletionResponse|is_completion_acknowledgement|parse_job_metadata|REALTIME_CHANNEL_PREFIX|type.?=.?[\"']call_ended|type.?=.?[\"']transcript[\"']" \
  apps/api apps/agent libs/shared
```

Expected: no app-local wire model, manual acknowledgement parser, duplicated
prefix, implicit dispatch parser, or old realtime event producer remains.
Intentional mentions in migration docs/fixtures are allowed only when they
describe history and cannot be imported or executed.

Also run:

```bash
rg -n "json\\.dumps|model_dump_json|response\\.json\\(\\)|\\.get\\([\"']status" \
  apps/api/app apps/agent/agent
```

Review every result. Ad hoc JSON and dictionary inspection may remain for
unrelated internal/provider payloads, but none may serialize or validate a
contract from the public contract map.

- [ ] **Step 2: Add contributor commands**

Document:

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src

docker build --file apps/api/Dockerfile --tag presvo-api:local .
docker build --file apps/agent/Dockerfile --tag presvo-agent:local .
```

Explain why both app lockfiles must be refreshed when the shared dependency
graph changes, but not when only package source changes. State that local uv
installs may be editable while production images use `--no-editable`.

- [ ] **Step 3: Record completion without overstating realtime or test scope**

In the engineering decision record:

- change issue 2A to `Accepted; implemented`;
- link the package, golden fixtures, design, and this plan;
- state that 2A makes later realtime implementation safer but does not enable
  realtime;
- leave 11C and 12C as accepted risks.

Change the design status to `Implemented` only after every focused suite,
independent lock check, image import smoke test, and duplication scan has passed.

- [ ] **Step 4: Run docs and diff hygiene checks**

```bash
git diff --check
rg -n "realtime_enabled|NEXT_PUBLIC_REALTIME_ENABLED" \
  apps/api/.env.example apps/web/.env.example compose.dev.yaml
git status --short
```

Expected: realtime defaults remain false; `Presvo_frontend/` remains the only
unrelated untracked path and is not staged.

- [ ] **Step 5: Commit documentation and cleanup evidence**

```bash
git add CONTRIBUTING.md \
  docs/engineering/2026-07-30-agent-api-review-decisions.md \
  docs/superpowers/specs/2026-07-31-shared-wire-contracts-design.md
git commit -m "docs: record shared contract implementation"
```

---

### Task 10: Run full verification and independent review

**Files:**

- Verify all files changed by Tasks 1–9
- Modify only defects found by verification, with a new failing regression test
  before each behavior fix

**Consumes:**

- All implementation commits

**Produces:**

- Full static, unit, integration, coverage, build, and compatibility evidence
- Review findings resolved or explicitly returned to the user

- [ ] **Step 1: Verify the shared package**

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

- [ ] **Step 2: Verify the complete API suite and coverage ratchet**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json --baseline coverage-baseline.json
```

- [ ] **Step 3: Verify the complete provider-free agent suite and coverage ratchet**

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
env -u LIVEKIT_API_KEY -u LIVEKIT_API_SECRET -u LIVEKIT_EVAL_MODEL \
  UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q -m "not livekit_eval" \
  --cov=agent --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json --baseline coverage-baseline.json
```

Do not run or claim the credentialed 11C evaluation as automated evidence.

- [ ] **Step 4: Verify Compose, E2E, images, and context hygiene**

```bash
cd ../..
docker compose -f compose.dev.yaml config >/tmp/presvo-compose-contracts.yml
bash scripts/run-local-e2e.sh

docker build --file apps/api/Dockerfile --tag presvo-api:contracts-final .
docker run --rm --entrypoint /app/.venv/bin/python \
  presvo-api:contracts-final -c "import presvo_contracts"

docker build --file apps/agent/Dockerfile --tag presvo-agent:contracts-final .
docker run --rm --entrypoint /app/.venv/bin/python \
  presvo-agent:contracts-final -c "import presvo_contracts"
```

Run the Task 5 root-context probe again. Do not describe the provider-free E2E
as a real agent-process test; 12C remains accepted.

- [ ] **Step 5: Inspect dependency, contract, and security invariants**

```bash
git diff --check
git status --short
git grep -n "presvo-contracts" -- \
  apps/api/pyproject.toml apps/agent/pyproject.toml \
  apps/api/uv.lock apps/agent/uv.lock
git grep -n "schema_version" -- libs/shared/tests/fixtures/v1
git grep -n "call_ended\\|\"type\": \"transcript\"" -- \
  apps/api/app apps/agent/agent
```

Expected:

- independent app lockfiles both contain the local distribution;
- every fixture is versioned;
- no old realtime producer remains;
- no unrelated user files are staged;
- realtime remains disabled.

- [ ] **Step 6: Request an independent two-axis code review**

Use `superpowers:requesting-code-review` to review:

1. **Spec compliance:** every acceptance criterion in the approved design and
   every public contract in this plan.
2. **Engineering quality:** DRY removal, safe errors, edge-case coverage,
   explicit boundaries, package/build reproducibility, and absence of
   unnecessary abstractions.

Resolve every high/medium finding with a regression test and a focused commit.
Return low-severity recommendations to the user rather than silently expanding
scope.

- [ ] **Step 7: Re-run affected suites and final verification**

After review fixes, use `superpowers:verification-before-completion` and rerun
every affected focused test plus Steps 1–5. Report exact commands and results;
do not claim completion from earlier or partial output.

- [ ] **Step 8: Commit any verified review fixes**

If review found defects, inspect `git diff --name-only`, stage each reviewed
implementation file and its regression test by explicit path, verify
`git diff --cached --name-only`, then commit with:

```bash
git commit -m "fix(contracts): address implementation review"
```

If no fixes were required, do not create an empty commit.
