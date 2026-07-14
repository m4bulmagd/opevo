# Credential rotation and revocation runbook

Gate 0 is **closed by user attestation** received at `2026-07-13T04:32:42Z`. The user attested that all 14 replacement credentials were installed, tested with the applications, and that the old values were revoked.

The timestamp in this runbook is the time the attestation was received. It is not a provider rotation, verification, or revocation timestamp. Provider action times and provider audit-event identifiers were not supplied and are not inferred.

This document must remain value-free. Record credential identifiers, timestamps, operators, provider audit-event references, and smoke-test outcomes only. Never paste a secret, token, signature, private key, webhook signing value, or partially masked credential here.

## Credential inventory

`Complete — user attested (received 2026-07-13T04:32:42Z)` records the user's completion statement and its receipt time only. It does not claim that the provider action occurred at that time.

| Credential name | Local environment variable(s) | Rotated | Verified | Revoked | Operator | Revocation evidence | Smoke test | Smoke-test result | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Stripe API | `STRIPE_SECRET_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Complete one test-mode authenticated Stripe API operation. | Passed — user attested | Complete |
| Stripe webhook | `STRIPE_WEBHOOK_SECRET` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Deliver a signed Stripe test event and confirm successful handling. | Passed — user attested | Complete |
| Clerk publishable | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Load the web authentication flow and confirm Clerk initializes. | Passed — user attested | Complete |
| Clerk secret | `CLERK_SECRET_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Complete an authenticated server-side Clerk request. | Passed — user attested | Complete |
| Clerk webhook | `CLERK_WEBHOOK_SECRET` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Deliver a signed Clerk test event and confirm successful handling. | Passed — user attested | Complete |
| LiveKit key/secret | `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Connect the API and agent to a disposable LiveKit room and complete a test dispatch. | Passed — user attested | Complete |
| Telnyx API | `TELNYX_API_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Complete a read-only authenticated Telnyx API request. | Passed — user attested | Complete |
| Gemini | `GEMINI_API_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Complete one minimal Gemini request through each configured consumer. | Passed — user attested | Complete |
| Speechmatics | `SPEECHMATICS_API_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Transcribe a short non-sensitive audio sample. | Passed — user attested | Complete |
| ElevenLabs | `ELEVENLABS_API_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Synthesize a short non-sensitive audio sample. | Passed — user attested | Complete |
| Mistral | `MISTRAL_API_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Complete one minimal Mistral request through the configured consumer. | Passed — user attested | Complete |
| S3 access/secret | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Put, read, and delete a disposable object in a non-production probe prefix. | Passed — user attested | Complete |
| dispatch JWT secret | `AGENT_DISPATCH_JWT_SECRET` | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | Complete — user attested (received 2026-07-13T04:32:42Z) | User (conversation attestation) | User attestation only; no provider audit ID supplied. | Confirm a JWT signed by the new secret is accepted and one signed by the old secret is rejected. | Passed — user attested | Complete |

## Rotation procedure

Repeat these steps for every inventory row. For a paired credential, rotate and deploy the pair atomically.

1. Identify every production, staging, CI, and local consumer before changing the credential.
2. Issue the replacement in the provider dashboard or generate a new high-entropy internal secret.
3. Store the replacement only in the approved secret store or the relevant ignored local `.env` file. Never put it in source control, shell history, logs, screenshots, tickets, chat, or this runbook.
4. Restart or redeploy every affected process.
5. Run the row-specific smoke test and record the `rotated` and `verified` timestamps, operator, and a value-free result.
6. Revoke the exposed credential. Record the `revoked` timestamp and a non-secret provider audit-event, credential ID, or screenshot reference that proves revocation.
7. Re-run the smoke test with the replacement. Where supported, explicitly prove the old credential is rejected.
8. Mark the row complete only when rotation, verification, and revocation evidence are all present.

## Local secret-file controls

Restrict the local files without reading or printing their contents:

```bash
chmod 600 apps/api/.env apps/agent/.env apps/web/.env
stat -c '%a %n' apps/api/.env apps/agent/.env apps/web/.env
```

Required result:

```text
600 apps/api/.env
600 apps/agent/.env
600 apps/web/.env
```

Confirm the files are ignored and have never been committed:

```bash
git check-ignore apps/api/.env apps/agent/.env apps/web/.env
git log --all -- apps/api/.env apps/agent/.env apps/web/.env
```

Required result: `git check-ignore` prints all three paths and the history command prints no commits.

## Gate 0 sign-off

Gate 0 was closed from the user's conversation attestation received at `2026-07-13T04:32:42Z`. The user attested that every row has:

- a replacement credential installed in the applications;
- a successful application smoke test; and
- revocation of the old credential.

This is an attestation-based close, not independent provider-dashboard verification. The receipt timestamp is not a provider action timestamp, and no provider audit IDs were supplied. If provider timestamps or audit references are collected later, append them without replacing or reinterpreting the attestation receipt time.
