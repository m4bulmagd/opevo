# Credential Rotation and Revocation Runbook

Use this procedure for planned rotation, suspected exposure, provider
offboarding, or ownership transfer. Store completion evidence in the approved
operations/change record, not in this repository. This runbook must remain
value-free: never paste a secret, token, signature, private key, webhook body,
credential fragment, or provider payload into Git.

## Current credential classes

The tracked `.env.example` files and deployment configuration are authoritative
for exact consumers. Before rotating, compare this inventory with those sources
and include any newly introduced credential.

| Credential class | Environment variable(s) | Minimum value-free verification |
| --- | --- | --- |
| Clerk browser/API | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | Sign in with a disposable account and complete one authenticated server request. |
| Clerk token verification | `CLERK_JWT_KEY` or the credentials controlling `CLERK_JWKS_URL` | Accept a current Clerk session and reject an invalid token without logging either value. |
| Clerk webhook | `CLERK_WEBHOOK_SECRET` | Deliver one signed disposable Clerk event and confirm one accepted result. |
| Stripe API | `STRIPE_SECRET_KEY` | Create or retrieve one test-mode hosted session through the application. |
| Stripe webhook | `STRIPE_WEBHOOK_SECRET` | Deliver one signed Stripe test event and confirm idempotent acceptance. |
| LiveKit API | `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Register the disposable agent revision and complete one staging dispatch. |
| Telnyx API | `TELNYX_API_KEY` | Perform one approved read-only staging request before any mutation. |
| Gemini | `GEMINI_API_KEY` | Complete one bounded summary request and each enabled Gemini voice mode. |
| Speechmatics | `SPEECHMATICS_API_KEY` | Complete one short non-sensitive STT or TTS staging probe. |
| ElevenLabs | `ELEVENLABS_API_KEY` | Complete one short non-sensitive enabled STT or TTS probe. |
| Deepgram | `DEEPGRAM_API_KEY` | Complete one short non-sensitive STT probe when Deepgram is enabled. |
| Object storage | `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Put, sign/read, and delete one disposable object in a probe prefix. |
| Agent dispatch signing | `AGENT_DISPATCH_JWT_SECRET` | Accept a newly signed disposable token and reject a token signed with the retired value. |
| Local development auth | `LOCAL_AUTH_TOKEN` | Run only the explicit provider-free development journey; never deploy this token or mode. |

Do not create, retain, or rotate a credential merely because an unused legacy
setting exists. First prove a current runtime consumer or remove the unused
setting through a separately reviewed change.

## Rotation procedure

For paired credentials, rotate and deploy the pair atomically unless the
provider documents a safe overlap mechanism.

1. Open a value-free change or incident record with the environment, owner,
   credential class, affected consumers, start time, and rollback authority.
2. Identify every production, staging, CI, and local consumer from deployment
   configuration, `.env.example` files, and provider access policy.
3. Issue the replacement in the provider dashboard or generate a new
   high-entropy internal secret. Record only an opaque credential/audit ID.
4. Store the replacement in the approved secret store or relevant ignored
   local `.env` file. Never place it in source control, shell history, logs,
   screenshots, tickets, chat, or this runbook.
5. Restart or deploy one bounded consumer set using the replacement. Preserve a
   rollback path until verification completes.
6. Run the credential-class smoke check above and record only pass/fail,
   timestamps, operator, environment, and opaque evidence references.
7. Move the remaining consumers, repeat verification, then revoke the old
   credential. Where supported, prove the retired value is rejected.
8. Monitor authentication and provider-error signals for one approved window.
   Close the record only after replacement verification and revocation evidence
   are both present.

If the replacement fails, stop rollout, restore the last independently verified
secret reference where safe, and keep the potentially exposed credential
revoked when security requires it. Escalate instead of weakening verification
or re-enabling a known-compromised value.

## Local secret-file controls

Restrict ignored local secret files without reading or printing their contents:

```bash
chmod 600 apps/api/.env apps/agent/.env apps/web/.env
stat -c '%a %n' apps/api/.env apps/agent/.env apps/web/.env
git check-ignore apps/api/.env apps/agent/.env apps/web/.env
git log --all -- apps/api/.env apps/agent/.env apps/web/.env
```

The expected permissions are `600`; all three paths must be ignored; the Git
history command must print no commits. If a secret file or value was committed,
treat the credential as exposed, rotate it immediately, and follow the private
security-reporting process in [`SECURITY.md`](../../SECURITY.md).

## Evidence boundary

The repository records this reusable procedure only. Provider action times,
audit-event identifiers, smoke results, approvals, and incident timelines
belong in access-controlled operational evidence with the organization's
retention policy. Do not append completed rotation tables or attestations here.
