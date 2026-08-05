# Presvo (WIP)

> An open-source, France-first AI voice assistant for handling inbound business calls.

**Status:** Working MVP in active development. Locally verified, but not yet production-certified.

![Presvo landing page](docs/dashboard.png)

## What Presvo does

Presvo gives professionals and small businesses a configurable AI receptionist and a dedicated French phone number. It can:

- answer conditionally forwarded calls with business-specific context;
- manage subscriptions, number setup, forwarding verification, and live-call monitoring;
- preserve transcripts, summaries, private recordings, and minute usage;
- provide a responsive dashboard for reviewing calls and configuring the assistant.

## Project progress

### Done

- Public landing page, authentication shell, and tenant-isolated dashboard
- Clerk-authenticated local activation journey with fake billing and telephony providers
- Stripe billing and queued Telnyx number-provisioning integrations
- LiveKit voice-agent runtime with configurable speech and language providers
- Durable call lifecycle, transcripts, summaries, private recordings, and usage accounting
- Account deactivation/reactivation and owner-controlled terminal-call removal

### In progress

- Fresh real-provider certification across Clerk, Stripe, Telnyx, and LiveKit
- Cloud deployment, monitoring, backup restoration, and recovery evidence
- Call tags and notes, accessibility conformance, and frontend performance gates

### Planned

- French localization plus approved legal, privacy, retention, and support pages
- Account export, permanent deletion, and automatic retention
- Conversation-flow builder, tools, human transfer, and reusable subflows
- Calendar, CRM, and appointment-booking integrations
- Live monitoring and intervention plus production push delivery
- Additional countries and subscription plans after the France-first launch

See [Project Status and Roadmap](docs/PROJECT_STATUS.md) for the complete, evidence-based feature matrix and production gates.

## Architecture

```mermaid
flowchart TB
    subgraph Setup["Setup and review"]
        Owner[Business owner] --> Web[Next.js dashboard]
        Web <--> Clerk[Clerk authentication]
    end

    subgraph Calls["Inbound call"]
        Caller --> Telnyx --> LiveKit --> Agent[LiveKit voice agent]
        Agent <--> AI[Speech and language providers]
    end

    Web <--> API[FastAPI control plane]
    Clerk -->|signed webhooks| API
    Agent -->|configuration, transcript, completion| API
    LiveKit -->|signed webhooks| API

    subgraph Platform["Durable platform"]
        API <--> DB[(PostgreSQL)]
        API --> Redis[(Redis / ARQ)]
        Redis --> Lifecycle[worker-lifecycle]
        Redis --> Background[worker-background]
        Lifecycle <--> DB
        Background <--> DB
        API --> Storage[(Private object storage)]
        Background --> Storage
    end

    API <--> Stripe[Stripe]
    Background <--> Telnyx
    Background <--> LiveKit
    LiveKit -->|recordings| Storage
```

The owner journey configures and reviews the service through the dashboard. The
call journey routes an inbound phone call through Telnyx and LiveKit to the
voice agent, which persists durable results through the API for later review.

### Worker ownership

`worker-lifecycle` consumes `arq:queue` for call finalization and call
reconciliation (default 10 slots). `worker-background` consumes
`arq:queue:background` for outbox delivery/reconciliation and verification
expiry (default 4 slots). PostgreSQL outbox/call state is authoritative; Redis
is only the execution and wakeup path. Operational rollout, recovery, and the
bounded local/CI isolation evidence are recorded in the
[deployment runbook](docs/runbooks/deploy.md).
That evidence holds four background slots while ten lifecycle probes start
simultaneously, with local/CI queue-delay p95 `<= 2 seconds`; it is not
production certification.

## Run locally

### Prerequisites

- Docker with Docker Compose
- Node.js 22 only when running browser tests from the host

Configure Clerk credentials in `apps/web/.env` and the API verifier credentials
in `apps/api/.env` (see the [staging smoke runbook](docs/architecture/staging-smoke-runbook.md)).
Then start the standard Clerk-authenticated development stack from the repository
root:

```bash
docker compose -f compose.dev.yaml up --build postgres redis minio minio-init migrate api worker-lifecycle worker-background web
```

Open [http://127.0.0.1:3000/activate](http://127.0.0.1:3000/activate).
This uses Clerk for identity. The fake billing, carrier, telephony, and
verification providers are separate from authentication; they do not create a
synthetic user. It does not start the LiveKit voice agent.

For a manual, provider-free local-auth test only, explicitly opt in to the
development token:

```bash
AUTH_MODE=local \
LOCAL_AUTH_TOKEN=replace-with-a-development-only-token \
docker compose -f compose.dev.yaml up --build postgres redis minio minio-init migrate api worker-lifecycle worker-background web
```

For disposable CI-equivalent proof, run the isolated provider-free browser
suite. The script explicitly selects local authentication and owns isolation,
credentials, ports, and cleanup:

```bash
npm exec --prefix apps/web -- playwright install chromium
bash scripts/run-local-e2e.sh
```

For real-provider configuration, follow the
[staging smoke runbook](docs/architecture/staging-smoke-runbook.md).

## Technology

| Area | Technology |
|---|---|
| Web | Next.js 16, React 19, TypeScript, Tailwind CSS 4, shadcn/ui |
| API | Python 3.13, FastAPI, SQLAlchemy, Alembic, Pydantic |
| Voice | LiveKit Agents, Speechmatics/Deepgram, Gemini, Speechmatics/ElevenLabs |
| Providers | Clerk, Stripe, Telnyx |
| Data and jobs | PostgreSQL, Redis, ARQ, S3-compatible object storage |
| Delivery | Docker Compose, GitHub Actions, OpenTelemetry, gitleaks, Trivy |

## Documentation

- [Detailed project status and roadmap](docs/PROJECT_STATUS.md)
- [Architecture and engineering context](docs/architecture/backend-context.md)
- [Integration endpoints](docs/architecture/integration-endpoints.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

Presvo is available under the [MIT License](LICENSE).
