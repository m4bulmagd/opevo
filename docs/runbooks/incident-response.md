# Incident Response Runbook

This runbook covers the first-response signals for the Opevo API,
`worker-lifecycle`, `worker-background`, and
voice agent. The production telemetry interface is OpenTelemetry Protocol
(OTLP) push export; there is intentionally no public `/metrics` endpoint.

## Health semantics

- `GET /healthz` is process liveness. It does not contact PostgreSQL, Redis,
  telemetry, or providers. A liveness failure means the process is wedged and
  should be restarted. The exact `/healthz` path bypasses HTTP span and metric
  middleware as well as telemetry lookup.
- `GET /readyz` checks PostgreSQL and Redis concurrently under one two-second
  deadline. A readiness failure removes the instance from traffic; it does not
  by itself justify restarting every replica.
- Telemetry is optional and fail-open. An exporter or collector failure alone
  must not restart a process, remove an instance from traffic, reject a call,
  or change provider, webhook, or worker behavior.
- Worker health is separate by service: inspect
  `opevo:worker:call-lifecycle:health` for `worker-lifecycle` and
  `opevo:worker:background:health` for `worker-background`. The lifecycle
  queue is `arq:queue`; the background queue is `arq:queue:background`.

## Beta alert thresholds

| Alert | Threshold | First checks |
| --- | --- | --- |
| Readiness unavailable | `/readyz` fails for 2 consecutive minutes | Identify whether `database` or `redis` is unavailable; check dependency health and recent deployment/configuration changes. |
| Webhook failures | `error` outcomes exceed 2% for 5 minutes | Break down by the fixed Clerk, Stripe, and LiveKit provider label; check provider status and signature-secret rotation history. |
| Outbox terminal failure | Any `opevo.outbox.terminal_failures` increment | Inspect the fixed topic and normalized error class; confirm the durable row is `failed` and determine whether a safe replay is supported. |
| Calls beyond lifecycle deadlines | More than 5 stale calls | Break down `opevo.calls.stale` by state and compare with reconciliation outcomes. |
| Queue oldest-job age | Durable outbox oldest-unfinished age exceeds 2 minutes | Inspect pending versus processing depth, worker availability, and PostgreSQL locking. This alert is based on the durable outbox age, not ARQ start delay. |
| Worker queue depth or oldest due | A class-specific depth or oldest-due signal is unexpectedly nonzero | Check the matching worker health key and `opevo.worker.queue.depth{queue_class}` plus `opevo.worker.queue.oldest_due.age{queue_class}` before changing capacity. |
| Provider errors | Error rate exceeds 10% for 5 minutes | Break down by fixed provider and operation; compare timeout, rate-limit, unavailable, authentication, validation, conflict, and unknown classes. |
| Recording failures | `livekit` recording-operation errors exceed 5% of matching recording operations for 10 minutes | Break down `opevo.provider.errors` and `opevo.provider.request.duration` by the fixed LiveKit recording operations; check egress, object-storage bucket availability, credentials, lifecycle policy, and recent configuration changes. |
| Backup freshness | No successful backup within 24 hours | Confirm the backup scheduler and destination, restore the backup pipeline, and perform a restore verification after recovery. |

## Signal interpretation

- `opevo.http.server.request.duration` uses HTTP method, matched route template,
  and status class. It never contains raw paths or query strings.
- `opevo.webhook.requests` and `opevo.webhook.duration` describe semantic
  `accepted`, `duplicate`, `rejected`, or `error` outcomes. HTTP 202 alone does
  not imply acceptance.
- `opevo.outbox.events` and `opevo.outbox.oldest_unfinished.age` come from a
  once-per-minute durable repository snapshot. `opevo.worker.queue.delay` is a
  separate, unlabeled ARQ execution-start signal.
- `opevo.worker.queue.depth{queue_class}` and
  `opevo.worker.queue.oldest_due.age{queue_class}` are separate bounded queue
  snapshots for `call_lifecycle` and `background`. They are diagnostic metrics,
  not a production SLO or an alerting-certification claim.
- `opevo.calls.current`, `opevo.calls.stale`, and
  `opevo.call_reconciliation.outcomes` use the existing call lifecycle policy.
- `opevo.provider.request.duration` and `opevo.provider.errors` count one
  logical operation at the Telnyx, S3, LiveKit, Gemini, or Stripe boundary.
  LiveKit `EGRESS_FAILED`, `EGRESS_ABORTED`, and `EGRESS_LIMIT_REACHED` terminal
  states are errors; only `EGRESS_COMPLETE` is a successful terminal recording.
  A missing egress on either the initial lookup or the post-stop recheck is an
  uncertain provider result, not proof of a successful recording, and retries
  through the durable outbox path.
  A definite failed terminal recording is marked terminal on its first durable
  outbox attempt, while an uncertain transport/provider result remains retryable.
  Provider adapters expose only a fixed category, retryability flag, and an
  allowlisted timeout, rate-limit, unavailable, authentication, validation,
  conflict, or unknown error class; they never derive a class from exception
  messages.

HTTP server spans accept only W3C `traceparent`; caller-supplied `tracestate`,
baggage, headers, paths, and queries are ignored. Worker and agent spans are
independently rooted and correlate through `opevo.call.id` only when a UUID
call reference is directly available. Outbox provider spans inherit that value
only after the fixed call topic, expected aggregate type, payload UUID, and
aggregate UUID agree. The current durable outbox schema does not carry W3C
parent context; do not add trace data to strict reference-only job payloads as
an incident workaround.

Span kinds identify the boundary without capturing content: HTTP request spans
are servers, provider and readiness dependency spans are clients, worker job
spans are consumers, and lifecycle/internal operations remain internal.

A sanitized provider request ID may be attached to a span only when the provider
SDK exposes it as a direct, bounded field. The current adapters do not expose
this consistently, so it is not a guaranteed signal, and exception text must
never be parsed to manufacture one.

The LiveKit Agents runtime can place caller text on built-in spans. Opevo uses
its own private tracer and does not replace or augment LiveKit's dynamic tracer
provider. LiveKit SDK transcript, log, trace, and audio recording are explicitly
disabled; the existing room-composite egress path remains the product's call
recording owner.

## First-response procedure

1. Confirm the alert window and whether one instance, one provider, or all
   traffic is affected.
2. Check `/healthz` and `/readyz` separately. Use only the fixed dependency
   outcome; the endpoint intentionally does not expose hosts, ports, or errors.
3. For queue symptoms, check each worker health key and both class-specific
   depth/oldest-due metrics. Do not treat Redis contents as authoritative:
   PostgreSQL outbox/call state determines whether reconciliation must recover
   an orphaned wakeup or lifecycle attempt after service restoration.
4. Compare the alert with deployment, secret rotation, dependency maintenance,
   and provider-status timelines.
5. Use fixed metric dimensions and trace correlation. Retrieve customer data
   only through authorized product/admin paths, never by adding it to telemetry.
6. Mitigate the narrow failure: remove an unready instance, pause a failing
   provider-dependent workflow, or roll back the responsible deployment.
7. Verify recovery for at least one complete alert window and record the
   timeline, scope, mitigation, and follow-up owner.

## Safe diagnostic boundary

Telemetry and incident notes must not contain credentials, authorization or
cookie headers, DSNs, SQL values, Redis keys or values, webhook bodies, provider
payloads, phone numbers, email addresses, prompts, transcripts, message text,
recording locations, raw event/job/request IDs, or exception messages. Use only
validated call references on traces, fixed provider/topic/state/operation
labels, normalized outcomes, and normalized error classes.

Collector HTTP response bodies and retry reasons are untrusted. Opevo replaces
the four stock OTLP HTTP exporter diagnostics that interpolate those fields with
a fixed message. It emits a separate fixed `observability_export_failed` event
only when the export call ultimately raises or returns failure; a transient
retry diagnostic followed by successful export does not emit that failure event.

If those safe signals are insufficient, escalate to an engineer with authorized
database/provider-console access. Do not weaken redaction or enable content
export to diagnose an incident.

## Escalation and rollback

- Escalate dependency-wide readiness failures and sustained multi-provider
  errors immediately to the on-call infrastructure owner.
- Escalate any terminal outbox failure, recording-loss pattern, or backup
  freshness breach to the application owner and data owner.
- Roll back when the incident correlates with a recent deploy and rollback is
  safer than forward repair. Do not replay terminal work until idempotency and
  current business state have been checked.
- After rollback, verify readiness, webhook acceptance, outbox drain, call
  reconciliation, provider success, recording upload, and backup freshness.
