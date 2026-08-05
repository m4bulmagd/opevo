# Production Deployment Decision Record

Status: **historical comparison — Paris recommendation superseded; no target
approved**

Decision owner: `<product/infrastructure owner>`

Security and privacy approver: `<security/privacy owner>`

Last researched: `2026-07-14`

## Decision boundary

This record preserves a 2026-07-14 comparison of three hosting targets. It does
not authorize a cloud account, resource creation, DNS changes, production data
movement, or vendor-specific infrastructure-as-code.

The earlier provisional **AWS Europe (Paris), `eu-west-3`** recommendation is
superseded. The current product preference is to evaluate Ireland as a possible
future hosting region, but no provider, region, or deployment is approved. A
fresh Ireland-capable recommendation remains pending explicit user approval and
requires a cost check, privacy review, and exact provider-resource inventory
before any infrastructure plan or work starts.

## Required production shape

The target deployment must preserve these boundaries regardless of provider:

- Publish API, `worker-lifecycle`, `worker-background`, voice-agent, and web images once, address them by
  immutable `sha256` digest, and promote the same digests between environments.
- Run the API, both worker services, voice agent, and web process as separate
  services. The workers and agent are long-running processes, not request-bound
  functions.
- Preserve N/N-1 contracts across the ordered rollout: the new worker's
  dispatch metadata must be consumable by the previous agent, the new agent
  must work with the previous API, and the previous web must work with the new
  API. Releases without that evidence require a maintenance/call-intake plan.
- Run `alembic upgrade head` as a one-shot release task from the exact API image
  digest being deployed. API process startup must never run a migration.
- Keep PostgreSQL and Redis/Valkey on private endpoints. Require PostgreSQL TLS,
  Redis TLS and authentication, encrypted storage, automated backups, and a
  tested PostgreSQL point-in-time recovery path.
- Keep recordings in a private S3-compatible bucket in the selected EU region.
  Enable bucket versioning, customer-controlled KMS encryption, and block public
  access. Do not activate automatic lifecycle expiry until a customer-facing
  retention policy is approved; any later lifecycle must match that policy.
- Inject secrets at runtime from a managed secret store. Never place secret
  values in images, Compose files, build arguments, deployment logs, tickets,
  or this document.
- Send outbound provider traffic through a stable egress address that can be
  allowlisted. Public inbound traffic terminates TLS at a managed edge/load
  balancer; application and data services remain private.
- Route liveness to `GET /healthz` and traffic eligibility to `GET /readyz`.
  A failed readiness check removes an API replica from traffic without treating
  an optional telemetry exporter failure as application failure.
- Treat production Compose as a portable application contract and staging
  smoke tool, not as the production database, Redis, object-storage, or secret
  platform.

### Worker isolation operating contract

| Service | Queue | Jobs | Health key | Default slots |
| --- | --- | --- | --- | ---: |
| `worker-lifecycle` | `arq:queue` | call finalization; call reconciliation | `presvo:worker:call-lifecycle:health` | 10 |
| `worker-background` | `arq:queue:background` | outbox delivery/reconciliation; verification expiry | `presvo:worker:background:health` | 4 |

PostgreSQL outbox/call state is authoritative. Redis supplies execution and
wakeup only, so a Redis or worker interruption is repaired from durable state:
outbox reconciliation finds orphaned wakeups and call reconciliation finds
orphaned lifecycle attempts after service restoration. This is schedule-bound
recovery, not a zero-delay guarantee. Direct and cron reconciliation retain no
ARQ result; their durable PostgreSQL transition is the result of record.

For the isolation migration, coexistence is deliberately ordered: start
`worker-background` from the new API image, then start `worker-lifecycle` while
the old generic worker still consumes the default queue; roll out the new API
so all new wakeups route explicitly; verify both health keys, both queue-depth
and oldest-due metrics, and both reconciliation jobs; wait for old API replicas
to disappear and the legacy/default backlog to drain; then drain and remove the
generic worker. An unknown-function error from the generic worker during this
bounded overlap is a migration signal, not evidence of durable loss: stop the
transition, retain only the normalized function/attempt signal, and reconcile
from PostgreSQL after compatible routing is restored. It does not justify a
zero-delay recovery claim.

### Voice-agent rollout boundary

Voice-agent replacement requires an application-level pre-drain: register the
new revision, mark the old revision unavailable for new dispatches, wait until
the old revision reports zero active jobs, and only then terminate it. The
worker's drain timeout and container termination grace are fallback bounds, not
a substitute for the active-job gate. The approved provider implementation must
verify its maximum task-stop grace and implement a pre-stop/drain control that
finishes before that platform deadline. Any future target must enforce this; an
ECS/Fargate candidate, if reconsidered, must not assume that orchestrator
termination grace can cover a full-length call.

## Scoring method

Every criterion has equal weight. Scores are intentionally simple and
auditable:

| Score | Meaning |
| --- | --- |
| 5 | Meets the requirement natively on a suitable beta tier with no material workaround. |
| 4 | Meets it natively, but needs a paid option, extra configuration, or a minor operational caveat. |
| 3 | Partially meets it or needs a reasonable external service/workaround. |
| 2 | Has a material limitation or a high-effort workaround. |
| 1 | The capability is absent, unsuitable, or not documented by the provider. |

For **operational effort**, `5` means the least operator work and `1` the most.
For **monthly beta cost**, the bands are `5` at no more than EUR 125, `4` at
EUR 126–180, `3` at EUR 181–300, `2` at EUR 301–500, and `1` above EUR 500 or
quote-only. Cost is only one of ten equally weighted criteria; a low price
cannot compensate for an unmet recovery or security control.

## Historical comparison summary (2026-07-14)

| Criterion | AWS Paris (`eu-west-3`) | Scaleway Paris (`fr-par`) | Render Frankfurt — EU managed application platform |
| --- | --- | --- | --- |
| data residency | **5** — AWS identifies `eu-west-3` as Europe (Paris), France, and the proposed data services stay there. | **5** — Scaleway documents `fr-par` as France (Paris); the proposed data services stay there. | **3** — services and datastores can run in Frankfurt, but static sites use a global CDN and Render is a US-operated platform. |
| managed PostgreSQL PITR | **5** — RDS automated backups support recovery to any second in the retention window. | **1** — official managed-database material documents scheduled backups/snapshots and restore, not continuous WAL-based PITR to an arbitrary time. | **5** — every paid Render Postgres database has continuous backups and a 3-day or 7-day PITR window. |
| managed Redis TLS | **5** — ElastiCache supports TLS for client and node traffic in a VPC. | **5** — Managed Redis can generate a TLS certificate and attach to a Private Network. | **3** — the managed Redis-compatible service supports `rediss://` externally, but the documented internal private URL is `redis://` and unauthenticated by default. |
| private networking | **5** — VPC-native application, database, cache, endpoints, and routing controls. | **5** — VPC/Private Networks integrate with Kapsule and both managed database products. | **5** — each region has an automatic private network and environment boundaries can block cross-environment private traffic. |
| secret management | **5** — Secrets Manager provides encryption, IAM access, versioning, and rotation integration. | **5** — Secret Manager uses KMS encryption, IAM, and versioning and integrates with Kubernetes. | **3** — environment variables/groups and secret files are managed, but the product documentation does not expose a customer-managed KMS/rotation control comparable to the other two. |
| object lifecycle/KMS | **5** — S3 supports lifecycle transitions/expiry and SSE-KMS with customer-managed keys. | **5** — Object Storage supports lifecycle rules and SSE-KMS through Scaleway Key Manager. | **1** — no native managed object store with lifecycle/KMS; Presvo would need a second provider. |
| worker support | **5** — ECS services are designed to maintain long-running stateless processes. | **5** — managed Kapsule runs arbitrary container deployments and supports the private service network. | **5** — background workers are a first-class service and use the same compute plans as web/private services. |
| static egress IP | **5** — private tasks can egress through NAT with an Elastic IP. | **5** — a Public Gateway performs outbound dynamic NAT through a detachable flexible public IP. | **5** — paid dedicated outbound IP sets provide reserved static addresses; shared regional CIDRs are also documented. |
| operational effort | **2** — widest control surface: ECS, load balancing, IAM, VPC routes/endpoints, NAT, RDS, cache, S3, KMS, logs, and alarms. | **3** — managed control plane and data services help, but the team still owns Kubernetes workloads, nodes, networking, and upgrades. | **5** — application platform handles builds/deploys, service discovery, private networking, TLS, and managed data services. |
| monthly beta cost | **3** — estimated **EUR 210–300/month** before VAT and variable traffic. | **4** — estimated **EUR 140–170/month** before VAT. | **3** — estimated **EUR 190–210/month** with Pro workspace and dedicated egress; approximately EUR 100–115 without dedicated egress. |
| **Total / 50** | **45** | **43** | **38** |

The infrastructure geography scores above are not legal opinions and do not
prove EU operational sovereignty. Before production data is placed with any
provider, the privacy owner must review the DPA, subprocessors, support-access
model, international-transfer mechanism, deletion terms, and breach process.

## Evidence behind the scores

### AWS Paris

AWS lists `eu-west-3` as the Europe (Paris) region with three Availability
Zones. RDS for PostgreSQL supports automated backups and point-in-time restore;
ElastiCache supports TLS client and server connections in a VPC. ECS services
maintain long-running task counts and replace failed tasks. These cover the
four application processes without changing their runtime model.

Primary sources:

- [AWS Regions: Europe (Paris) is `eu-west-3`](https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html)
- [RDS point-in-time recovery and retention](https://docs.aws.amazon.com/AmazonRDS/latest/gettingstartedguide/managing-backup-restore.html)
- [ElastiCache in-transit TLS](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/in-transit-encryption.html)
- [ECS services for long-running applications](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_services.html)
- [Secrets Manager lifecycle and rotation](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html)
- [S3 server-side encryption and SSE-KMS](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingEncryption.html)
- [S3 lifecycle management](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html)
- [VPC Elastic IPs and private networking](https://docs.aws.amazon.com/vpc/latest/userguide/what-is-amazon-vpc.html)

The main weakness is operator burden. A stable outbound IP normally adds a NAT
gateway, which is charged per provisioned hour and per processed GB. A single
gateway is a beta cost optimization and an Availability Zone failure point;
the approved infrastructure plan must explicitly choose between that risk and
one gateway per active zone.

### Scaleway Paris

Scaleway offers strong Paris residency, VPC integration, managed Redis TLS,
Secret Manager, and S3-compatible Object Storage with lifecycle rules and
SSE-KMS. Its managed PostgreSQL documentation describes scheduled logical
backups or block snapshots (daily with seven-day retention by default) and
restore operations. It does not document continuous, arbitrary-time PITR.
That missing recovery contract is why Scaleway is not the recommendation even
though its score and estimated cost are attractive.

Primary sources:

- [Managed Database concepts and `fr-par`](https://www.scaleway.com/en/docs/managed-databases-for-postgresql-and-mysql/concepts/)
- [Managed PostgreSQL backup and restore behavior](https://www.scaleway.com/en/docs/managed-databases-for-postgresql-and-mysql/how-to/manage-backups/)
- [Managed Redis TLS certificates](https://www.scaleway.com/en/developers/api/managed-database-redis/tls-certificates)
- [Managed Redis Private Networks](https://www.scaleway.com/en/docs/managed-databases-for-redis/faq)
- [Secret Manager KMS and IAM controls](https://www.scaleway.com/en/secret-manager/)
- [Object Storage lifecycle rules](https://www.scaleway.com/en/docs/object-storage/api-cli/lifecycle-rules-api/)
- [Object Storage SSE-KMS](https://www.scaleway.com/en/docs/object-storage/how-to/enable-sse-kms/)
- [Kapsule and container pricing](https://www.scaleway.com/en/pricing/containers/)
- [Public Gateway flexible IP and NAT](https://www.scaleway.com/en/docs/public-gateways/concepts/)

If Scaleway publishes and contractually supports PostgreSQL PITR before the
hosting decision is approved, re-score it. Do not equate a daily snapshot with
PITR or silently build a self-managed WAL archive into the beta architecture.

### Render Frankfurt managed application platform

Render is the lowest-effort application platform in this comparison. It has a
Frankfurt region, regional private networking, long-running background workers,
paid PostgreSQL PITR, managed Redis-compatible storage, managed environment
secrets, and optional dedicated outbound IPs. Its two material gaps for Presvo
are the lack of native object storage with lifecycle/customer KMS and the fact
that the documented internal Key Value connection is not TLS by default.

Primary sources:

- [Render regions and regional private networks](https://render.com/docs/regions)
- [Render Postgres continuous backups and PITR](https://render.com/docs/postgresql-backups)
- [Render Key Value connection security](https://render.com/docs/key-value)
- [Render environment variables and secret files](https://render.com/docs/configure-environment-variables)
- [Render outbound and dedicated IP behavior](https://render.com/docs/outbound-ip-addresses)
- [Render service, worker, database, cache, and dedicated-IP pricing](https://render.com/pricing)

Render remains a reasonable speed-first beta alternative if the user accepts a
second EU object-storage provider and an authenticated external `rediss://`
connection or another TLS-capable cache provider. Those workarounds lower
architectural cohesion and must be reviewed before selection.

## Cost assumptions

All figures are planning estimates captured on `2026-07-14`, exclude VAT,
support plans, telephony/AI providers, CI, domain registration, unexpected
egress, and growth. For USD prices, the table uses a deliberately rounded
budgeting conversion of `USD 1 = EUR 0.87`; it is an assumption, not a quoted
exchange rate. Recalculate in each provider's calculator on the approval date.

### AWS estimate: EUR 210–300/month

- Four always-on Fargate tasks totaling about `2.25 vCPU` and `4.5 GB` RAM.
- One application load balancer and low beta request volume.
- One small Single-AZ burstable RDS PostgreSQL instance, 20 GB storage,
  automated backups, and PITR. Multi-AZ is excluded from this beta estimate.
- One small TLS-enabled ElastiCache/Valkey deployment.
- One NAT gateway and Elastic IP, 100 GB S3, one KMS key, application secrets,
  registry storage, and modest logs/metrics.

AWS bills Fargate by requested vCPU/memory and second, RDS by instance/storage,
ElastiCache by its selected capacity, and NAT by gateway hours and processed
traffic. See the official [Fargate](https://aws.amazon.com/fargate/pricing/),
[RDS PostgreSQL](https://aws.amazon.com/rds/postgresql/pricing/),
[ElastiCache](https://aws.amazon.com/elasticache/pricing/), and
[VPC/NAT](https://aws.amazon.com/vpc/pricing/) pricing pages.

### Scaleway estimate: EUR 140–170/month

- Free mutualized Kapsule control plane with two `DEV1-M` nodes.
- One `LB-S`, one `VPC-GW-S`, and one flexible IPv4 address.
- One `DB-DEV-S` PostgreSQL primary with the Multi-AZ option, 20 GB Block
  Storage, scheduled backups, and snapshots.
- A two-node `RED1-MICRO` managed Redis deployment.
- 100 GB Object Storage plus a small number of Secret Manager and Key Manager
  versions/operations.

The official pricing inputs are on the Scaleway
[virtual instances](https://www.scaleway.com/en/pricing/virtual-instances/),
[managed databases](https://www.scaleway.com/en/pricing/managed-databases/),
[network](https://www.scaleway.com/en/pricing/network/), and
[security/account](https://www.scaleway.com/en/pricing/security-and-account/)
pages. This estimate does not cure the missing documented PostgreSQL PITR.

### Render estimate: EUR 190–210/month with static egress

- Pro workspace at USD 25/month.
- Standard API and voice-agent instances, Starter web and post-call worker.
- Basic 1 GB paid PostgreSQL, Starter Key Value, and about EUR 5/month for an
  external EU object store.
- One dedicated outbound IP set at USD 100/month. Without that set, services
  use shared regional CIDRs and the estimate falls to roughly EUR 100–115.

The current plan and component prices are on the official
[Render pricing page](https://render.com/pricing). Memory must be load-tested,
especially for the voice agent and its preloaded turn-detection assets.

## Superseded recommendation and new approval gate

The historical comparison favored **AWS Paris (`eu-west-3`)** because it met the
listed technical capabilities without weakening PostgreSQL recovery or adding
a second storage provider. That recommendation is no longer active and must not
be treated as an approved deployment decision.

Before any new recommendation or approval, compare Ireland-capable candidates
against the current product, privacy, recovery, support, and cost requirements,
then record all of the following:

- `<user>` explicitly accepts or rejects the proposed provider and Irish region;
- `<privacy owner>` accepts the DPA, data-transfer, support-access, and
  subprocessor position;
- `<infrastructure owner>` validates a provider-calculator estimate and monthly
  budget cap;
- `<application owner>` confirms the minimum API, worker, agent, and web
  resources after load testing;
- `<data owner>` confirms database retention/restore targets, object-storage
  retention behavior, and encryption policy;
- `<security owner>` confirms private networking, identity/access, secret,
  egress, logging, and audit controls.

Only after that sign-off may a separate plan specify provider resources. No
vendor-specific infrastructure-as-code belongs in this task.
