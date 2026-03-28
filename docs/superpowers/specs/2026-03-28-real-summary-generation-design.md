# Real Summary Generation Design

## Goal

Replace the current placeholder post-call summary logic with real LLM-generated summaries.

The design must:

- stay provider-agnostic
- use Google Gemini as the default provider
- persist both a human-readable `summary_text` and a structured JSON summary on `calls`
- keep summary generation non-blocking so call completion still succeeds if summary generation fails

## Scope

### Included

- provider-agnostic summary provider interface
- Gemini-backed default provider
- structured summary output validation
- `calls.summary_data` JSON persistence
- `calls.summary_text` derived from structured output
- non-blocking failure handling inside call finalization

### Excluded

- summary retry queue
- admin/manual re-run endpoint
- multi-provider runtime switching beyond config
- UI work for rendering structured summaries

## Approach

Use a narrow provider boundary:

- `SummaryProvider`
  - generates a structured summary from transcript input
- `GeminiSummaryProvider`
  - default implementation
- `SummaryService`
  - orchestrates prompt construction
  - validates returned JSON
  - renders `summary_text`
  - handles provider failure without breaking call completion

This keeps the existing call-finalization architecture intact while replacing the fake summary output with a real generated result.

## Data Model

Add a nullable JSON column to [`calls`](/home/i933k/code/ai/bmad-opevo/apps/api/app/models/call.py):

- `summary_data`

Suggested stored shape:

```json
{
  "summary_text": "Caller asked about opening hours and weekend availability.",
  "caller_intent": "Ask about opening hours",
  "action_items": [
    "Confirm weekend schedule"
  ],
  "sentiment": "neutral",
  "follow_up_required": false
}
```

Persistence rules:

- `calls.summary_text`
  - stores the human-readable summary used by the current API
- `calls.summary_data`
  - stores the full structured JSON result

If generation fails, both remain `null`.

## Runtime Flow

1. Agent completes the call and enqueues finalization as it does today.
2. [`CallLifecycleService`](/home/i933k/code/ai/bmad-opevo/apps/api/app/services/call_lifecycle_service.py) receives transcript payload.
3. `SummaryService` sends transcript content to the configured summary provider.
4. Provider returns structured JSON.
5. `SummaryService` validates the output and extracts:
   - `summary_text`
   - `summary_data`
6. `CallLifecycleService` stores both fields on the `calls` row.
7. If provider invocation or validation fails:
   - log the failure
   - continue finalization
   - leave summary fields `null`

## Provider Boundary

### `SummaryProvider`

Expose one focused method, for example:

```python
async def generate_summary(self, transcript: list[dict]) -> StructuredSummary:
    ...
```

The provider contract should return normalized structured data, not raw model text.

### `GeminiSummaryProvider`

Default implementation using the configured Google Gemini API key.

Responsibilities:

- call the configured Gemini model
- request structured JSON output
- return parsed structured summary data

## Config

Keep config provider-agnostic even if Gemini is the only implementation right now.

Add:

- `SUMMARY_PROVIDER=gemini`
- `SUMMARY_MODEL=gemini-2.5-flash`

Reuse:

- `GOOGLE_API_KEY`

This creates the right extension point later without overbuilding a full provider registry for MVP.

## Service Behavior

[`SummaryService`](/home/i933k/code/ai/bmad-opevo/apps/api/app/services/summary_service.py) should:

- normalize transcript input
- ignore empty transcript lines
- build a provider-safe summary prompt
- validate the returned JSON shape
- render a stable stored `summary_text`

The service should not expose raw provider responses to callers.

## Validation Rules

The structured result must include:

- `summary_text: str`
- `caller_intent: str`
- `action_items: list[str]`
- `sentiment: str`
- `follow_up_required: bool`

If the provider output is malformed or missing required fields, treat it as a summary generation failure.

## Failure Handling

Summary generation is non-blocking.

If Gemini fails, times out, or returns invalid JSON:

- do not fail the call finalization transaction
- do not fail usage deduction
- do not fail transcript persistence
- leave `calls.summary_text` and `calls.summary_data` as `null`
- emit a structured log entry for later debugging

This preserves the operational stability of the current backend while improving summary quality when the provider succeeds.

## Testing Plan

Add tests for:

- structured summary success stores both `summary_text` and `summary_data`
- provider failure leaves both fields `null` and still completes the call
- malformed provider output is rejected and treated as non-blocking failure
- rendered `summary_text` is persisted to the `calls` row
- existing call finalization behavior still passes with the new summary path

This should include:

- unit tests for `SummaryService`
- lifecycle tests for call finalization success/failure persistence
- migration coverage through the normal API test setup

## Migration

Add an Alembic migration for `calls.summary_data`.

No backfill is required.

## Recommendation

Implement this as a focused backend change on top of the existing call-finalization flow.

It satisfies MVP requirements now, preserves operational safety, and leaves room for retries or alternate providers later without redesigning the whole lifecycle.
