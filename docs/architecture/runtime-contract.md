# Backend Runtime Contract

**Status:** Current

This document defines Opevo's durable backend authority, worker ownership, recovery behavior, and bounded local evidence. Product capability status remains canonical in [`PROJECT_STATUS.md`](../PROJECT_STATUS.md); endpoint payloads remain in [`integration-endpoints.md`](integration-endpoints.md).

## Authority boundaries

- PostgreSQL owns business state, call state, usage, and outbox intent.
- Redis carries ARQ execution and wakeups. Optional realtime events are non-authoritative observations.
- `apps/api` owns authenticated control-plane requests, provider webhooks, durable transactions, and worker composition.
- `apps/agent` owns the LiveKit media session and reports ordered transcript and completion facts through authenticated API contracts.
- Provider calls occur through adapters and never redefine committed PostgreSQL state.

PostgreSQL outbox/call state is authoritative. A missed Redis wakeup is recovered from committed PostgreSQL state by reconciliation.

## Worker ownership

| Service | Queue | Jobs | Health key | Default slots |
|---|---|---|---|---:|
| `worker-lifecycle` | `arq:queue` | call finalization; call reconciliation | `opevo:worker:call-lifecycle:health` | 10 |
| `worker-background` | `arq:queue:background` | outbox delivery/reconciliation; verification expiry | `opevo:worker:background:health` | 4 |

The services are separate scaling and failure domains. The lifecycle worker owns latency-sensitive call completion. The background worker owns provider and reconciliation work that must not consume lifecycle capacity.

## Durable flow

1. FastAPI commits domain state and reference-only outbox intent in PostgreSQL.
2. Redis wakes the owning worker queue after commit.
3. A worker reads a fresh PostgreSQL snapshot, performs bounded work, and commits the result.
4. Reconciliation finds committed work whose Redis wakeup was missed or whose provider outcome remains incomplete.

Provider identifiers and customer content do not belong in queue payloads when a private operation identifier is sufficient.

## Realtime boundary

Realtime remains deferred. The optional observer is disabled by default and must not be presented as an authoritative customer feature. Any future enablement must resynchronize from PostgreSQL, preserve tenant isolation, and apply explicit connection, delivery, and backpressure limits.

## Evidence boundary

Worker isolation is implemented as decision 4A + 4B. controlled ten-call local/CI evidence held four background slots while ten lifecycle probes started simultaneously, with lifecycle queue-delay p95 `<= 2 seconds`.

This is bounded local/CI evidence, not a cloud-capacity claim. Issue 16A remains open for representative load, saturation, monitoring, alert routing, and recovery drills.

## Operations

- Use [`deploy.md`](../runbooks/deploy.md) for rollout and queue-transition order.
- Use [`rollback.md`](../runbooks/rollback.md) for reverse transitions and database forward-fix policy.
- Use [`incident-response.md`](../runbooks/incident-response.md) for health, queue, and reconciliation signals.
- Use [`staging-smoke-runbook.md`](staging-smoke-runbook.md) for real-provider staging verification.
