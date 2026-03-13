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

## Remaining Manual Verification

- Real Postgres migration run against the target database.
- Real Clerk webhook delivery.
- Real Stripe webhook delivery with live subscription objects.
- Real Telnyx provisioning and `app-active` / `app-disabled` switching.
- Real LiveKit webhook verification and agent dispatch against a cloud project.
- End-to-end forwarded phone call with transcript, summary, and minute deduction.

## Blockers For Full Staging Smoke Path

- No live provider credentials are available in this worktree session.
- No staging Postgres, Redis, MinIO, Clerk, Stripe, Telnyx, or LiveKit environments were provided for final integration verification.
