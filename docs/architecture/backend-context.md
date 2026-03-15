# Backend Context

This document captures implementation notes and staging verification for the backend foundation MVP.

## Current State

- Chunk 1 foundation implemented: app scaffold, config, schema, repositories, and Clerk auth sync.
- Chunk 2 core API paths implemented: Stripe billing webhook, telephony service boundary, authenticated websocket gate, and LiveKit inbound dispatch webhook.
- Chunk 3 initial agent and post-call layers implemented: provider registry, prompt builder, worker entrypoint scaffold, and post-call lifecycle scaffolding.

## Verified Locally

- API tests covering health, auth, billing, telephony, realtime, LiveKit dispatch, repository flow, and post-call lifecycle.
- Agent tests covering prompt building, pipeline config selection, and runtime event emission.
- API Docker image build.
- Agent Docker image build.
- Local infrastructure stack prepared in [compose.yaml](/home/i933k/code/ai/bmad-opevo/.worktrees/backend-foundation-mvp/compose.yaml) for PostgreSQL 17.8, Redis 7.4.7, and MinIO.
- LiveKit SIP participant field mapping reviewed against the official docs on 2026-03-15: `sip.phoneNumber` is the caller number for inbound trunks and `sip.trunkPhoneNumber` is the dialed trunk number.

## Remaining Manual Verification

- Real Postgres migration run against the target database.
- Real Clerk webhook delivery.
- Real Stripe webhook delivery with live subscription objects.
- Real Telnyx provisioning and `app-active` / `app-disabled` switching.
- Real LiveKit webhook verification and agent dispatch against a cloud project.
- End-to-end forwarded phone call with transcript, summary, and minute deduction.

## Staging Smoke Status

Not executed in this session.

Ready for manual execution once these external credentials and endpoints are available:
- Clerk issuer, JWT secret, and webhook secret
- Stripe webhook secret and live test-mode subscription objects
- Telnyx API key and active/disabled connection IDs
- LiveKit URL, API key, and API secret
- Gemini, Speechmatics, Deepgram, and ElevenLabs credentials as needed
- Reachable staging Postgres, Redis, and S3-compatible storage if not using the local Compose stack

## Blockers For Full Staging Smoke Path

- No live provider credentials are available in this worktree session.
- No staging Postgres, Redis, MinIO, Clerk, Stripe, Telnyx, or LiveKit environments were provided for final integration verification.
