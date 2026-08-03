# Real Telnyx Local E2E Design

## Decision

Issue 24 is a local runtime-mode mismatch, not an activation-domain defect. The
legacy test account owns a real Telnyx number, while both the API and worker are
running with `TELEPHONY_MODE=fake`. Forwarding verification can complete through
LiveKit in that state, but Go live asks the worker to enable the stored real
provider number. The fake provider deliberately rejects non-fake provider IDs,
so the durable `phone.enable` event terminates with `provider_terminal` and the
activation service safely returns the account to `ready_to_activate`.

Use an explicit, private Compose override for this real-provider E2E session.
Do not change the committed development default or any `.env.example`; those
remain `fake` so an ordinary local startup cannot mutate Telnyx accidentally.

## Runtime configuration

Create a private override outside the repository that sets
`TELEPHONY_MODE=telnyx` for exactly these services:

- `api`, so development-only fake-provider behavior is unavailable during the
  real test and the reported runtime mode matches the worker;
- `worker`, which owns provisioning, routing, cleanup, deactivation, and the
  actual Telnyx `enable_number` operation.

Reuse the existing private credential env-file wiring. Do not copy credentials,
connection identifiers, provider number identifiers, or customer data into the
override, logs, documentation, or version control. The agent does not receive a
telephony-mode override because it communicates through LiveKit and the API; it
does not mutate Telnyx number configuration.

The API and worker must be recreated together from the reviewed worktree and
the same Compose file chain. Post-recreation diagnostics must print only the
selected provider mode and boolean credential-presence checks.

## Safe execution sequence

1. Confirm the database still records one matching account with forwarding
   verified, activation false, a Telnyx provider classification, and a non-fake
   provider number identity. Do not print the number or provider identifier.
2. Make a read-only Telnyx lookup for that exact stored provider identifier.
   Confirm only that it exists and is managed by the configured account. Do not
   display provider response bodies or identifiers.
3. Create the private override and validate the rendered Compose configuration
   without printing secrets.
4. Recreate only API and worker. Preserve PostgreSQL, Redis, MinIO, agent, and
   web containers unless a health check proves a restart is required.
5. Verify both recreated services report `TELEPHONY_MODE=telnyx`, required
   Telnyx settings are present, API health is green, and the worker starts
   without configuration failures.
6. The account owner clicks Go live once. This is the authorized mutation: the
   worker changes the existing number from the disabled Telnyx connection to
   the configured active connection.
7. Verify the outbox event is delivered, the phone projection becomes active,
   activation completes, and no safe failure code remains. Then place one real
   inbound call and inspect API, worker, and agent logs for the complete voice
   path.
8. Keep the override active until the owner explicitly chooses either to keep
   the number live or to disable it. If disabling, confirm the Telnyx connection
   is returned to the disabled connection before removing the override.

## Failure handling

- A failed read-only preflight stops the procedure before any provider
  mutation.
- A retryable Telnyx failure remains in the durable outbox retry path; do not
  bypass it with database edits or direct projection changes.
- A terminal Telnyx failure remains visible through the safe activation failure
  code. Diagnose its provider classification before retrying.
- Do not fabricate activation timestamps, rewrite provider identities, seed the
  fake adapter with a real identifier, or manually mark the phone active.
- Do not retry Go live repeatedly while a prior event is pending or processing.

## Verification and cleanup

Verification is complete only when all of the following are observed:

- API and worker run in Telnyx mode from the same reviewed checkout;
- the real `phone.enable` event is delivered exactly once for the successful
  attempt;
- the durable activation and phone projections are active;
- a real inbound call reaches LiveKit and the agent, with the expected API and
  worker callbacks succeeding;
- logs contain no credentials, raw provider identifiers, or unredacted phone
  numbers;
- the final live-or-disabled provider state is explicitly chosen and verified;
- temporary override files are retained while needed and removed only after
  their provider state is safely resolved.

No production code change is required for Issue 24. The durable record of the
root cause and selected solution is this design; the private runtime override
must remain uncommitted.
