# Presvo Open-Source Documentation Design

## Summary

Presvo will be presented as a portfolio-first, contributor-friendly open-source
project. The public documentation will show the product and its engineering
depth quickly, while remaining explicit that it is a working pre-production
MVP progressing toward a controlled beta.

The documentation will use `README.md` as the concise portfolio landing page
and `docs/PROJECT_STATUS.md` as the canonical source for feature status,
limitations, production-readiness gates, and roadmap details. Supporting
open-source files will define the MIT license, contribution workflow, and
private security-reporting process.

## Goals

- Give a first-time visitor a clear understanding of Presvo within a few
  minutes.
- Demonstrate the implemented product and the repository's strongest
  engineering work without overstating production readiness.
- Distinguish implemented, partial, planned, and exploratory capabilities.
- Publish a realistic roadmap led by guided onboarding and production
  certification, with a Retell-style conversation-flow builder as later work.
- Make the repository legally reusable under the MIT License and approachable
  to potential contributors.
- Reduce drift by establishing one canonical product-status document.

## Non-Goals

- Changing application behavior or implementing roadmap features.
- Claiming that Presvo is ready for unattended production deployment.
- Converting the existing documentation into a full documentation website.
- Deleting historical design specifications, implementation plans, or audits.
- Promising roadmap dates or compatibility with providers that are not present
  in the current implementation.
- Making the provider-backed voice path credential-free.

## Public Positioning

The project name is **Presvo**. “AI voice assistant platform” may be used as a
descriptive subtitle, but “AI Call Assistant” and “Opevo” will not be presented
as competing public product names.

The README will describe the repository as:

> An open-source, France-first AI voice assistant platform for handling inbound
> business calls.

Its status language will be:

> **Status:** Active development. Presvo is a working pre-production MVP with a
> production-oriented architecture. Work is progressing toward a controlled
> beta, with onboarding, compliance, recovery testing, and real-provider
> certification still in progress.

This positioning is portfolio-first: the opening sections optimize for people
assessing the product and engineering work. Self-hosting and contribution
material remain available but do not displace the product story.

## Documentation Structure

### `README.md`

The README is the public landing page. Its information order is:

1. Presvo name, purpose, and status.
2. Existing optimized landing-page screenshot.
3. Implemented product capabilities.
4. Concise limitations and a link to the canonical status document.
5. High-level Mermaid architecture diagram and inbound-call sequence.
6. Engineering highlights, including durable transcript persistence,
   transactional outbox delivery, authoritative usage accounting, tenant
   isolation, observability, deployment hardening, and automated tests.
7. Technology stack.
8. Local quick start that separates the core local stack from provider-backed
   voice operation.
9. Repository map and documentation index.
10. Contribution, security, and MIT license links.

Detailed deployment commands, CI policy, and branch-protection instructions
will remain in their focused operational documents and be linked from the
README.

### `docs/PROJECT_STATUS.md`

This file is the canonical product-status source. It contains:

- The current France-first product boundary.
- A feature matrix using the approved status vocabulary.
- Known customer-facing, operational, compliance, and verification
  limitations.
- Production-readiness gates.
- A phased roadmap with no promised dates.
- Links to relevant current architecture documents for supporting detail.

### `CONTRIBUTING.md`

The contribution guide contains:

- Repository prerequisites and workspace layout.
- Local development paths for API, agent, and web.
- Required lint, type, test, build, and migration checks.
- Guidance for changes that require external provider credentials.
- Expectations for focused changes, tests, migrations, documentation, and
  secret handling.

### `SECURITY.md`

The security policy directs vulnerability reports to GitHub private
vulnerability reporting once the repository is public. It defines the
information a useful report should contain and asks reporters not to publish
customer data, credentials, recordings, or transcripts. It does not invent a
private email address or disclosure response-time commitment.

### `LICENSE`

The repository will use the standard MIT License.

### Existing documents

- `docs/Verdict.md` remains as a historical audit and receives a prominent
  notice that it has been superseded by later hardening work and
  `docs/PROJECT_STATUS.md`.
- Current architecture documents will use repository-relative links instead
  of machine-specific `/home/...` paths where encountered in public-facing
  current documentation.
- Historical specifications and plans remain available as engineering history;
  their point-in-time statements are not silently rewritten as current claims.
- Ignored local planning files are not promoted as public sources of truth.

## Feature Status Vocabulary

Every capability in the public status document uses exactly one of these
labels:

- **Implemented:** Present in the repository and supported by relevant tests.
- **Partial:** A technical foundation exists, but the customer workflow,
  operational proof, or real-provider validation is incomplete.
- **Planned:** Accepted roadmap work with a clear intended outcome.
- **Exploratory:** A possible direction that is not a delivery commitment.

Implemented claims must be traceable to current source code and tests. Planned
or exploratory capabilities must never be described with completed-product
language.

## Roadmap Design

### Phase 1: Guided onboarding

- Step-by-step onboarding flow.
- Business and use-case templates.
- Agent identity, context, and knowledge setup.
- Carrier-appropriate call-forwarding guidance.
- Recording and AI-disclosure acknowledgement.
- Test-call or browser-based agent preview.
- Final readiness review and go-live action.
- Resumable failure and delayed-provisioning states.

### Phase 2: Customer workflow completion

- French localization and locale-aware formatting.
- Account and session controls.
- Inline recording playback.
- Call pagination, search, and improved review workflows.
- Account data export and deletion.
- Approved legal, privacy, retention, subprocessor, and support surfaces.
- Accessibility and frontend performance gates.

### Phase 3: Production certification

- Real-provider staging certification journeys.
- Backup restoration and object-lifecycle proof.
- Provider-outage and incident drills.
- Concurrency and load testing.
- Behavioral voice-agent evaluations.
- Controlled design-partner beta with explicit stop conditions.

### Phase 4: Conversation-flow builder

This phase is inspired by Retell AI's structured conversation flows, not
Recall.ai's meeting-bot platform. It starts with runtime correctness rather than
a visual canvas:

1. Typed flow model and business templates.
2. Conversation steps, conditional transitions, fallbacks, and end states.
3. Validation, versioning, simulation, and call-path traces.
4. Visual node editor after the underlying runtime is proven.
5. Reusable subflows and tool/function nodes after the core authoring model is
   stable.

### Phase 5: Advanced capabilities

- Live call monitoring and intervention.
- Human transfer and tool calls.
- Calendar and CRM integrations.
- Reusable conversation components.
- Mobile experience.
- Additional countries and plans after the France-first path is proven.

## Architecture Narrative

The README's Mermaid diagram will keep the system understandable at a glance:

- Clerk authenticates dashboard users.
- Stripe drives paid subscription state and minute grants.
- Telnyx provides the French number and routes inbound SIP calls.
- LiveKit owns the real-time room and dispatches the voice-agent worker.
- The agent uses configured speech and language providers and sends incremental
  transcripts and completion facts to the API.
- FastAPI, PostgreSQL, Redis/ARQ, the transactional outbox, and object storage
  provide the durable control plane and post-call processing.
- Next.js reads authenticated API state for onboarding, agent configuration,
  calls, recordings, and billing.

The diagram will show durable and real-time responsibilities without exposing
deployment secrets or presenting optional realtime dashboard delivery as a
working launch feature.

## Local Development Narrative

The quick start will distinguish two outcomes:

1. **Core local stack:** PostgreSQL, Redis, MinIO, migrations, API, worker, and
   web through `compose.dev.yaml`.
2. **Live voice path:** the optional Compose `voice` profile plus valid Clerk,
   Stripe, Telnyx, LiveKit, and model-provider configuration as applicable.

The README will not promise that a complete phone call works without hosted
provider credentials. Detailed environment configuration remains in the
application `.env.example` files and staging/deployment documentation.

## Accuracy, Failure Handling, and Maintenance

- `docs/PROJECT_STATUS.md` is the only canonical feature-status and roadmap
  document.
- The README contains a short status summary and links to the canonical source.
- Roadmap phases describe outcomes and dependencies without dates.
- Provider-dependent behavior is labeled clearly so setup failures are not
  mistaken for credential-free local functionality.
- Historical documents are labeled rather than rewritten to conceal earlier
  states.
- Documentation must not contain secrets, interpolated environment values,
  customer content, full private phone numbers, or machine-specific links.
- If a feature claim cannot be confirmed from current code or tests, it is
  labeled partial instead of implemented.

## Verification Strategy

Implementation verification will include:

- Review the final diff against the repository inventory from this design
  process.
- Search public-facing documentation for stale product names, machine-specific
  paths, placeholders, and unsupported completion claims.
- Validate Markdown structure and repository-relative internal links.
- Confirm the optimized WebP screenshot is used rather than embedding the
  larger PNG asset.
- Run the documentation-relevant web and agent test suites.
- Run available formatting or documentation checks without altering unrelated
  application files.
- Confirm `git status` contains no `.env`, credentials, generated artifacts, or
  unrelated user changes.

The API suite will not be represented as newly verified unless it completes in
the implementation environment; existing CI configuration may be described
without fabricating a fresh local pass.

## Acceptance Criteria

The documentation work is complete when:

1. The README explains Presvo's purpose, current state, architecture, strongest
   implemented capabilities, and local setup without relying on other files.
2. The existing product screenshot is visible near the top of the README.
3. `docs/PROJECT_STATUS.md` accurately separates implemented, partial, planned,
   and exploratory work.
4. Guided onboarding is the next product milestone and the conversation-flow
   builder is clearly later work.
5. Presvo is the consistent public product name.
6. Production ambition is clear without claiming current production readiness.
7. The repository contains the MIT License, contribution guidance, and a
   private security-reporting policy.
8. Historical audit content is visibly marked as superseded where it could be
   mistaken for current status.
9. Current public-facing documentation contains no machine-specific links or
   known unsupported feature claims.
10. Documentation verification passes and the final diff contains only the
    approved documentation and open-source metadata changes.
