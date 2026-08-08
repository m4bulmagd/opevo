# Call History Pagination and Search Design

Date: 2026-07-25  
Status: Approved design

## Goal

Complete the existing call-history list workflow with server-rendered,
bookmarkable pagination and search.

Customers must be able to:

- search visible calls by caller number, summary text, or caller intent;
- move through their complete visible history in pages of 20 calls;
- preserve the active search while navigating pages;
- distinguish an account with no calls from a search with no matches.

The feature must preserve the current authenticated owner boundary, removed-call
behavior, dashboard recent-call list, and read-only historical access for
inactive accounts.

## Scope

This increment includes:

- paged call-history API metadata;
- bounded server-side search;
- deterministic offset pagination;
- URL-driven calls-page controls;
- separate no-history and no-match states;
- API, repository, service, web, and production-build coverage.

This increment does not include:

- transcript search;
- status or date filters;
- tags, notes, or saved searches;
- cursor pagination;
- live result updates;
- a call-history schema migration;
- a broad redesign of the call cards or call-detail page.

## Chosen approach

Use URL-driven, server-rendered offset pagination.

Example:

```text
/dashboard/calls?q=opening&page=2
```

This approach fits the existing `limit` and `offset` API, keeps searches
bookmarkable, avoids client-fetch state and loading flicker, and produces a
small accessible interface. Cursor pagination is deferred until observed call
volume or query performance justifies its added complexity.

## API contract

### Request

`GET /api/calls` accepts:

- `q`: optional search text, trimmed, maximum 100 characters;
- `limit`: integer from 1 through 100, default 20;
- `offset`: non-negative integer, default 0.

A blank or whitespace-only `q` behaves as no search.

### Response

The existing `calls` array remains present. Pagination metadata is added:

```json
{
  "calls": [],
  "total": 47,
  "limit": 20,
  "offset": 20,
  "has_more": true
}
```

Field semantics:

- `total` is the number of visible calls matching the same owner and search
  predicate;
- `limit` and `offset` are the validated request values;
- `has_more` is true when another matching row exists after this page.

Adding metadata is backward-compatible for callers that currently consume only
the `calls` field.

## Search behavior

Search is always constrained by:

- `Call.user_id == authenticated owner`;
- `Call.deleted_at IS NULL`.

Text search is case-insensitive partial matching against:

- `summary_text`;
- the bounded `caller_intent` field stored in `summary_data`.

Phone search is enabled only when the complete query is phone-shaped: digits
plus common phone punctuation or whitespace. The server strips non-digits and
requires at least three digits before adding the caller-number predicate. It
then performs a partial digit match against the stored E.164 caller number.
This permits searches such as `01 87`, `+33 1 87`, or `187` without treating a
digit embedded in normal prose as a phone search.

Search does not inspect:

- transcripts;
- provider identifiers;
- private recording metadata;
- deleted customer content;
- arbitrary keys in `summary_data`.

## Repository and service boundaries

### Repository

`CallRepository` owns one shared visible-call search predicate and exposes a
paged result containing:

- the selected `Call` rows;
- the matching total.

The count and row queries must use the same predicate builder so they cannot
drift. Database access remains in the repository.

Rows are ordered deterministically by:

1. `started_at DESC NULLS LAST`;
2. `created_at DESC`;
3. `id DESC`.

The final ID tie-breaker prevents duplicate or missing rows when timestamps are
equal.

The implementation uses two bounded queries: one count and one page query. It
does not join transcripts or introduce an N+1 query.

### Service

`CallHistoryService` passes the normalized search request to the repository and
maps rows through the existing bounded list-item projection. It returns a
typed page result rather than a bare list.

### Router

The router:

- validates `q`, `limit`, and `offset`;
- invokes the service with the authenticated internal owner ID;
- serializes the page and pagination metadata.

Search or pagination never weakens authentication, owner scoping, rate limits,
or removed-call exclusion.

## Web data flow

The calls page is a React Server Component. It awaits Next.js `searchParams`
and derives:

- normalized `q`;
- a one-based `page`, defaulting to 1;
- `offset = (page - 1) * 20`.

The web API client returns the complete typed page response. The dashboard
recent-calls view requests five rows and consumes only the response `calls`
field. It remains unsearched and visually unchanged.

Invalid, negative, zero, fractional, or non-numeric page values resolve to
page 1.

When matching calls exist but the requested page is past the final page, the
server redirects to the last valid page while retaining `q`. A search with
zero total matches remains on page 1 and renders the no-match state.

## Calls-page interaction

The existing Opevo call cards remain intact.

Above the list, the page adds:

- a visible `Search calls` label;
- a text input initialized from `q`;
- a `Search` submit button;
- a `Clear` link when a search is active.

The form uses `method="get"`. It does not submit a page value, so every new
search begins on page 1. No client-side debounce, fetch state, or hydration is
required.

Below the results, the page shows:

- `Showing 21–40 of 47 calls`;
- `Page 2 of 3`;
- `Previous` and `Next` links.

Navigation links retain the current normalized search. Controls use disabled
button presentation when no corresponding page exists.

The controls stack on narrow screens and remain fully available. Search and
pagination are keyboard-operable and have explicit accessible names.

## Empty states

The page distinguishes:

### No call history

When `total == 0` and no search is active:

```text
No calls yet
Your visible call history will appear here after the first handled
conversation.
```

### No search matches

When `total == 0` and a search is active:

```text
No calls match “opening”
Try another caller number or summary phrase.
```

The no-match state includes a clear-search link. The search form remains
visible in both states.

## Error handling

- Invalid API query bounds return the framework's validated `422` response.
- Backend, authentication, or transport failures remain errors and are not
  converted into empty results.
- Page parsing is fail-safe and resolves malformed values to page 1.
- Out-of-range valid pages canonicalize through a server redirect.
- Search input is treated only as a bound database value; it is never
  interpolated into SQL.

## Performance

The first version intentionally uses the existing table and bounded count/page
queries. It introduces no extension or search index.

The query remains owner-scoped, excludes removed calls, limits returned rows,
and avoids transcript joins. If controlled-beta evidence shows search latency
growing with call volume, a later change may add PostgreSQL trigram indexes or
move to cursor pagination without changing the customer-facing search scope.

## Testing

### API and repository

Tests must prove:

- pagination metadata matches returned rows;
- count and row predicates remain aligned;
- calls are ordered deterministically when timestamps tie;
- case-insensitive summary matching;
- case-insensitive caller-intent matching;
- phone punctuation and digit normalization;
- prose containing a digit does not accidentally enable phone matching;
- blank search behaves as no search;
- deleted and cross-tenant calls never appear or affect totals;
- inactive owners retain read-only list access;
- limit, offset, and query-length bounds;
- no transcript content is searched.

### Web

Tests must prove:

- `q` and `page` are parsed from asynchronous `searchParams`;
- malformed pages resolve to page 1;
- a new search omits the page parameter;
- Previous and Next retain the active search;
- result range and page count are correct;
- first and final page controls are disabled appropriately;
- an out-of-range page redirects to the last page;
- no-history and no-match states are distinct;
- clear search returns to the unfiltered first page;
- dashboard recent calls still request five unfiltered rows;
- mobile-compatible controls retain every action.

### Gates

Required completion gates:

- focused API and web RED/GREEN evidence;
- complete API tests on SQLite;
- complete PostgreSQL/Redis API suite with zero PostgreSQL skips;
- Ruff and mypy;
- complete web tests, Biome check, and TypeScript;
- Node 22 production build;
- `git diff --check`;
- clean worktree and disposable infrastructure cleanup.

## Acceptance criteria

The feature is complete when:

1. An authenticated customer can search visible calls by caller number,
   summary text, or caller intent.
2. Search results include matching totals and deterministic pages of 20.
3. Calls-page URLs preserve query and page state.
4. Previous and Next navigation never loses the search.
5. No-history and no-match states communicate different conditions.
6. Removed or cross-tenant calls cannot appear or influence totals.
7. Dashboard recent calls remain unchanged.
8. Full API, PostgreSQL, web, type, lint, and production-build gates pass.
