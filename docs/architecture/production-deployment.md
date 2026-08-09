# Production Deployment Decision

Status: **not selected**

No hosting provider or region is approved. Production deployment requires explicit owner approval after the candidate is evaluated against the constraints below. Historical provider comparisons remain available in Git history and are not current recommendations.

## Required production capabilities

An approved target must provide:

- documented EU data residency for application, PostgreSQL, Redis, object storage, logs, traces, metrics, and backups;
- managed PostgreSQL PITR with a demonstrated restore path;
- Redis TLS, authentication, persistence appropriate to ARQ, and recovery behavior compatible with PostgreSQL reconciliation;
- private networking between application processes and stateful services;
- managed secret management with rotation and audit support;
- static egress IP where provider allowlists require it;
- private S3-compatible object storage with lifecycle controls and signed access;
- independently deployable API, web, voice-agent, `worker-lifecycle`, and `worker-background` processes;
- health probes, OpenTelemetry export, alert routing, immutable image rollout, and rollback support;
- an itemized monthly beta cost reviewed against the expected controlled-beta load.

## Runtime and rollout constraints

PostgreSQL remains the durable authority. Redis queue loss or delayed wakeup must be recoverable through reconciliation. The current worker topology is defined in [`runtime-contract.md`](runtime-contract.md).

Releases follow [`deploy.md`](../runbooks/deploy.md) and [`rollback.md`](../runbooks/rollback.md). Voice-agent rollout requires pre-drain: stop accepting new dispatches, allow active jobs to reach zero within the termination grace, and prove adjacent API/agent contract compatibility before removing the previous revision.

Schema changes must remain backward-compatible across the documented rolling window. An irreversible migration requires a reviewed forward-fix or separately authorized restore decision; it is not automatically downgraded.

## Approval evidence

Before selecting a target, record in the approving change:

- provider, region, and service topology;
- evidence for every required capability above;
- data-processing and subprocessor review;
- restore, rollback, queue-recovery, and provider-egress results;
- measured monthly beta cost and scaling assumptions;
- the approving product/infrastructure and security/privacy owners.

Until that evidence receives explicit owner approval, the project remains locally verified and not production-certified.
