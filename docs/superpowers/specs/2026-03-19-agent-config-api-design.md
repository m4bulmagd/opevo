# Agent Config API Design

## Summary

This spec adds the missing backend API surface for user-editable agent configuration. The MVP needs a single backend-owned source of truth for agent settings, including prompt fields, knowledge base, runtime mode selection, and the enabled/disabled toggle that immediately switches the assigned Telnyx number between `app-active` and `app-disabled`.

The design keeps the external API small while separating HTTP concerns, persistence, and telephony side effects into distinct backend units. The public surface is limited to `GET /api/agent/config` and `PATCH /api/agent/config`.

## Goal

Provide a backend API that allows the authenticated user to read and update their full editable `AgentConfig`, with `is_enabled` changes taking effect immediately on the assigned Telnyx number in the same request.

## Non-Goals

- Building the frontend settings screen.
- Adding a separate toggle-only endpoint.
- Adding per-field audit history.
- Adding optimistic background reconciliation for enable/disable.
- Expanding beyond the existing single-agent-per-user model.

## Current State

- `agent_configs` already exists in the database with the required editable fields.
- [agent_config_repository.py](/home/i933k/code/ai/bmad-opevo/apps/api/app/repositories/agent_config_repository.py) only supports `create_default()` and `get_by_user_id()`.
- [agent.py](/home/i933k/code/ai/bmad-opevo/apps/api/app/routers/agent.py) does not expose a real config contract yet; `GET /api/agent/config` only returns `user_id`.
- Telephony enable/disable behavior already exists elsewhere in the backend through the telephony service/provider boundary.
- `pipeline_mode` is now a valid runtime field with supported values `stt_llm_tts` and `sts`.

## Proposed Approaches

### Approach A: Single endpoint plus service orchestration

Expose `GET /api/agent/config` and `PATCH /api/agent/config`, with a dedicated backend service coordinating partial updates and immediate telephony switching when `is_enabled` changes.

Pros:
- Small public API surface
- Clean backend boundaries
- Fits current MVP scope

Cons:
- Requires adding a small new service layer

### Approach B: Router handles all logic inline

Keep the same endpoints, but let the router load the config, update fields, and call the telephony service directly.

Pros:
- Fewer files
- Fastest to wire initially

Cons:
- Blurs HTTP and business logic
- Harder to test and maintain

### Approach C: Persist first, toggle asynchronously

Expose the same config API but queue enable/disable telephony changes after the DB update.

Pros:
- More resilient to transient provider failures

Cons:
- Breaks the product expectation that the toggle takes effect immediately
- Adds more moving pieces than the MVP needs

## Recommendation

Use Approach A. The backend should keep one public config API while isolating orchestration logic in a dedicated service. This gives a clean place to handle rollback behavior when telephony switching fails.

## Design

### Public API

Add two authenticated endpoints in [agent.py](/home/i933k/code/ai/bmad-opevo/apps/api/app/routers/agent.py):

- `GET /api/agent/config`
- `PATCH /api/agent/config`

`GET` returns the user’s current config from `agent_configs`.

`PATCH` accepts partial updates for all editable fields:

- `agent_name`
- `owner_context`
- `system_prompt`
- `knowledge_base`
- `pipeline_mode`
- `is_enabled`

The patch response should return the full updated config, not only the changed fields, so clients always have a fresh canonical snapshot.

### Data Validation

Add dedicated request and response schemas for agent config.

Validation rules:

- `pipeline_mode` must be one of `stt_llm_tts` or `sts`
- partial patch fields are optional
- empty strings are allowed where the model already allows them
- the response should reflect the actual persisted database state

This keeps runtime mode validation in the API contract instead of accepting arbitrary strings and failing later in the agent worker.

### Backend Units

The implementation should stay split across focused units:

- [agent.py](/home/i933k/code/ai/bmad-opevo/apps/api/app/routers/agent.py)
  - owns the HTTP contract and authentication
  - delegates reads and updates

- [agent_config_repository.py](/home/i933k/code/ai/bmad-opevo/apps/api/app/repositories/agent_config_repository.py)
  - owns DB fetch and partial field assignment
  - does not call telephony providers

- new `agent_config_service.py`
  - owns patch orchestration
  - loads the current config and user phone number
  - applies partial updates
  - triggers telephony switching when `is_enabled` changes
  - commits on success and rolls back on provider failure

- existing telephony boundary
  - [telephony_service.py](/home/i933k/code/ai/bmad-opevo/apps/api/app/services/telephony_service.py)
  - continues to own number enable/disable provider calls

This separation prevents the router from accumulating business logic and keeps telephony side effects out of repository code.

### Toggle Behavior

When `PATCH /api/agent/config` includes `is_enabled` and the value differs from the persisted value:

1. Load the user’s assigned phone number.
2. If no phone number exists, fail the request.
3. If enabling, call the telephony service to switch the number to `app-active`.
4. If disabling, call the telephony service to switch the number to `app-disabled`.
5. Persist the config change only if the telephony call succeeds.

If `is_enabled` is absent, or present but unchanged, no telephony call should happen.

### Error Handling

Expected failure behavior:

- config missing for authenticated user: `404`
- invalid `pipeline_mode`: `422`
- toggle requested without an assigned phone number: `409`
- telephony switch failure: `502` or `503`
- provider failure must roll back the attempted `is_enabled` change

The API should fail clearly rather than persisting a toggle that does not match the actual Telnyx routing state.

### Testing Strategy

Tests should cover:

- `GET /api/agent/config` returns the full persisted config
- `PATCH /api/agent/config` updates prompt and knowledge fields without telephony side effects
- `PATCH` updates `pipeline_mode` to `sts`
- `PATCH` toggles `is_enabled` from false to true and calls telephony enable
- `PATCH` toggles `is_enabled` from true to false and calls telephony disable
- toggle request without a phone number returns `409`
- telephony failure rolls back the DB toggle state
- unauthorized access still returns auth failure

These tests should stay at the API/service level so the config contract and rollback semantics are proven together.

## Acceptance Criteria

This work is complete when all of the following are true:

- authenticated users can fetch their current full agent config
- authenticated users can patch any editable config field
- `pipeline_mode` persists correctly, including `sts`
- `is_enabled` changes immediately trigger Telnyx active/disabled switching
- telephony failures do not leave `is_enabled` in the wrong persisted state
- missing phone number on toggle returns a clear error
- automated tests cover both normal updates and toggle rollback behavior

## Rollout Notes

- This backend API is the MVP config surface; UI can be built on top later without changing the core contract.
- The toggle remains synchronous because the MVP product expectation is immediate activation/deactivation.
- This design keeps the backend as the single source of truth for both the editable config and the routing state transition request.
