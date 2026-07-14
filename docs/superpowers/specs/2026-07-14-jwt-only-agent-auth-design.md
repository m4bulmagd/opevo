# JWT-Only Agent Authentication Design

## Goal

Use one authentication mechanism for voice-agent calls: short-lived,
call-scoped JWTs signed with `AGENT_DISPATCH_JWT_SECRET`. Remove the legacy
static `AGENT_INTERNAL_API_TOKEN` fallback from runtime configuration, code,
tests, examples, and operational documentation.

## Scope

This change covers the API endpoints used by the voice agent to append call
transcripts and complete calls, the agent API client that invokes those
endpoints, runtime settings, environment examples, tests, and documentation.
It does not change LiveKit SDK APIs, SIP routing, dispatch creation, JWT claims,
JWT lifetime, or the existing LiveKit project credentials.

The user's real `apps/api/.env` already contains
`AGENT_DISPATCH_JWT_SECRET`. Secret values remain untracked and must not be
copied into code, examples, tests, logs, or documentation.

## Alternatives Considered

1. Keep the fallback code but unset `AGENT_INTERNAL_API_TOKEN` in deployed
   environments. This avoids immediate code changes but preserves a dormant
   authentication bypass and leaves two security models to maintain.
2. Keep the static token only for local recovery. This makes development less
   representative of production and can conceal missing dispatch metadata.
3. Remove the static-token mechanism completely. This gives development and
   production the same call-scoped authorization behavior and is the selected
   approach.

## Authentication Contract

For each eligible inbound call, the worker creates a signed JWT containing the
call ID, user ID, agent-configuration ID, issue time, and expiration time. The
token is placed in trusted dispatch metadata. The agent forwards that token in
the `x-agent-token` header when appending transcript segments and completing
the call.

The API verifies the signature, expiration, call ID, user ID, and
agent-configuration ownership before accepting either operation. Missing,
malformed, expired, cross-call, or ownership-mismatched tokens return the
existing unauthorized response. There is no environment-specific static-token
exception.

Only the API and worker receive `AGENT_DISPATCH_JWT_SECRET` through the API
environment. The agent receives generated call tokens but never receives the
signing secret.

## Component Changes

### API

- Remove `agent_internal_api_token` from `Settings`.
- Remove the development-only HMAC comparison from `require_agent_auth`.
- Preserve JWT verification and database ownership checks for transcript and
  completion endpoints.
- Remove the static token from shared test environment setup and redaction
  fixtures while preserving dispatch-secret redaction coverage.

### Agent

- Remove `agent_internal_api_token` from `AgentSettings`.
- Remove `agent_token` construction state and the development fallback in
  `AgentApiClient.complete_call`.
- Require a non-empty dispatch token for completion in every environment,
  matching the existing transcript behavior.
- Keep sending the call-scoped token through `x-agent-token`.

### Configuration and Documentation

- Remove `AGENT_INTERNAL_API_TOKEN` from API and agent environment examples.
- Remove it from local/staging runbooks and credential-rotation inventory.
- Update integration documentation to state that transcript and completion
  always require call-scoped JWT authentication.
- Remove the variable from the user's local API and agent environment files if
  present, without displaying or modifying any other secret.

## Error Handling

- Agent completion without dispatch metadata fails locally with
  `ValueError("Dispatch token is required")` before making an HTTP request.
- Transcript append without a dispatch token continues to fail locally with
  `TranscriptAppendPermanentError("dispatch token is required")`.
- API requests without a valid call-scoped JWT continue to return HTTP 401
  with `Invalid agent token`.
- Missing or unsafe `AGENT_DISPATCH_JWT_SECRET` continues to prevent dispatch
  token creation and remains a runtime configuration error outside
  development. The local development stack must also contain a safe secret to
  dispatch calls successfully.

## Testing Strategy

Implementation follows red-green-refactor.

1. Change agent-client tests so completion without dispatch metadata is
   rejected even in development; run the test before implementation and
   observe the current static fallback make it fail.
2. Change API endpoint tests so a former development static token receives
   HTTP 401; run before implementation and observe the current fallback accept
   it.
3. Remove production fallback code and obsolete fallback-only tests and
   fixtures after the new JWT-only expectations are red.
4. Run focused API and agent authentication suites, then each application's
   full test, lint, and type-check suites.
5. Recreate API, worker, and agent containers so environment removals and the
   new dispatch secret are loaded.
6. Verify non-secret runtime state: the API and worker see a safe dispatch
   secret, the agent has no signing secret, the worker is registered, and a
   fresh dispatch no longer ends with `dispatch_configuration`.

## Success Criteria

- No runtime code or tracked configuration references
  `AGENT_INTERNAL_API_TOKEN` or `agent_internal_api_token`.
- Transcript append and call completion accept only a valid call-scoped JWT in
  every environment.
- The agent never receives `AGENT_DISPATCH_JWT_SECRET`.
- JWT ownership, expiry, malformed-token, and missing-token tests pass.
- API and agent full verification suites pass.
- Existing unrelated worktree changes remain untouched.
