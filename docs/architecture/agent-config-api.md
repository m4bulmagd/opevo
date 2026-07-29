# Agent Config API

This document describes the authenticated backend API for reading and updating a user's agent configuration.

## Scope

These endpoints control the editable runtime fields in `agent_configs`:

- `agent_name`
- `owner_context`
- `system_prompt`
- `knowledge_base`
- `pipeline_mode`
- `is_enabled`

When `is_enabled` changes, the backend also switches the user's assigned Telnyx number between `app-active` and `app-disabled` in the same request.

When the activation flow is enabled, assistant content remains profile-owned.
`agent_name` is synchronized to the receptionist name, while explicit owner
context, system prompt, and knowledge-base edits are stored as profile
overrides and projected to `agent_configs` in the same transaction. This keeps
the profile content revision and runtime projection revision equal, including
after later profile saves. For an already-confirmed profile, the explicit save
also advances its confirmed content revision so a later account reactivation
does not treat the saved assistant edit as an unreviewed onboarding draft.

## Authentication

Both endpoints require a valid Clerk bearer token for a user that has already been synced into the local `users` table.

Example header:

```http
Authorization: Bearer <clerk-session-token>
```

## Endpoints

### `GET /api/agent/config`

Returns the current editable config for the authenticated user.

Example:

```bash
curl -X GET http://localhost:8000/api/agent/config \
  -H "Authorization: Bearer <clerk-session-token>"
```

Success response:

```json
{
  "agent_name": "Ava",
  "owner_context": "Muhammad Abulmagd",
  "system_prompt": "Be helpful.",
  "knowledge_base": "Open weekdays",
  "pipeline_mode": "stt_llm_tts",
  "is_enabled": false
}
```

### `PATCH /api/agent/config`

Applies a partial update to the authenticated user's config.

Only the fields you send are updated.

Example:

```bash
curl -X PATCH http://localhost:8000/api/agent/config \
  -H "Authorization: Bearer <clerk-session-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "Reception",
    "knowledge_base": "Open weekdays",
    "pipeline_mode": "sts",
    "is_enabled": true
  }'
```

Success response:

```json
{
  "agent_name": "Reception",
  "owner_context": null,
  "system_prompt": "",
  "knowledge_base": "Open weekdays",
  "pipeline_mode": "sts",
  "is_enabled": true
}
```

## Patch Fields

All fields are optional in `PATCH /api/agent/config`.

| Field | Type | Meaning |
|---|---|---|
| `agent_name` | `string` | Public-facing assistant name used in call handling and prompts. |
| `owner_context` | `string \| null` | Optional business or owner context injected into prompting. |
| `system_prompt` | `string` | Custom instruction block for the assistant. |
| `knowledge_base` | `string` | Short domain knowledge or operating context available to the assistant. |
| `pipeline_mode` | `"stt_llm_tts" \| "sts"` | Runtime mode for the voice pipeline. |
| `is_enabled` | `boolean` | Whether the assigned number should route to the active or disabled Telnyx app. |

## Field Semantics

### `pipeline_mode`

Allowed values:

- `stt_llm_tts`
  - Default mode
  - Uses the standard Speechmatics/Deepgram + Gemini + TTS composition
- `sts`
  - Uses the Gemini Live native-audio runtime

Any other value is rejected by request validation.

### `is_enabled`

`is_enabled` is not just a DB flag.

When it changes:

- `true`
  - the backend updates the assigned number to the Telnyx `app-active` connection
- `false`
  - the backend updates the assigned number to the Telnyx `app-disabled` connection

This happens synchronously inside the same request. If the telephony switch fails, the config change is rolled back and the response is an error.

With the activation flow enabled, changing `is_enabled` from `false` to `true`
is owned by the verified go-live workflow and direct PATCH requests are
rejected. Sending the already-saved `true` value is idempotent and does not
enqueue another routing operation. Customers may still disable routing
directly.

## Error Responses

### `401 Unauthorized`

Returned when:

- the bearer token is missing
- the bearer token is invalid
- the Clerk user exists in Clerk but has not been synced into the local `users` table

Example:

```json
{
  "detail": "Missing bearer token"
}
```

### `404 Not Found`

Returned when the authenticated local user exists but no `agent_configs` row exists yet.

```json
{
  "detail": "Agent config not found"
}
```

### `409 Conflict`

Returned when `is_enabled` is being toggled but the user has no assigned phone number to switch.

```json
{
  "detail": "Phone number not found"
}
```

### `422 Unprocessable Entity`

Returned when the request body fails validation, for example an unsupported `pipeline_mode`.

### `502 Bad Gateway`

Returned when the telephony provider update fails during an `is_enabled` toggle.

```json
{
  "detail": "Failed to update telephony state"
}
```

## Transaction Behavior

`PATCH /api/agent/config` is transactional for config persistence plus telephony switching:

- non-toggle field updates commit normally
- toggle updates only commit if the Telnyx switch succeeds
- telephony failure rolls both the number state and the config flag back

## Current Test Coverage

The API is covered in [test_agent_config_api.py](../../apps/api/tests/agent/test_agent_config_api.py):

- full config read
- normal field patch
- enable toggle
- missing-number conflict
- rollback on telephony failure
