# README Architecture Diagram Simplification Design

## Goal

Make the README architecture diagram readable at normal GitHub width while
preserving the core runtime topology verified against the codebase.

## Problem

The current diagram is accurate but visually dense. Provider-specific arrows
cross multiple subgraphs, external services sit far from their callers, and the
shared-contract dependency adds long dotted edges. Mermaid consequently renders
the three conceptual areas beside one another with many overlapping lines.

## Approved approach

Keep one Mermaid flowchart with a top-to-bottom outer direction and three
visually stacked subgraphs:

1. `Owner experience`
2. `Inbound call`
3. `Durable platform`

Each subgraph may use a short left-to-right internal flow. Invisible ordering
links may be used only to keep the subgraphs stacked; they must not imply a
runtime dependency.

## Diagram contents

The simplified diagram keeps:

- business owner, Next.js dashboard, and Clerk authentication;
- caller, Telnyx, LiveKit, voice worker, and speech/language providers;
- FastAPI, PostgreSQL, Redis/ARQ, `worker-lifecycle`,
  `worker-background`, and private object storage;
- the server-side dashboard-to-API request;
- signed Clerk and LiveKit webhooks;
- agent transcript/completion delivery to FastAPI;
- the two explicit ARQ queue names;
- direct LiveKit room-composite recording delivery to object storage.

The diagram removes:

- individual FastAPI/background-worker arrows to Stripe, Telnyx, LiveKit,
  Gemini, and storage when those details are already described by the prose;
- the `libs/shared` dependency node, because the boundary table immediately
  above the diagram documents it;
- optional realtime Pub/Sub edges, because the worker-ownership paragraph
  immediately below the diagram documents that non-authoritative path.

The detailed prose below the diagram remains unchanged and continues to explain
dispatch metadata, provider work, recording reconciliation, and Redis realtime
behavior.

## Layout and semantics

- Use `flowchart TB` for the overall reading direction.
- Use concise edge labels only where they explain a boundary or protocol.
- Avoid bidirectional arrows unless both directions are material to the overview.
- Keep PostgreSQL visibly connected to FastAPI and both workers.
- Keep Redis between FastAPI and the two workers so queue ownership is obvious.
- Do not add visible runtime relationships solely to force layout. Any invisible
  ordering link must be visually absent and must connect only the three
  subgraph-order anchors named above.

## Verification

After editing the README:

1. Inspect the rendered Mermaid diagram or a local Mermaid render when tooling is
   available.
2. Run `git diff --check -- README.md`.
3. Run the focused deployment-readiness documentation contract test.
4. Confirm that only layout/detail level changed and every retained edge is
   supported by the code paths established in the architecture audit.
