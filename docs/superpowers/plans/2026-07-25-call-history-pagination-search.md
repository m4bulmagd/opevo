# Call History Pagination and Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authenticated, server-rendered call-history search and deterministic pagination while preserving inactive-account history access and the unfiltered five-call dashboard preview.

**Architecture:** The API repository builds one owner-scoped visibility/search predicate and uses it for both a count query and a bounded page query. The service maps rows into the existing safe list projection and returns typed pagination data; the FastAPI router validates and serializes the public contract. The Next.js calls page awaits URL `searchParams`, fetches one server-side page, canonicalizes out-of-range pages, and renders accessible GET search and pagination controls without client fetch state.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 async, SQLite/PostgreSQL, pytest, Next.js 16 App Router, React 19 Server Components, TypeScript 5.9, Tailwind CSS 4, shadcn/ui, Vitest, Testing Library, Biome, Node 22.

## Global Constraints

- `GET /api/calls` accepts optional `q`, trimmed by the service, with a maximum request length of 100 characters.
- `limit` remains an integer from 1 through 100 with default 20; `offset` remains a non-negative integer with default 0.
- Search is case-insensitive partial matching over `summary_text` and only the `caller_intent` key in `summary_data`.
- Phone matching is added only when the whole query contains digits plus common phone punctuation/whitespace and normalizes to at least three digits. The full digit candidate is retained; when it starts with a national trunk `0`, search also uses the candidate with exactly that leading zero removed if at least three digits remain, so `01 87` matches stored E.164 `+33187001234`.
- Every count and row query is constrained by the authenticated `user_id` and `deleted_at IS NULL`.
- Search must not inspect transcripts, provider identifiers, recording metadata, deleted content, or arbitrary JSON keys.
- Row order is exactly `started_at DESC NULLS LAST`, `created_at DESC`, then `id DESC`.
- The repository performs one count query and one bounded page query; it introduces no transcript join and no N+1 query.
- The calls page size is exactly 20 and URL state uses `/dashboard/calls?q=<query>&page=<one-based-page>`.
- Invalid, negative, zero, fractional, unsafe, or non-numeric web page values resolve to page 1.
- A positive total with an out-of-range page redirects to the final valid page while retaining `q`; a zero total stays on page 1 and renders an empty state.
- The dashboard recent-calls view requests exactly five unfiltered rows and remains visually unchanged.
- Inactive customers retain read-only call list/detail/playback access.
- Use the existing Presvo dashboard components and visual language; do not redesign call cards or the detail page.
- Search and page controls remain server-rendered, keyboard-operable, explicitly labelled, and fully available at narrow widths.
- Add no database migration, dependency, search extension, search index, client-side debounce, status/date filter, transcript search, tags, notes, cursor pagination, or live updates.
- Node must satisfy the repository engine range `>=22.12 <23`; use the installed Node 22.23.1 binary for verification.
- Completion requires focused RED/GREEN evidence, complete SQLite API tests, complete PostgreSQL/Redis API tests with zero PostgreSQL skips, Ruff, mypy, complete web tests, Biome, TypeScript, a Node 22 production build, `git diff --check`, a clean worktree, and cleanup of disposable infrastructure.

---

## File and Responsibility Map

### API

- `apps/api/app/repositories/call_repository.py`
  - Owns search-shape detection, literal LIKE escaping, the shared visibility/search predicate, count query, bounded row query, and deterministic order.
- `apps/api/app/services/call_history_service.py`
  - Trims blank search values, maps rows through `_list_item`, and returns page metadata.
- `apps/api/app/schemas/calls.py`
  - Defines the public list response metadata.
- `apps/api/app/routers/calls.py`
  - Validates `q`, `limit`, and `offset`, preserves auth/rate limiting, and serializes the service page.
- `apps/api/tests/calls/test_call_history_search.py`
  - Proves repository/service search scope, phone normalization, count alignment, deterministic ordering, tenant/deletion isolation, blank search, and transcript exclusion.
- `apps/api/tests/calls/test_call_history_api.py`
  - Proves the HTTP contract, request bounds, domestic trunk-prefix matching, inactive-account access, and updates existing service assertions for the typed page result.
- `apps/api/tests/conftest.py`
  - Allows the real API/client database fixture to target an explicit disposable PostgreSQL URL for cross-database HTTP regression checks while retaining isolated SQLite by default.
- `apps/api/tests/calls/test_call_lifecycle.py`
  - Updates the one direct repository-list assertion to consume the new page result.
- `docs/architecture/call-history-api.md`
  - Documents request parameters, response metadata, matching rules, and stable ordering.

### Web

- `apps/web/src/lib/types/calls.ts`
  - Mirrors the complete API page response.
- `apps/web/src/lib/api/calls.ts`
  - Accepts a named list-options object and returns the complete page response.
- `apps/web/src/lib/calls/call-history-navigation.ts`
  - Purely parses URL state, calculates offsets/page counts, and builds canonical calls-page links.
- `apps/web/src/components/calls/call-history-controls.tsx`
  - Renders the server-compatible GET search form, result range, and Previous/Next controls.
- `apps/web/src/components/calls/calls-table.tsx`
  - Keeps existing call cards and distinguishes no-history from no-search-match copy.
- `apps/web/src/app/(app)/dashboard/calls/page.tsx`
  - Awaits `searchParams`, fetches one page, redirects out-of-range pages, and composes controls/results.
- `apps/web/src/app/(app)/dashboard/page.tsx`
  - Continues to fetch five unfiltered rows and extracts the response `calls` field.
- `apps/web/tests/lib/calls-api.test.ts`
  - Proves query construction and complete page return behavior.
- `apps/web/tests/lib/call-history-navigation.test.ts`
  - Proves URL parsing, fail-safe page handling, offsets, page counts, and link preservation.
- `apps/web/tests/app/calls-page.test.tsx`
  - Proves server-page data flow, form semantics, links, disabled states, redirects, and distinct empty states.
- `apps/web/tests/app/home-page.test.tsx`
- `apps/web/tests/app/dashboard-onboarding.test.tsx`
- `apps/web/tests/app/app-shell.test.tsx`
  - Adapt mocks to the complete page response and protect the five-call dashboard behavior.
- `docs/PROJECT_STATUS.md`
  - Marks web pagination/search implemented while leaving tags and notes as remaining work.

---

### Task 1: Owner-Scoped Search and Paged API Contract

**Files:**

- Create: `apps/api/tests/calls/test_call_history_search.py`
- Modify: `apps/api/app/repositories/call_repository.py:1-15,246-258`
- Modify: `apps/api/app/services/call_history_service.py:1-18,28-54`
- Modify: `apps/api/app/schemas/calls.py:59-61`
- Modify: `apps/api/app/routers/calls.py:48-58`
- Modify: `apps/api/tests/conftest.py:1-8,113-132`
- Modify: `apps/api/tests/calls/test_call_history_api.py:276-453`
- Modify: `apps/api/tests/calls/test_call_lifecycle.py:38-45`
- Modify: `docs/architecture/call-history-api.md:11-39`

**Interfaces:**

- Consumes:
  - `Call.summary_text: str | None`
  - `Call.summary_data: dict | None`
  - `Call.caller_number: str | None`
  - `CallHistoryService._list_item(call: Call) -> CallHistoryListItem`
- Produces:
  - `CallHistoryPage(calls: list[Call], total: int)`
  - `CallRepository.list_visible_page_by_user_id(user_id: UUID, *, limit: int = 100, offset: int = 0, query: str | None = None) -> CallHistoryPage`
  - `CallHistoryPageResult(calls: list[CallHistoryListItem], total: int, limit: int, offset: int, has_more: bool)`
  - `CallHistoryService.list_calls(user_id: UUID, *, limit: int = 100, offset: int = 0, query: str | None = None) -> CallHistoryPageResult`
  - HTTP `GET /api/calls?q=<text>&limit=<1..100>&offset=<0..n>`
  - `CallHistoryListResponse(calls, total, limit, offset, has_more)`

- [ ] **Step 1: Write repository/service tests for deterministic pages and visibility boundaries**

Create `apps/api/tests/calls/test_call_history_search.py` with the following setup and first test:

```python
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models.call import Call
from app.models.call_message import CallMessage
from app.models.user import User
from app.services.call_history_service import CallHistoryService


NOW = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)


def make_call(
    *,
    call_id: str,
    user_id: UUID,
    caller_number: str | None = None,
    summary_text: str | None = None,
    summary_data: dict | None = None,
    deleted: bool = False,
) -> Call:
    return Call(
        id=UUID(call_id),
        user_id=user_id,
        caller_number=caller_number,
        status="completed",
        started_at=NOW,
        ended_at=NOW,
        duration_seconds=0,
        minutes_charged=0,
        summary_text=summary_text,
        summary_data=summary_data,
        deleted_at=NOW if deleted else None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_list_calls_returns_deterministic_page_and_matching_total(
    db_session,
    active_user,
) -> None:
    other_user = User(
        clerk_user_id="call_search_other",
        email="call-search-other@example.invalid",
    )
    db_session.add(other_user)
    await db_session.flush()

    owner_calls = [
        make_call(
            call_id=f"00000000-0000-0000-0000-00000000000{index}",
            user_id=active_user.id,
            summary_text=f"Visible call {index}",
        )
        for index in (1, 2, 3)
    ]
    db_session.add_all(
        [
            *owner_calls,
            make_call(
                call_id="00000000-0000-0000-0000-000000000004",
                user_id=active_user.id,
                summary_text="Removed owner call",
                deleted=True,
            ),
            make_call(
                call_id="00000000-0000-0000-0000-000000000005",
                user_id=other_user.id,
                summary_text="Foreign call",
            ),
        ]
    )
    await db_session.commit()

    service = CallHistoryService(db_session, recording_service=None)
    first_page = await service.list_calls(active_user.id, limit=2, offset=0)
    second_page = await service.list_calls(active_user.id, limit=2, offset=2)

    assert [item.id for item in first_page.calls] == [
        UUID("00000000-0000-0000-0000-000000000003"),
        UUID("00000000-0000-0000-0000-000000000002"),
    ]
    assert first_page.total == 3
    assert first_page.limit == 2
    assert first_page.offset == 0
    assert first_page.has_more is True
    assert [item.id for item in second_page.calls] == [
        UUID("00000000-0000-0000-0000-000000000001")
    ]
    assert second_page.total == 3
    assert second_page.has_more is False
```

- [ ] **Step 2: Run the first test and capture the expected RED result**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/calls/test_call_history_search.py::test_list_calls_returns_deterministic_page_and_matching_total
```

Expected: FAIL because `CallHistoryService.list_calls()` still returns `list[CallHistoryListItem]`, so `.calls`, `.total`, and `.has_more` do not exist and the ID tie-breaker is not implemented.

- [ ] **Step 3: Add the repository page type, shared predicate, count, and stable row query**

In `apps/api/app/repositories/call_repository.py`, add `import re`, `ColumnElement`, and these module-level definitions:

```python
import re

from sqlalchemy.sql.elements import ColumnElement


PHONE_QUERY_PATTERN = re.compile(r"^[0-9\s()+.\-]+$")


@dataclass(frozen=True)
class CallHistoryPage:
    calls: list[Call]
    total: int


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

Replace `list_visible_by_user_id` with a private predicate builder and the page method:

```python
    @staticmethod
    def _visible_call_predicates(
        user_id: UUID,
        query: str | None,
    ) -> tuple[ColumnElement[bool], ...]:
        predicates: list[ColumnElement[bool]] = [
            Call.user_id == user_id,
            Call.deleted_at.is_(None),
        ]
        if query is None:
            return tuple(predicates)

        escaped_query = _escape_like(query)
        search_predicates: list[ColumnElement[bool]] = [
            Call.summary_text.ilike(f"%{escaped_query}%", escape="\\"),
            Call.summary_data["caller_intent"]
            .as_string()
            .ilike(f"%{escaped_query}%", escape="\\"),
        ]
        if PHONE_QUERY_PATTERN.fullmatch(query):
            digits = "".join(character for character in query if character.isdigit())
            if len(digits) >= 3:
                search_predicates.append(Call.caller_number.ilike(f"%{digits}%"))
            domestic_digits = digits[1:]
            if digits.startswith("0") and len(domestic_digits) >= 3:
                search_predicates.append(
                    Call.caller_number.ilike(f"%{domestic_digits}%")
                )

        predicates.append(or_(*search_predicates))
        return tuple(predicates)

    async def list_visible_page_by_user_id(
        self,
        user_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
    ) -> CallHistoryPage:
        predicates = self._visible_call_predicates(user_id, query)
        total = await self.session.scalar(
            select(func.count(Call.id)).where(*predicates)
        )
        result = await self.session.execute(
            select(Call)
            .where(*predicates)
            .order_by(
                Call.started_at.desc().nullslast(),
                Call.created_at.desc(),
                Call.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return CallHistoryPage(
            calls=list(result.scalars()),
            total=int(total or 0),
        )
```

Do not join `CallMessage` and do not inspect the complete JSON document. The generic SQLAlchemy JSON string accessor must remain because it compiles for both SQLite JSON1 and PostgreSQL JSON.

Update the direct repository assertion in `apps/api/tests/calls/test_call_lifecycle.py` to:

```python
    page = await repository.list_visible_page_by_user_id(active_user.id)

    assert [call.id for call in page.calls] == [visible_call.id]
```

- [ ] **Step 4: Add the typed service result and normalized query handoff**

In `apps/api/app/services/call_history_service.py`, import `dataclass` and define:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CallHistoryPageResult:
    calls: list[CallHistoryListItem]
    total: int
    limit: int
    offset: int
    has_more: bool
```

Replace `list_calls` with:

```python
    async def list_calls(
        self,
        user_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
    ) -> CallHistoryPageResult:
        normalized_query = query.strip() if query is not None else None
        if normalized_query == "":
            normalized_query = None
        page = await self.call_repository.list_visible_page_by_user_id(
            user_id,
            limit=limit,
            offset=offset,
            query=normalized_query,
        )
        calls = [self._list_item(call) for call in page.calls]
        return CallHistoryPageResult(
            calls=calls,
            total=page.total,
            limit=limit,
            offset=offset,
            has_more=offset + len(calls) < page.total,
        )
```

- [ ] **Step 5: Run the deterministic page test and verify GREEN**

Run the command from Step 2 again.

Expected: PASS with the first page ordered by descending ID after tied timestamps, `total == 3`, and the removed/foreign rows excluded.

- [ ] **Step 6: Add the complete search-scope tests**

Append these tests to `apps/api/tests/calls/test_call_history_search.py`:

```python
@pytest.mark.anyio
async def test_search_matches_summary_and_caller_intent_case_insensitively(
    db_session,
    active_user,
) -> None:
    summary_call = make_call(
        call_id="00000000-0000-0000-0000-000000000011",
        user_id=active_user.id,
        caller_number="+33187000011",
        summary_text="Asked about OPENING hours",
    )
    intent_call = make_call(
        call_id="00000000-0000-0000-0000-000000000012",
        user_id=active_user.id,
        caller_number="+33187000012",
        summary_data={
            "summary_text": "Appointment request",
            "caller_intent": "Book a Consultation",
            "action_items": [],
            "sentiment": "neutral",
            "follow_up_required": False,
            "private_debug_value": "debug value",
        },
    )
    transcript_only_call = make_call(
        call_id="00000000-0000-0000-0000-000000000013",
        user_id=active_user.id,
        caller_number="+33187000013",
    )
    db_session.add_all([summary_call, intent_call, transcript_only_call])
    await db_session.flush()
    db_session.add(
        CallMessage(
            call_id=transcript_only_call.id,
            speaker="CALLER",
            text="transcript-only secret phrase",
            sequence_number=1,
        )
    )
    await db_session.commit()

    service = CallHistoryService(db_session, recording_service=None)

    opening = await service.list_calls(active_user.id, query="opening")
    consultation = await service.list_calls(active_user.id, query="CONSULTATION")
    transcript = await service.list_calls(active_user.id, query="secret phrase")
    arbitrary_json = await service.list_calls(active_user.id, query="debug value")
    prose_with_digit = await service.list_calls(active_user.id, query="invoice 2")
    blank = await service.list_calls(active_user.id, query="   ")

    assert [item.id for item in opening.calls] == [summary_call.id]
    assert [item.id for item in consultation.calls] == [intent_call.id]
    assert transcript.total == 0
    assert arbitrary_json.total == 0
    assert prose_with_digit.total == 0
    assert blank.total == 3


@pytest.mark.anyio
async def test_phone_search_normalizes_punctuation_and_requires_three_digits(
    db_session,
    active_user,
) -> None:
    phone_call = make_call(
        call_id="00000000-0000-0000-0000-000000000021",
        user_id=active_user.id,
        caller_number="+33187001234",
        summary_text="Unrelated summary",
    )
    db_session.add(phone_call)
    await db_session.commit()
    service = CallHistoryService(db_session, recording_service=None)

    international = await service.list_calls(active_user.id, query="+33 1 87")
    punctuated = await service.list_calls(active_user.id, query="(187)")
    domestic = await service.list_calls(active_user.id, query="01 87")
    too_short = await service.list_calls(active_user.id, query="87")

    assert [item.id for item in international.calls] == [phone_call.id]
    assert [item.id for item in punctuated.calls] == [phone_call.id]
    assert [item.id for item in domestic.calls] == [phone_call.id]
    assert too_short.total == 0
```

- [ ] **Step 7: Run all new search tests and verify GREEN**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/calls/test_call_history_search.py
```

Expected: all tests PASS. A failure involving JSON extraction must be fixed in the repository expression, not by searching serialized arbitrary JSON.

- [ ] **Step 8: Write failing HTTP contract and bounds assertions**

Update `seed_call_history` to accept a caller-number override for its newest
call:

```python
async def seed_call_history(
    database_url: str,
    *,
    clerk_user_id: str,
    email: str,
    user_status: str = "active",
    newest_caller_number: str = "+33111111111",
) -> dict[str, UUID]:
    # Existing setup remains unchanged.
    newest_call = Call(
        # Existing fields remain unchanged.
        caller_number=newest_caller_number,
    )
```

Update `test_list_calls_returns_visible_calls_newest_first` in `apps/api/tests/calls/test_call_history_api.py` to assert:

```python
    assert response.status_code == 200
    payload = response.json()
    assert [UUID(item["id"]) for item in payload["calls"]] == [
        ids["newest_id"],
        ids["older_id"],
    ]
    assert payload == {
        "calls": payload["calls"],
        "total": 2,
        "limit": 20,
        "offset": 0,
        "has_more": False,
    }
```

Add:

```python
@pytest.mark.anyio
async def test_list_calls_applies_search_and_pagination_metadata(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    ids = await seed_call_history(
        client_database_url,
        clerk_user_id="user_calls_search",
        email="calls-search@example.invalid",
    )

    response = await async_client.get(
        "/api/calls?q=older&limit=1&offset=0",
        headers={
            "authorization": (
                f"Bearer {rs256_clerk_token_for('user_calls_search')}"
            )
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "calls": [response.json()["calls"][0]],
        "total": 1,
        "limit": 1,
        "offset": 0,
        "has_more": False,
    }
    assert UUID(response.json()["calls"][0]["id"]) == ids["older_id"]


@pytest.mark.anyio
async def test_list_calls_phone_search_matches_domestic_trunk_prefix_to_e164_number(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
) -> None:
    ids = await seed_call_history(
        client_database_url,
        clerk_user_id="user_calls_domestic_phone_search",
        email="calls-domestic-phone-search@example.invalid",
        newest_caller_number="+33187001234",
    )

    response = await async_client.get(
        "/api/calls",
        params={"q": "01 87"},
        headers={
            "authorization": (
                "Bearer "
                f"{rs256_clerk_token_for('user_calls_domestic_phone_search')}"
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [UUID(item["id"]) for item in payload["calls"]] == [
        ids["newest_id"]
    ]
    assert payload["limit"] == 20
    assert payload["offset"] == 0
    assert payload["has_more"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query_string",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        f"q={'x' * 101}",
    ],
)
async def test_list_calls_rejects_invalid_query_bounds(
    async_client,
    client_database_url,
    rs256_clerk_token_for,
    query_string,
) -> None:
    await seed_user(
        client_database_url,
        clerk_user_id="user_calls_bounds",
        email="calls-bounds@example.invalid",
    )
    response = await async_client.get(
        f"/api/calls?{query_string}",
        headers={
            "authorization": (
                f"Bearer {rs256_clerk_token_for('user_calls_bounds')}"
            )
        },
    )

    assert response.status_code == 422
```

Update the inactive-owner list assertion to require `total == 1`, `limit == 20`, `offset == 0`, and `has_more is False`.

Update the three existing direct service assertions at lines 379, 424-429, and
446-451 so they index `.calls[0]` on the returned `CallHistoryPageResult`
instead of indexing the result object itself.

- [ ] **Step 9: Run the HTTP tests and capture the expected RED result**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/calls/test_call_history_api.py::test_list_calls_returns_visible_calls_newest_first \
  tests/calls/test_call_history_api.py::test_list_calls_applies_search_and_pagination_metadata \
  tests/calls/test_call_history_api.py::test_list_calls_phone_search_matches_domestic_trunk_prefix_to_e164_number \
  tests/calls/test_call_history_api.py::test_list_calls_rejects_invalid_query_bounds
```

Expected: FAIL because the Pydantic response still exposes only `calls`, the router has no `q` parameter, and it passes the page object as though it were a list.
The domestic phone regression additionally returns no calls until trunk-prefix
matching is implemented.

- [ ] **Step 10: Implement the FastAPI response and request contract**

Change `CallHistoryListResponse` in `apps/api/app/schemas/calls.py` to:

```python
class CallHistoryListResponse(BaseModel):
    calls: list[CallHistoryListItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    has_more: bool
```

Change the router function in `apps/api/app/routers/calls.py` to:

```python
@router.get("", response_model=CallHistoryListResponse)
@limiter.limit("60/minute")
async def list_calls(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: CallHistoryService = Depends(get_call_history_service),
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CallHistoryListResponse:
    page = await service.list_calls(
        identity.internal_user_id,
        limit=limit,
        offset=offset,
        query=q,
    )
    return CallHistoryListResponse(
        calls=page.calls,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )
```

Keep the existing `60/minute` limiter and `require_user_identity` dependency unchanged.

- [ ] **Step 11: Run the focused API call-history suite and verify GREEN**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/calls/test_call_history_search.py \
  tests/calls/test_call_history_api.py \
  tests/calls/test_call_lifecycle.py
```

Expected: all focused tests PASS, including inactive history access and the existing call deletion/detail coverage.

- [ ] **Step 12: Verify the search expression against PostgreSQL**

In `apps/api/tests/conftest.py`, import `os` and let the real API test fixture
use an explicit PostgreSQL URL when requested while retaining isolated SQLite
by default:

```python
        database_url = os.getenv("CLIENT_TEST_DATABASE_URL")
        if database_url is None:
            database_path = tmp_path / "test_client.db"
            database_url = f"sqlite+aiosqlite:///{database_path}"
        elif database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://",
                "postgresql+asyncpg://",
                1,
            )
```

Start a disposable PostgreSQL container:

```bash
docker run --detach --rm --name presvo-call-search-postgres \
  --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD=postgres \
  --env POSTGRES_DB=ai_call_test \
  --publish 5432:5432 \
  --health-cmd='pg_isready -U postgres -d ai_call_test' \
  --health-interval=1s \
  --health-timeout=5s \
  --health-retries=30 \
  postgres:17.8-bookworm
```

Wait until `docker inspect --format '{{.State.Health.Status}}' presvo-call-search-postgres` prints `healthy`, then run:

```bash
cd apps/api
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test \
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test \
CLIENT_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test \
UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q -ra \
  tests/calls/test_call_history_search.py \
  tests/calls/test_call_history_api.py::test_list_calls_applies_search_and_pagination_metadata \
  tests/calls/test_call_history_api.py::test_list_calls_phone_search_matches_domestic_trunk_prefix_to_e164_number
```

Expected: all selected tests PASS, the API tests use PostgreSQL through
`client_database_url`, and the summary contains no PostgreSQL skip.

Remove the disposable container:

```bash
docker rm --force presvo-call-search-postgres
```

- [ ] **Step 13: Update the public API documentation**

In `docs/architecture/call-history-api.md`, document:

```markdown
Query parameters:

- `q`: optional trimmed search text, maximum 100 characters
- `limit`: page size from 1 through 100, default 20
- `offset`: zero-based row offset, default 0

Search is owner-scoped and excludes removed calls. It matches `summary_text`
and structured `caller_intent` case-insensitively. A fully phone-shaped query
with at least three digits also matches the stored caller number after query
punctuation is removed. When those digits start with a domestic trunk `0`, the
search also tries the digits with exactly that leading zero removed, so `01 87`
matches an E.164 number containing `33187`. Transcripts and arbitrary summary
metadata are not searched.
```

Extend the response example with:

```json
{
  "calls": [],
  "total": 47,
  "limit": 20,
  "offset": 20,
  "has_more": true
}
```

State that rows use `started_at DESC NULLS LAST`, `created_at DESC`, and `id DESC`.

- [ ] **Step 14: Run backend static checks and commit the backend deliverable**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
cd ../..
git diff --check
git add \
  apps/api/app/repositories/call_repository.py \
  apps/api/app/services/call_history_service.py \
  apps/api/app/schemas/calls.py \
  apps/api/app/routers/calls.py \
  apps/api/tests/conftest.py \
  apps/api/tests/calls/test_call_history_search.py \
  apps/api/tests/calls/test_call_history_api.py \
  apps/api/tests/calls/test_call_lifecycle.py \
  docs/architecture/call-history-api.md
git commit -m "feat(api): add call history pagination and search"
```

Expected: Ruff and mypy pass, `git diff --check` is silent, and the commit contains only the backend contract, tests, and API documentation.

---

### Task 2: Typed Web Page Data and URL Navigation Utilities

**Files:**

- Create: `apps/web/src/lib/calls/call-history-navigation.ts`
- Create: `apps/web/tests/lib/calls-api.test.ts`
- Create: `apps/web/tests/lib/call-history-navigation.test.ts`
- Modify: `apps/web/src/lib/types/calls.ts:23-25`
- Modify: `apps/web/src/lib/api/calls.ts:1-11`
- Modify: `apps/web/src/app/(app)/dashboard/calls/page.tsx:16-18`
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx:34-50`
- Modify: `apps/web/tests/app/calls-page.test.tsx:44-77`
- Modify: `apps/web/tests/app/home-page.test.tsx:31-141`
- Modify: `apps/web/tests/app/dashboard-onboarding.test.tsx:92-213`
- Modify: `apps/web/tests/app/app-shell.test.tsx:83-95`

**Interfaces:**

- Consumes:
  - API `CallHistoryListResponse(calls, total, limit, offset, has_more)` from Task 1.
- Produces:
  - `ListCallsOptions { limit?: number; offset?: number; query?: string }`
  - `listCalls(options?: ListCallsOptions) -> Promise<CallHistoryListResponse>`
  - `CALLS_PAGE_SIZE = 20`
  - `CallHistorySearchParams`
  - `parseCallHistoryNavigation(params) -> { query: string; page: number; limit: 20; offset: number }`
  - `callHistoryPageCount(total: number, pageSize?: number) -> number`
  - `buildCallHistoryHref(query: string, page: number) -> string`

- [ ] **Step 1: Write failing API-client contract tests**

Create `apps/web/tests/lib/calls-api.test.ts`:

```typescript
import { beforeEach, describe, expect, it, vi } from "vitest";

const backendFetchMock = vi.fn();

vi.mock("@/lib/api/backend-client", () => ({
  backendFetch: backendFetchMock,
}));

describe("calls API client", () => {
  beforeEach(() => {
    backendFetchMock.mockReset();
  });

  it("returns the complete page and sends normalized named options", async () => {
    const page = {
      calls: [],
      total: 47,
      limit: 20,
      offset: 20,
      has_more: true,
    };
    backendFetchMock.mockResolvedValueOnce(page);
    const { listCalls } = await import("@/lib/api/calls");

    await expect(
      listCalls({ limit: 20, offset: 20, query: " opening hours " }),
    ).resolves.toEqual(page);
    expect(backendFetchMock).toHaveBeenCalledWith(
      "/api/calls?limit=20&offset=20&q=opening+hours",
    );
  });

  it("omits q for an unfiltered request", async () => {
    backendFetchMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    });
    const { listCalls } = await import("@/lib/api/calls");

    await listCalls({ limit: 5 });

    expect(backendFetchMock).toHaveBeenCalledWith(
      "/api/calls?limit=5&offset=0",
    );
  });
});
```

- [ ] **Step 2: Run the client tests and capture RED**

Run:

```bash
cd apps/web
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run test:ci -- tests/lib/calls-api.test.ts
```

Expected: FAIL because `listCalls` accepts positional numbers, does not send `q`, and returns only `response.calls`.

- [ ] **Step 3: Implement the typed response and named client options**

Change `CallHistoryListResponse` in `apps/web/src/lib/types/calls.ts` to:

```typescript
export type CallHistoryListResponse = {
  calls: CallHistoryListItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};
```

Replace `listCalls` in `apps/web/src/lib/api/calls.ts` with:

```typescript
export type ListCallsOptions = {
  limit?: number;
  offset?: number;
  query?: string;
};

export async function listCalls({
  limit = 20,
  offset = 0,
  query,
}: ListCallsOptions = {}): Promise<CallHistoryListResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const normalizedQuery = query?.trim();
  if (normalizedQuery) {
    params.set("q", normalizedQuery);
  }
  return backendFetch<CallHistoryListResponse>(
    `/api/calls?${params.toString()}`,
  );
}
```

- [ ] **Step 4: Run the client tests and verify GREEN**

Run the command from Step 2.

Expected: both client tests PASS.

- [ ] **Step 5: Write failing pure URL-navigation tests**

Create `apps/web/tests/lib/call-history-navigation.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import {
  buildCallHistoryHref,
  callHistoryPageCount,
  parseCallHistoryNavigation,
} from "@/lib/calls/call-history-navigation";

describe("call history navigation", () => {
  it("trims q and derives a one-based page offset", () => {
    expect(
      parseCallHistoryNavigation({
        q: [" opening hours ", "ignored"],
        page: "2",
      }),
    ).toEqual({
      query: "opening hours",
      page: 2,
      limit: 20,
      offset: 20,
    });
  });

  it.each([
    undefined,
    "",
    "0",
    "-2",
    "1.5",
    "abc",
    "9007199254740992",
  ])("resolves malformed page %s to page one", (page) => {
    expect(parseCallHistoryNavigation({ page }).page).toBe(1);
    expect(parseCallHistoryNavigation({ page }).offset).toBe(0);
  });

  it("calculates at least one page", () => {
    expect(callHistoryPageCount(0)).toBe(1);
    expect(callHistoryPageCount(40)).toBe(2);
    expect(callHistoryPageCount(41)).toBe(3);
  });

  it("builds canonical links that retain q and omit page one", () => {
    expect(buildCallHistoryHref("opening hours", 2)).toBe(
      "/dashboard/calls?q=opening+hours&page=2",
    );
    expect(buildCallHistoryHref("opening hours", 1)).toBe(
      "/dashboard/calls?q=opening+hours",
    );
    expect(buildCallHistoryHref("", 1)).toBe("/dashboard/calls");
  });
});
```

- [ ] **Step 6: Run the navigation tests and capture RED**

Run:

```bash
cd apps/web
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run test:ci -- tests/lib/call-history-navigation.test.ts
```

Expected: FAIL because `@/lib/calls/call-history-navigation` does not exist.

- [ ] **Step 7: Implement the pure navigation module**

Create `apps/web/src/lib/calls/call-history-navigation.ts`:

```typescript
export const CALLS_PAGE_SIZE = 20 as const;

export type CallHistorySearchParams = {
  q?: string | string[];
  page?: string | string[];
};

export type CallHistoryNavigation = {
  query: string;
  page: number;
  limit: typeof CALLS_PAGE_SIZE;
  offset: number;
};

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function parsePage(value: string | undefined): number {
  if (value === undefined || !/^[1-9]\d*$/.test(value)) {
    return 1;
  }
  const page = Number(value);
  if (!Number.isSafeInteger(page)) {
    return 1;
  }
  const offset = (page - 1) * CALLS_PAGE_SIZE;
  return Number.isSafeInteger(offset) ? page : 1;
}

export function parseCallHistoryNavigation(
  params: CallHistorySearchParams,
): CallHistoryNavigation {
  const query = firstValue(params.q)?.trim() ?? "";
  const page = parsePage(firstValue(params.page));
  return {
    query,
    page,
    limit: CALLS_PAGE_SIZE,
    offset: (page - 1) * CALLS_PAGE_SIZE,
  };
}

export function callHistoryPageCount(
  total: number,
  pageSize = CALLS_PAGE_SIZE,
): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export function buildCallHistoryHref(
  query: string,
  page: number,
): string {
  const params = new URLSearchParams();
  if (query) {
    params.set("q", query);
  }
  if (page > 1) {
    params.set("page", String(page));
  }
  const queryString = params.toString();
  return queryString
    ? `/dashboard/calls?${queryString}`
    : "/dashboard/calls";
}
```

- [ ] **Step 8: Run both library test files and verify GREEN**

Run:

```bash
cd apps/web
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run test:ci -- tests/lib/calls-api.test.ts tests/lib/call-history-navigation.test.ts
```

Expected: all library tests PASS.

- [ ] **Step 9: Migrate the two production callers without changing visible behavior**

In the calls page, temporarily consume the complete response while preserving its current unfiltered rendering:

```typescript
  const page = await listCalls({ limit: CALLS_PAGE_SIZE });

  return <CallsTable calls={page.calls} />;
```

Import `CALLS_PAGE_SIZE` from the new navigation module.

In the dashboard page, rename the `Promise.all` result to `callsPage`, call:

```typescript
listCalls({ limit: 5 })
```

Then assign:

```typescript
const calls = callsPage.calls;
```

before rendering `StatusSummaryCards` and `RecentCallsList`. Do not pass `q` or `offset`, and do not change those components.

- [ ] **Step 10: Adapt all web mocks to the full response and protect the dashboard request**

For each empty `listCallsMock.mockResolvedValueOnce([])` in:

- `apps/web/tests/app/calls-page.test.tsx`
- `apps/web/tests/app/home-page.test.tsx`
- `apps/web/tests/app/dashboard-onboarding.test.tsx`
- `apps/web/tests/app/app-shell.test.tsx`

return this exact shape, using `limit: 20` for calls-page/app-shell tests and `limit: 5` for dashboard tests:

```typescript
{
  calls: [],
  total: 0,
  limit: 5,
  offset: 0,
  has_more: false,
}
```

Wrap populated arrays in the same shape and set `total` to the array length.

In `apps/web/tests/app/home-page.test.tsx`, add after rendering:

```typescript
expect(listCallsMock).toHaveBeenCalledWith({ limit: 5 });
```

In `apps/web/tests/app/dashboard-onboarding.test.tsx`, keep the pre-dashboard redirect assertion that `listCallsMock` was not called.

- [ ] **Step 11: Run all affected web tests and type checks**

Run:

```bash
cd apps/web
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run test:ci -- \
  tests/lib/calls-api.test.ts \
  tests/lib/call-history-navigation.test.ts \
  tests/app/calls-page.test.tsx \
  tests/app/home-page.test.tsx \
  tests/app/dashboard-onboarding.test.tsx \
  tests/app/app-shell.test.tsx
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run typecheck
```

Expected: all selected tests and TypeScript checks PASS. Dashboard tests must prove `{ limit: 5 }` is used and no search is sent.

- [ ] **Step 12: Commit the typed web-data deliverable**

Run:

```bash
cd ../..
git diff --check
git add \
  apps/web/src/lib/types/calls.ts \
  apps/web/src/lib/api/calls.ts \
  apps/web/src/lib/calls/call-history-navigation.ts \
  'apps/web/src/app/(app)/dashboard/calls/page.tsx' \
  'apps/web/src/app/(app)/dashboard/page.tsx' \
  apps/web/tests/lib/calls-api.test.ts \
  apps/web/tests/lib/call-history-navigation.test.ts \
  apps/web/tests/app/calls-page.test.tsx \
  apps/web/tests/app/home-page.test.tsx \
  apps/web/tests/app/dashboard-onboarding.test.tsx \
  apps/web/tests/app/app-shell.test.tsx
git commit -m "refactor(web): consume paged call history"
```

Expected: `git diff --check` is silent and the commit leaves both production callers working with the complete response.

---

### Task 3: Server-Rendered Search, Pagination Controls, and Final Gates

**Files:**

- Create: `apps/web/src/components/calls/call-history-controls.tsx`
- Modify: `apps/web/src/components/calls/calls-table.tsx:13-33`
- Modify: `apps/web/src/app/(app)/dashboard/calls/page.tsx:1-20`
- Modify: `apps/web/tests/app/calls-page.test.tsx:33-78`
- Modify: `apps/web/tests/app/app-shell.test.tsx:83-95`
- Modify: `docs/PROJECT_STATUS.md:56,132`

**Interfaces:**

- Consumes:
  - `listCalls({ limit, offset, query }) -> Promise<CallHistoryListResponse>` from Task 2.
  - `parseCallHistoryNavigation`, `buildCallHistoryHref`, `callHistoryPageCount`, and `CALLS_PAGE_SIZE` from Task 2.
- Produces:
  - `CallHistorySearch({ query: string })`
  - `CallHistoryPagination({ query: string; page: number; pageSize: number; total: number; returnedCount: number })`
  - `CallsPage({ searchParams: Promise<CallHistorySearchParams> })`
  - `CallsTable({ calls: CallHistoryListItem[]; query?: string })`

- [ ] **Step 1: Replace the calls-page list test with failing search/pagination behavior tests**

In `apps/web/tests/app/calls-page.test.tsx`, add:

```typescript
const callItem = {
  id: "call-1",
  status: "completed",
  caller_number: "+33123456789",
  started_at: "2026-03-28T10:00:00Z",
  ended_at: "2026-03-28T10:01:00Z",
  duration_seconds: 60,
  minutes_charged: 1,
  summary_text: "Caller asked about opening hours.",
  summary_status: "ready" as const,
  caller_intent: "Check opening hours",
  action_items: ["Send weekday hours"],
  sentiment: "neutral",
  follow_up_required: true,
  has_recording: true,
};

const secondPageItems = Array.from({ length: 20 }, (_, index) =>
  index === 0
    ? callItem
    : {
        ...callItem,
        id: `call-${index + 1}`,
        summary_text: `Additional call ${index + 1}`,
        caller_intent: null,
        action_items: null,
        follow_up_required: false,
      },
);
```

Replace the existing combined empty/populated list test with:

```typescript
it("reads async URL state and renders range-preserving navigation", async () => {
  listCallsMock.mockResolvedValueOnce({
    calls: secondPageItems,
    total: 47,
    limit: 20,
    offset: 20,
    has_more: true,
  });
  const { default: Page } = await import(
    "@/app/(app)/dashboard/calls/page"
  );

  render(
    await Page({
      searchParams: Promise.resolve({ q: " opening ", page: "2" }),
    }),
  );

  expect(listCallsMock).toHaveBeenCalledWith({
    limit: 20,
    offset: 20,
    query: "opening",
  });
  expect(screen.getByLabelText("Search calls")).toHaveValue("opening");
  expect(screen.getByText("Showing 21–40 of 47 calls")).toBeInTheDocument();
  expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Previous" })).toHaveAttribute(
    "href",
    "/dashboard/calls?q=opening",
  );
  expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
    "href",
    "/dashboard/calls?q=opening&page=3",
  );
  expect(screen.getByRole("link", { name: "Clear" })).toHaveAttribute(
    "href",
    "/dashboard/calls",
  );
  expect(
    document.querySelector('form input[name="page"]'),
  ).not.toBeInTheDocument();
});

it("distinguishes no history from no search matches", async () => {
  listCallsMock
    .mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 20,
      offset: 0,
      has_more: false,
    })
    .mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 20,
      offset: 0,
      has_more: false,
    });
  const { default: Page } = await import(
    "@/app/(app)/dashboard/calls/page"
  );

  const historyView = render(
    await Page({ searchParams: Promise.resolve({}) }),
  );
  expect(screen.getByText("No calls yet")).toBeInTheDocument();
  historyView.unmount();

  render(
    await Page({
      searchParams: Promise.resolve({ q: "opening", page: "1" }),
    }),
  );
  expect(
    screen.getByText("No calls match “opening”"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Try another caller number or summary phrase."),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Clear" })).toHaveAttribute(
    "href",
    "/dashboard/calls",
  );
});

it("shows disabled first and final page controls", async () => {
  listCallsMock
    .mockResolvedValueOnce({
      calls: [callItem],
      total: 21,
      limit: 20,
      offset: 0,
      has_more: true,
    })
    .mockResolvedValueOnce({
      calls: [callItem],
      total: 21,
      limit: 20,
      offset: 20,
      has_more: false,
    });
  const { default: Page } = await import(
    "@/app/(app)/dashboard/calls/page"
  );

  const first = render(
    await Page({ searchParams: Promise.resolve({ page: "1" }) }),
  );
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Next" })).toBeInTheDocument();
  first.unmount();

  render(
    await Page({ searchParams: Promise.resolve({ page: "2" }) }),
  );
  expect(screen.getByRole("link", { name: "Previous" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
});

it("redirects an out-of-range page to the final matching page", async () => {
  listCallsMock.mockResolvedValueOnce({
    calls: [],
    total: 21,
    limit: 20,
    offset: 80,
    has_more: false,
  });
  const { default: Page } = await import(
    "@/app/(app)/dashboard/calls/page"
  );

  await expect(
    Page({
      searchParams: Promise.resolve({ q: "opening", page: "5" }),
    }),
  ).rejects.toThrow("NEXT_REDIRECT");
  expect(redirectMock).toHaveBeenCalledWith(
    "/dashboard/calls?q=opening&page=2",
  );
});

it("redirects a zero-result later page to filtered page one", async () => {
  listCallsMock.mockResolvedValueOnce({
    calls: [],
    total: 0,
    limit: 20,
    offset: 20,
    has_more: false,
  });
  const { default: Page } = await import(
    "@/app/(app)/dashboard/calls/page"
  );

  await expect(
    Page({
      searchParams: Promise.resolve({ q: "opening", page: "2" }),
    }),
  ).rejects.toThrow("NEXT_REDIRECT");
  expect(redirectMock).toHaveBeenCalledWith(
    "/dashboard/calls?q=opening",
  );
});
```

Retain the existing populated card assertions in the first test and retain every call-detail/deletion test below it.

- [ ] **Step 2: Run calls-page tests and capture RED**

Run:

```bash
cd apps/web
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run test:ci -- tests/app/calls-page.test.tsx
```

Expected: FAIL because the page does not accept/await `searchParams`, the
controls do not exist, no-match copy is absent, and positive-total and
zero-total out-of-range pages do not redirect.

- [ ] **Step 3: Implement server-compatible search and pagination controls**

Create `apps/web/src/components/calls/call-history-controls.tsx`:

```tsx
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  buildCallHistoryHref,
  callHistoryPageCount,
} from "@/lib/calls/call-history-navigation";

export function CallHistorySearch({ query }: { query: string }) {
  return (
    <form
      action="/dashboard/calls"
      method="get"
      className="flex flex-col gap-3 sm:flex-row sm:items-end"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <label htmlFor="call-search" className="text-sm font-medium">
          Search calls
        </label>
        <Input
          id="call-search"
          name="q"
          type="search"
          maxLength={100}
          defaultValue={query}
          placeholder="Caller number or summary"
        />
      </div>
      <div className="flex items-center gap-2">
        <Button type="submit">Search</Button>
        {query ? (
          <Button asChild variant="ghost">
            <Link href="/dashboard/calls">Clear</Link>
          </Button>
        ) : null}
      </div>
    </form>
  );
}

type CallHistoryPaginationProps = {
  query: string;
  page: number;
  pageSize: number;
  total: number;
  returnedCount: number;
};

export function CallHistoryPagination({
  query,
  page,
  pageSize,
  total,
  returnedCount,
}: CallHistoryPaginationProps) {
  if (total === 0) {
    return null;
  }

  const totalPages = callHistoryPageCount(total, pageSize);
  const firstResult = (page - 1) * pageSize + 1;
  const lastResult = firstResult + returnedCount - 1;

  return (
    <nav
      aria-label="Call history pages"
      className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="text-muted-foreground text-sm">
        <p>
          Showing {firstResult}–{lastResult} of {total} calls
        </p>
        <p>
          Page {page} of {totalPages}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {page > 1 ? (
          <Button asChild variant="outline">
            <Link href={buildCallHistoryHref(query, page - 1)}>
              Previous
            </Link>
          </Button>
        ) : (
          <Button type="button" variant="outline" disabled>
            Previous
          </Button>
        )}
        {page < totalPages ? (
          <Button asChild variant="outline">
            <Link href={buildCallHistoryHref(query, page + 1)}>Next</Link>
          </Button>
        ) : (
          <Button type="button" variant="outline" disabled>
            Next
          </Button>
        )}
      </div>
    </nav>
  );
}
```

Do not add `"use client"` to this file. The GET form must contain only the named `q` input, so submitting a new search always starts at page 1.

- [ ] **Step 4: Implement URL-driven calls-page orchestration and canonical redirects**

Replace `apps/web/src/app/(app)/dashboard/calls/page.tsx` with:

```tsx
import { redirect } from "next/navigation";

import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import {
  CallHistoryPagination,
  CallHistorySearch,
} from "@/components/calls/call-history-controls";
import { CallsTable } from "@/components/calls/calls-table";
import { listCalls } from "@/lib/api/calls";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import {
  buildCallHistoryHref,
  callHistoryPageCount,
  parseCallHistoryNavigation,
  type CallHistorySearchParams,
} from "@/lib/calls/call-history-navigation";

type CallsPageProps = {
  searchParams: Promise<CallHistorySearchParams>;
};

export default async function CallsPage({
  searchParams,
}: CallsPageProps) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Call history is unavailable"
        description="Configure Clerk in your local environment before loading protected call records."
      />
    );
  }

  const navigation = parseCallHistoryNavigation(await searchParams);
  const result = await listCalls({
    limit: navigation.limit,
    offset: navigation.offset,
    query: navigation.query,
  });
  const lastPage = callHistoryPageCount(result.total, navigation.limit);

  if (navigation.page > lastPage) {
    redirect(buildCallHistoryHref(navigation.query, lastPage));
  }

  return (
    <div className="flex flex-col gap-4 md:gap-6">
      <CallHistorySearch query={navigation.query} />
      <CallsTable calls={result.calls} query={navigation.query} />
      <CallHistoryPagination
        query={navigation.query}
        page={navigation.page}
        pageSize={navigation.limit}
        total={result.total}
        returnedCount={result.calls.length}
      />
    </div>
  );
}
```

The redirect must remain outside any `try/catch` because Next.js navigation works by throwing an internal control-flow error.

- [ ] **Step 5: Add distinct empty-state copy without changing populated call cards**

Change the `CallsTable` signature to:

```tsx
export function CallsTable({
  calls,
  query = "",
}: {
  calls: CallHistoryListItem[];
  query?: string;
}) {
```

Inside the existing empty branch, use:

```tsx
<EmptyTitle>
  {query ? `No calls match “${query}”` : "No calls yet"}
</EmptyTitle>
<EmptyDescription>
  {query
    ? "Try another caller number or summary phrase."
    : "Your visible call history will appear here after the first handled conversation."}
</EmptyDescription>
```

Keep the existing card header, populated call mapping, badges, outcomes, duration, and detail links unchanged. The always-visible search form supplies the no-match clear link.

- [ ] **Step 6: Update page invocations in tests for asynchronous `searchParams`**

In `apps/web/tests/app/calls-page.test.tsx`, call the page everywhere with:

```typescript
Page({ searchParams: Promise.resolve({}) })
```

or the explicit `q`/`page` object required by the test.

In `apps/web/tests/app/app-shell.test.tsx`, change:

```typescript
render(await CallsPage());
```

to:

```typescript
render(
  await CallsPage({
    searchParams: Promise.resolve({}),
  }),
);
```

- [ ] **Step 7: Run page, navigation, shell, and dashboard tests and verify GREEN**

Run:

```bash
cd apps/web
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run test:ci -- \
  tests/app/calls-page.test.tsx \
  tests/app/app-shell.test.tsx \
  tests/app/home-page.test.tsx \
  tests/app/dashboard-onboarding.test.tsx \
  tests/lib/calls-api.test.ts \
  tests/lib/call-history-navigation.test.ts
```

Expected: all selected tests PASS. The rendered controls include Search, Clear when active, Previous, and Next at narrow-width-compatible DOM order; no action is hidden behind a client-only interaction.

- [ ] **Step 8: Update project status**

Change the `Rich call-review workflow` row in `docs/PROJECT_STATUS.md` to:

```markdown
| Rich call-review workflow | **Partial** | Server-rendered pagination and caller-number/summary/intent search, inline original-audio playback, and structured next-action presentation are implemented; tags and notes remain. |
```

Remove `Call pagination, search` from the later-work bullet and retain only the still-unimplemented richer review items:

```markdown
- Call tags, notes, and richer review workflows
```

- [ ] **Step 9: Run the complete SQLite API quality gate**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: lock check, Ruff, mypy, and the complete SQLite-oriented API suite PASS.

- [ ] **Step 10: Run the complete PostgreSQL/Redis API gate with zero skips**

Start disposable services:

```bash
docker run --detach --rm --name presvo-api-test-postgres \
  --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD=postgres \
  --env POSTGRES_DB=ai_call_test \
  --publish 5432:5432 \
  --health-cmd='pg_isready -U postgres -d ai_call_test' \
  --health-interval=1s \
  --health-timeout=5s \
  --health-retries=30 \
  postgres:17.8-bookworm
docker run --detach --rm --name presvo-api-test-redis \
  --publish 6379:6379 \
  --health-cmd='redis-cli ping' \
  --health-interval=1s \
  --health-timeout=5s \
  --health-retries=30 \
  redis:7.4.7-alpine
```

Wait until both `docker inspect --format '{{.State.Health.Status}}' <container>` commands print `healthy`, then run:

```bash
cd apps/api
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test \
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test \
REDIS_URL=redis://127.0.0.1:6379/0 \
TEST_REDIS_URL=redis://127.0.0.1:6379/0 \
UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q -ra
```

Expected: the complete API suite PASS and the terminal summary reports zero skipped PostgreSQL tests.

Always clean up the disposable services after the test command, whether it passes or fails:

```bash
docker rm --force presvo-api-test-postgres presvo-api-test-redis
```

- [ ] **Step 11: Run the complete web quality and production-build gate**

Run:

```bash
cd apps/web
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run check
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run typecheck
/home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run test:ci
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_YWEuYWEk \
CLERK_SECRET_KEY=ci-build-only-placeholder \
API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_APP_URL=http://127.0.0.1:3000 \
NEXT_PUBLIC_REALTIME_ENABLED=false \
  /home/mo/.nvm/versions/node/v22.23.1/bin/node \
  /home/mo/.nvm/versions/node/v22.23.1/lib/node_modules/npm/bin/npm-cli.js \
  run build
```

Expected: Biome, TypeScript, the complete Vitest suite, and the production build PASS without new warnings.

- [ ] **Step 12: Inspect the final diff, commit, and prove a clean worktree**

Run:

```bash
cd ../..
git diff --check
git status --short
git diff --stat
git diff -- \
  'apps/web/src/app/(app)/dashboard/calls/page.tsx' \
  apps/web/src/components/calls/call-history-controls.tsx \
  apps/web/src/components/calls/calls-table.tsx \
  apps/web/tests/app/calls-page.test.tsx \
  docs/PROJECT_STATUS.md
git add \
  'apps/web/src/app/(app)/dashboard/calls/page.tsx' \
  apps/web/src/components/calls/call-history-controls.tsx \
  apps/web/src/components/calls/calls-table.tsx \
  apps/web/tests/app/calls-page.test.tsx \
  apps/web/tests/app/app-shell.test.tsx \
  docs/PROJECT_STATUS.md
git commit -m "feat(web): add call history search and pagination"
git status --short --branch
```

Expected:

- `git diff --check` is silent before commit.
- The reviewed diff contains no client fetch/debounce state and no call-card redesign.
- The final worktree is clean.
- `main` contains three focused implementation commits after the design/plan documentation commits.
- No deployment or provider action has occurred.
