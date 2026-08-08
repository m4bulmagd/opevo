# General-Audience README Refresh Design

**Date:** 2026-08-01

## Goal

Replace the current detailed README with a lighter, product-first introduction
that explains what the application does, shows its implementation status,
connects the architecture into understandable journeys, and gives a new reader
the shortest reliable path to running it locally.

## Audience

The primary audience is a general reader evaluating the product. The README
must remain useful to a developer who wants to run the repository, but detailed
engineering evidence belongs in the existing project-status, architecture, and
contribution documents.

## Scope

- Rewrite `README.md` to approximately 100–130 lines.
- Keep the current product name during this documentation-only change. The
  broader Opevo-to-Opevo rename is separate work.
- Keep the existing landing-page screenshot and use concise surrounding copy.
- Treat `docs/PROJECT_STATUS.md` as the canonical source for implementation and
  production-readiness claims.
- Preserve the current local Docker Compose workflow and browser-proof command.
- Replace the disconnected component map with a connected Mermaid diagram.

## Information Structure

The README will use this order:

1. Product name, one-sentence description, and honest status statement
2. Landing-page screenshot
3. Four concise bullets explaining the customer value
4. A compact `Done`, `In progress`, and `Planned` progress section
5. One connected architecture diagram
6. Minimal local-development instructions
7. Compact technology table
8. Links to detailed documentation, contributing guidance, security, and license

The long inbound-call lifecycle, detailed deletion semantics, repository tree,
extended engineering highlights, and repeated production caveats will be
removed from the README. Their authoritative detail remains in linked project
documentation.

## Progress Vocabulary

`Done` means implemented in the repository and supported by relevant local
tests. It does not imply real-provider or production certification.

`In progress` covers work with an implemented foundation but missing external
certification, operational evidence, localization, legal approval, or remaining
customer workflow details.

`Planned` covers accepted roadmap outcomes that are not implemented. The
section will link to `docs/PROJECT_STATUS.md` instead of reproducing its full
feature matrix or phased roadmap.

## Architecture Design

The diagram will show two connected journeys:

- **Setup and review:** business owner → Next.js dashboard → FastAPI API →
  PostgreSQL and Redis/ARQ worker, with provider connections for authentication,
  billing, telephony, and storage.
- **Inbound call:** caller → Telnyx → LiveKit → voice agent → FastAPI API →
  persisted call data shown in the dashboard.

The diagram must also show the agent's speech/language providers, LiveKit
webhooks returning to the API, background work consuming Redis jobs and durable
PostgreSQL state, and recordings flowing to private object storage. No component
may appear as an unexplained island.

## Local Development

The README will retain Docker and Node.js prerequisites, one core-stack command,
the `http://127.0.0.1:3000/activate` URL, and the disposable browser-proof
commands. It will state that the default Compose path uses local identity and
fake billing, carrier, telephony, and verification providers. Real-call setup
will be linked to the staging documentation instead of explained inline.

## Acceptance Criteria

- A general reader can understand the product and current maturity before the
  architecture section.
- `Done`, `In progress`, and `Planned` claims agree with
  `docs/PROJECT_STATUS.md`.
- The architecture has two complete, connected journeys and accurately reflects
  the repository.
- The documented local commands and URLs match the current Compose setup.
- Detailed evidence is linked rather than duplicated.
- Markdown links resolve, Mermaid syntax is valid, and the working tree retains
  the user's untracked `Opevo_frontend/` directory untouched.
