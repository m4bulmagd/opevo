# Credential rotation and revocation runbook

Gate 0 remains **open** until an operator confirms that every replacement credential works and every exposed credential has been revoked. Generating a replacement without revoking the old value does not pass the gate.

This document must remain value-free. Record credential identifiers, timestamps, operators, provider audit-event references, and smoke-test outcomes only. Never paste a secret, token, signature, private key, webhook signing value, or partially masked credential here.

## Credential inventory

Use UTC RFC 3339 timestamps. `Pending user confirmation` means the action has not been evidenced to the repository operator; it is not proof of rotation, verification, or revocation.

| Credential name | Local environment variable(s) | Rotated | Verified | Revoked | Operator | Revocation evidence | Smoke test | Smoke-test result | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Stripe API | `STRIPE_SECRET_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Complete one test-mode authenticated Stripe API operation. | Pending user confirmation | Pending user confirmation |
| Stripe webhook | `STRIPE_WEBHOOK_SECRET` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Deliver a signed Stripe test event and confirm successful handling. | Pending user confirmation | Pending user confirmation |
| Clerk publishable | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Load the web authentication flow and confirm Clerk initializes. | Pending user confirmation | Pending user confirmation |
| Clerk secret | `CLERK_SECRET_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Complete an authenticated server-side Clerk request. | Pending user confirmation | Pending user confirmation |
| Clerk webhook | `CLERK_WEBHOOK_SECRET` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Deliver a signed Clerk test event and confirm successful handling. | Pending user confirmation | Pending user confirmation |
| LiveKit key/secret | `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Connect the API and agent to a disposable LiveKit room and complete a test dispatch. | Pending user confirmation | Pending user confirmation |
| Telnyx API | `TELNYX_API_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Complete a read-only authenticated Telnyx API request. | Pending user confirmation | Pending user confirmation |
| Gemini | `GEMINI_API_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Complete one minimal Gemini request through each configured consumer. | Pending user confirmation | Pending user confirmation |
| Speechmatics | `SPEECHMATICS_API_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Transcribe a short non-sensitive audio sample. | Pending user confirmation | Pending user confirmation |
| ElevenLabs | `ELEVENLABS_API_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Synthesize a short non-sensitive audio sample. | Pending user confirmation | Pending user confirmation |
| Mistral | `MISTRAL_API_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Complete one minimal Mistral request through the configured consumer. | Pending user confirmation | Pending user confirmation |
| S3 access/secret | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Put, read, and delete a disposable object in a non-production probe prefix. | Pending user confirmation | Pending user confirmation |
| agent internal token | `AGENT_INTERNAL_API_TOKEN` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Confirm the new token is accepted and the old token is rejected by an agent-only endpoint. | Pending user confirmation | Pending user confirmation |
| dispatch JWT secret | `AGENT_DISPATCH_JWT_SECRET` | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Pending user confirmation | Confirm a JWT signed by the new secret is accepted and one signed by the old secret is rejected. | Pending user confirmation | Pending user confirmation |

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

The controller may close Gate 0 only after checking every row has:

- a rotation timestamp;
- a successful smoke-test result and verification timestamp;
- an operator identity;
- a revocation timestamp; and
- value-free evidence that the old credential was revoked or rejected.

As recorded above, all credential actions are pending user confirmation, so Gate 0 is open.
