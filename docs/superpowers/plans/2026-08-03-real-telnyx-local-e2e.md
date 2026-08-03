# Real Telnyx Local E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the reviewed local stack against the existing real Telnyx number, complete Go live through the durable application path, and verify one real inbound voice call without fabricating application or provider state.

**Architecture:** A private Compose override changes only API and worker provider mode to `telnyx`; committed development defaults remain `fake`. A masked database check and read-only Telnyx lookup gate the service restart, after which the account owner triggers the existing Go-live API through the browser and the normal worker outbox performs the only provider mutation.

**Tech Stack:** Docker Compose, FastAPI, ARQ, PostgreSQL 17, Telnyx Python SDK 2.1.6, LiveKit, pytest-style boolean operational assertions.

## Global Constraints

- Do not change `compose.dev.yaml`, any `.env.example`, application code, or database rows for Issue 24.
- Keep the committed development and example telephony mode `fake`.
- Never print or commit credentials, connection identifiers, provider number identifiers, raw phone numbers, Clerk identities, or provider response bodies.
- Set `TELEPHONY_MODE=telnyx` for both `api` and `worker`; never run them in split provider modes during this E2E session.
- Recreate API and worker from `/home/mo/code/ai/bmad-opevo/.worktrees/clerk-first-local-auth` and preserve PostgreSQL, Redis, MinIO, agent, and web container identities.
- Use the existing `/home/mo/code/ai/bmad-opevo/apps/api/.env` credential file without reading or copying its values.
- Perform a read-only Telnyx identity and disabled-connection preflight before any provider mutation.
- Go live must run through the existing browser/API/outbox path; do not call `enable_number` directly.
- Do not retry Go live while its latest `phone.enable` event is pending or processing.
- Keep `/tmp/presvo-voice-e2e.override.yaml` and the new Telnyx override until the final provider state is explicitly resolved.
- Do not disable Telnyx directly while leaving the application projection active. The current reversible product workflow has no Go-offline action; if the owner does not keep the number live, stop and review account deactivation or a new Go-offline feature as a separate issue.

---

### Task 1: Prepare and validate the real-provider runtime

**Files:**
- Create privately: `/tmp/presvo-telnyx-e2e.override.yaml`
- Create privately: `/tmp/presvo-telnyx-preflight.py`
- Reference: `apps/api/app/providers/telephony/telnyx.py:100-568`
- Reference: `apps/api/app/workers/jobs/outbox_topics.py:288-470`
- Do not modify tracked repository files.

**Interfaces:**
- Consumes: the current PostgreSQL activation/phone projection, existing API credentials, and `/tmp/presvo-voice-e2e.override.yaml`.
- Produces: healthy `bmad-opevo-api-1` and `bmad-opevo-worker-1` containers, both reporting `TELEPHONY_MODE=telnyx`, with every unrelated container preserved.

- [ ] **Step 1: Record the current service identities without environment output**

Run:

```bash
docker ps --format '{{.ID}}|{{.Names}}' --filter label=com.docker.compose.project=bmad-opevo
```

Record only container ID/name pairs in the task report. The later comparison must show that only API and worker changed.

- [ ] **Step 2: Create the masked read-only preflight script**

Create `/tmp/presvo-telnyx-preflight.py` with exactly this behavior:

```python
import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.providers.telephony.telnyx import TelephonyTelnyx


async def preflight() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        SELECT p.e164,
                               p.provider_number_id,
                               p.provider_connection_name,
                               p.is_active
                        FROM phone_numbers AS p
                        JOIN customer_activations AS a ON a.user_id = p.user_id
                        WHERE p.provider = 'telnyx'
                          AND a.verification_status = 'succeeded'
                          AND a.forwarding_verified_at IS NOT NULL
                          AND a.activated_at IS NULL
                          AND a.last_failure_code = 'routing_provider_terminal'
                        """
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    assert len(rows) == 1
    e164, provider_number_id, projection, is_active = rows[0]
    assert isinstance(e164, str) and e164
    assert isinstance(provider_number_id, str) and provider_number_id
    assert projection == "app-disabled"
    assert is_active is False

    provider = TelephonyTelnyx()
    response = await asyncio.to_thread(
        provider.phone_number_resource.list,
        api_key=provider.api_key,
        **{"filter[phone_number]": e164},
    )
    provider_numbers = list(getattr(response, "data", None) or [])
    assert len(provider_numbers) == 1
    remote_id = provider._read_field(provider_numbers[0], "id")
    remote_connection_id = provider._read_field(
        provider_numbers[0], "connection_id"
    )
    assert remote_id == provider_number_id
    assert provider.disabled_connection_id is not None
    assert remote_connection_id == provider.disabled_connection_id

    print("database_target_count=1")
    print("provider_lookup_count=1")
    print("provider_identity_matches=true")
    print("provider_connection_is_disabled=true")


try:
    asyncio.run(preflight())
except Exception:
    print("telnyx_preflight_ok=false")
    sys.exit(1)
else:
    print("telnyx_preflight_ok=true")
```

The exception path deliberately suppresses exception details because SDK errors can contain provider data.

- [ ] **Step 3: Run the read-only preflight inside the configured worker**

Run:

```bash
docker exec -i bmad-opevo-worker-1 /app/.venv/bin/python - < /tmp/presvo-telnyx-preflight.py
```

Expected exact boolean outcome:

```text
database_target_count=1
provider_lookup_count=1
provider_identity_matches=true
provider_connection_is_disabled=true
telnyx_preflight_ok=true
```

Stop without creating or applying the override if the command exits nonzero.

- [ ] **Step 4: Create the private provider-mode override**

Create `/tmp/presvo-telnyx-e2e.override.yaml` with:

```yaml
services:
  api:
    env_file:
      - /home/mo/code/ai/bmad-opevo/apps/api/.env
    environment:
      TELEPHONY_MODE: telnyx
  worker:
    environment:
      TELEPHONY_MODE: telnyx
```

Do not add credentials or connection identifiers to this file.

- [ ] **Step 5: Validate and apply the exact Compose chain**

Run the syntax-only validation:

```bash
docker compose -p bmad-opevo -f compose.dev.yaml -f /tmp/presvo-voice-e2e.override.yaml -f /tmp/presvo-telnyx-e2e.override.yaml config --quiet
```

Expected: exit code 0 and no rendered configuration output.

Then recreate only API and worker:

```bash
docker compose -p bmad-opevo -f compose.dev.yaml -f /tmp/presvo-voice-e2e.override.yaml -f /tmp/presvo-telnyx-e2e.override.yaml up -d --no-deps --force-recreate api worker
```

- [ ] **Step 6: Verify mode, credentials, health, and container preservation**

For both API and worker, print only the mode and boolean presence of
`TELNYX_API_KEY`, `TELNYX_ACTIVE_CONNECTION_ID`, and
`TELNYX_DISABLED_CONNECTION_ID`. Expected values are `telnyx` and `true`.

Run:

```bash
for container in bmad-opevo-api-1 bmad-opevo-worker-1; do
  docker exec "$container" sh -lc 'printf "service=%s\n" "$HOSTNAME"; printf "telephony_mode=%s\n" "$TELEPHONY_MODE"; test -n "$TELNYX_API_KEY" && echo telnyx_api_key_configured=true || echo telnyx_api_key_configured=false; test -n "$TELNYX_ACTIVE_CONNECTION_ID" && echo telnyx_active_connection_configured=true || echo telnyx_active_connection_configured=false; test -n "$TELNYX_DISABLED_CONNECTION_ID" && echo telnyx_disabled_connection_configured=true || echo telnyx_disabled_connection_configured=false'
done
curl --fail --silent --show-error http://127.0.0.1:8000/healthz
docker inspect bmad-opevo-api-1 bmad-opevo-worker-1 --format '{{.Name}}|running={{.State.Running}}|restart_count={{.RestartCount}}'
docker ps --format '{{.ID}}|{{.Names}}' --filter label=com.docker.compose.project=bmad-opevo
```

Expected: API health succeeds; API and worker are running with zero restarts;
their IDs changed; PostgreSQL, Redis, MinIO, agent, and web IDs did not change.

- [ ] **Step 7: Confirm preparation did not mutate activation or routing state**

Run this masked read-only PostgreSQL query:

```bash
docker exec bmad-opevo-postgres-1 psql -U postgres -d ai_call -At -F '|' -c "WITH target AS (SELECT p.is_active, p.provider_connection_name, a.verification_status, a.forwarding_verified_at, a.activated_at, a.last_failure_code FROM phone_numbers AS p JOIN customer_activations AS a ON a.user_id = p.user_id WHERE p.provider = 'telnyx' AND p.provider_number_id !~ '^fake-' AND a.verification_status = 'succeeded' AND a.forwarding_verified_at IS NOT NULL AND a.activated_at IS NULL) SELECT 'target_count', count(*)::text FROM target UNION ALL SELECT 'forwarding_verified', coalesce(bool_and(verification_status = 'succeeded' AND forwarding_verified_at IS NOT NULL)::text, 'false') FROM target UNION ALL SELECT 'activation_completed', coalesce(bool_and(activated_at IS NOT NULL)::text, 'false') FROM target UNION ALL SELECT 'phone_inactive', coalesce(bool_and(is_active = false)::text, 'false') FROM target UNION ALL SELECT 'projection_disabled', coalesce(bool_and(provider_connection_name = 'app-disabled')::text, 'false') FROM target UNION ALL SELECT 'failure_preserved', coalesce(bool_and(last_failure_code = 'routing_provider_terminal')::text, 'false') FROM target;"
```

Expected values are `target_count|1`, `forwarding_verified|true`,
`activation_completed|false`, `phone_inactive|true`,
`projection_disabled|true`, and `failure_preserved|true`.

Task 1 ends here. Do not click Go live or invoke Telnyx modification from the
worker task.

### Task 2: Observe the owner-triggered Go-live operation

**Files:**
- Modify: none.
- Reference: `apps/api/app/services/activation_go_live_service.py:91-330`
- Reference: `apps/api/app/workers/jobs/outbox_delivery.py:91-120`
- Reference: `apps/api/app/workers/jobs/outbox_topics.py:360-470`

**Interfaces:**
- Consumes: the validated Task 1 runtime and one browser-triggered Go-live request from the authenticated account owner.
- Produces: a delivered latest `phone.enable` event, active phone projection, enabled agent, and completed customer activation.

- [ ] **Step 1: Establish the human-action checkpoint**

Tell the owner that both services are ready and ask them to click **Go live
once**. Record the UTC observation start immediately before they click. This is
the only authorized Telnyx mutation in this task.

Run immediately before asking for the click:

```bash
date -u +'%Y-%m-%dT%H:%M:%SZ' | tee /tmp/presvo-go-live-observation-start.utc
```

- [ ] **Step 2: Observe the durable event without retrying it manually**

Inspect worker logs from the recorded UTC time and poll the latest matching
`phone.enable` event with read-only SQL. Do not display payloads, aggregate IDs,
idempotency keys, routing targets, or provider identifiers.

Print only the count of safe outbox batch summaries since the marker:

```bash
docker logs --since "$(tr -d '\n' < /tmp/presvo-go-live-observation-start.utc)" bmad-opevo-worker-1 2>&1 | awk '/outbox batch finished/{count++} END{print "outbox_batch_summary_count=" count+0}'
```

Run the following query repeatedly without modifying the event:

```bash
docker exec bmad-opevo-postgres-1 psql -U postgres -d ai_call -At -F '|' -c "WITH target AS (SELECT p.user_id FROM phone_numbers AS p JOIN customer_activations AS a ON a.user_id = p.user_id WHERE p.provider = 'telnyx' AND p.provider_number_id !~ '^fake-' AND a.verification_status = 'succeeded' AND a.forwarding_verified_at IS NOT NULL), latest AS (SELECT o.topic, o.status, o.last_error_code FROM outbox_events AS o WHERE o.aggregate_type = 'user' AND o.aggregate_id IN (SELECT user_id FROM target) AND o.topic = 'phone.enable' ORDER BY o.created_at DESC LIMIT 1) SELECT 'topic', topic FROM latest UNION ALL SELECT 'status', status FROM latest UNION ALL SELECT 'last_error_code', coalesce(last_error_code, 'none') FROM latest;"
```

Expected latest event fields:

```text
topic=phone.enable
status=delivered
last_error_code=none
```

If status is `pending` or `processing`, continue observing. If it becomes
`failed`, stop and report the safe error code; do not ask the owner to click
again.

- [ ] **Step 3: Verify the durable activation projection**

Run masked read-only SQL for the same account and assert:

```bash
docker exec bmad-opevo-postgres-1 psql -U postgres -d ai_call -At -F '|' -c "WITH target AS (SELECT p.is_active, p.provider_connection_name, a.verification_status, a.forwarding_verified_at, a.go_live_requested_at, a.go_live_approved_at, a.activated_at, a.last_failure_code, c.is_enabled FROM phone_numbers AS p JOIN customer_activations AS a ON a.user_id = p.user_id JOIN agent_configs AS c ON c.user_id = p.user_id WHERE p.provider = 'telnyx' AND p.provider_number_id !~ '^fake-' AND a.verification_status = 'succeeded' AND a.forwarding_verified_at IS NOT NULL) SELECT 'target_count', count(*)::text FROM target UNION ALL SELECT 'forwarding_verified', coalesce(bool_and(verification_status = 'succeeded' AND forwarding_verified_at IS NOT NULL)::text, 'false') FROM target UNION ALL SELECT 'go_live_pending', coalesce(bool_or(go_live_requested_at IS NOT NULL OR go_live_approved_at IS NOT NULL)::text, 'false') FROM target UNION ALL SELECT 'activation_completed', coalesce(bool_and(activated_at IS NOT NULL)::text, 'false') FROM target UNION ALL SELECT 'phone_active', coalesce(bool_and(is_active)::text, 'false') FROM target UNION ALL SELECT 'provider_projection_active', coalesce(bool_and(provider_connection_name = 'app-active')::text, 'false') FROM target UNION ALL SELECT 'agent_enabled', coalesce(bool_and(is_enabled)::text, 'false') FROM target UNION ALL SELECT 'last_failure_code_present', coalesce(bool_or(last_failure_code IS NOT NULL)::text, 'false') FROM target;"
```

```text
target_count=1
forwarding_verified=true
go_live_pending=false
activation_completed=true
phone_active=true
provider_projection_active=true
agent_enabled=true
last_failure_code_present=false
```

Do not infer success solely from the browser. All seven durable assertions must
be true.

### Task 3: Verify one real call and retain a consistent final provider state

**Files:**
- Modify: none.
- Retain until resolved: `/tmp/presvo-voice-e2e.override.yaml`
- Retain until resolved: `/tmp/presvo-telnyx-e2e.override.yaml`

**Interfaces:**
- Consumes: the active Task 2 account and one real inbound call placed by the account owner.
- Produces: database and log evidence for one complete voice path, followed by an explicit keep-live or separate-cleanup decision.

- [ ] **Step 1: Establish the real-call checkpoint**

Record the UTC observation start and ask the owner to place exactly one inbound
call to the assigned number, speak with the receptionist, and hang up normally.

Run immediately before asking for the call:

```bash
date -u +'%Y-%m-%dT%H:%M:%SZ' | tee /tmp/presvo-call-observation-start.utc
```

- [ ] **Step 2: Verify the call path with logs and durable state**

Inspect API, worker, and agent logs only from the recorded UTC start. Confirm:

- the API accepted the LiveKit participant webhook with HTTP 202;
- a `livekit.dispatch` outbox event was delivered;
- the agent accepted the call job rather than a forwarding-verification job;
- transcript append calls returned success after the required spoken exchange;
- call completion returned success;
- call finalization reached a terminal completed state;
- no container restarted and no credential/configuration/provider failure was logged.

Print only safe counts, not matching log lines:

```bash
docker logs --since "$(tr -d '\n' < /tmp/presvo-call-observation-start.utc)" bmad-opevo-api-1 2>&1 | awk '/POST \/webhooks\/livekit HTTP\/1.1" 202/{webhooks++} /POST \/api\/agent\/calls\/.*\/transcript HTTP\/1.1" 2[0-9][0-9]/{transcripts++} /POST \/api\/agent\/calls\/.*\/complete HTTP\/1.1" 2[0-9][0-9]/{completions++} END{print "accepted_livekit_webhook_count=" webhooks+0; print "successful_transcript_request_count=" transcripts+0; print "successful_completion_request_count=" completions+0}'
docker logs --since "$(tr -d '\n' < /tmp/presvo-call-observation-start.utc)" bmad-opevo-agent-1 2>&1 | awk '/job_request_rejected|Traceback|level=(ERROR|CRITICAL)/{errors++} END{print "agent_rejection_or_error_count=" errors+0}'
docker logs --since "$(tr -d '\n' < /tmp/presvo-call-observation-start.utc)" bmad-opevo-worker-1 2>&1 | awk '/provider_(terminal|retryable)|Traceback|level=(ERROR|CRITICAL)/{errors++} END{print "worker_provider_or_runtime_error_count=" errors+0}'
docker inspect bmad-opevo-api-1 bmad-opevo-worker-1 bmad-opevo-agent-1 --format '{{.Name}}|running={{.State.Running}}|restart_count={{.RestartCount}}'
```

Expected: at least one accepted webhook and transcript request, exactly one
successful completion request, zero agent/worker error counts, and zero
container restarts.

Use read-only SQL to assert exactly one new call after the UTC marker and print
only boolean state classifications and counts—never call IDs, room names,
participant identities, phone numbers, transcript content, or summaries.

Pass the exact recorded ISO-8601 UTC marker from the file as the
`observation_start` psql variable and run:

```bash
docker exec bmad-opevo-postgres-1 psql -U postgres -d ai_call -v observation_start="$(tr -d '\n' < /tmp/presvo-call-observation-start.utc)" -At -F '|' -c "WITH target AS (SELECT p.user_id FROM phone_numbers AS p WHERE p.provider = 'telnyx' AND p.provider_number_id !~ '^fake-'), recent AS (SELECT c.id, c.status, c.started_at, c.ended_at, c.failure_code, c.duration_seconds FROM calls AS c WHERE c.user_id IN (SELECT user_id FROM target) AND c.created_at >= :'observation_start'::timestamptz) SELECT 'new_call_count', count(*)::text FROM recent UNION ALL SELECT 'call_connected', coalesce(bool_and(started_at IS NOT NULL)::text, 'false') FROM recent UNION ALL SELECT 'call_ended', coalesce(bool_and(ended_at IS NOT NULL)::text, 'false') FROM recent UNION ALL SELECT 'call_completed', coalesce(bool_and(status = 'completed')::text, 'false') FROM recent UNION ALL SELECT 'failure_code_present', coalesce(bool_or(failure_code IS NOT NULL)::text, 'false') FROM recent UNION ALL SELECT 'duration_recorded', coalesce(bool_and(duration_seconds IS NOT NULL AND duration_seconds >= 0)::text, 'false') FROM recent UNION ALL SELECT 'dispatch_delivered', coalesce(bool_and(EXISTS (SELECT 1 FROM outbox_events AS o WHERE o.aggregate_type = 'call' AND o.aggregate_id = recent.id AND o.topic = 'livekit.dispatch' AND o.status = 'delivered'))::text, 'false') FROM recent UNION ALL SELECT 'transcript_present', coalesce(bool_and(EXISTS (SELECT 1 FROM call_messages AS m WHERE m.call_id = recent.id))::text, 'false') FROM recent;"
```

Expected values are `new_call_count|1`, `call_connected|true`,
`call_ended|true`, `call_completed|true`, `failure_code_present|false`,
`duration_recorded|true`, `dispatch_delivered|true`, and
`transcript_present|true`.

- [ ] **Step 3: Record the final provider-state decision**

Present these two valid outcomes:

1. **Keep live:** retain both overrides, keep API/worker in Telnyx mode, and
   verify the application projection remains active.
2. **Take offline:** stop and open a separate review issue. The current product
   has account deactivation but no reversible Go-offline operation. Do not call
   Telnyx `disable_number` directly and do not remove the override while the
   application still considers the number active.

The E2E is complete only after the owner selects one outcome and its stated
consistency check passes.
