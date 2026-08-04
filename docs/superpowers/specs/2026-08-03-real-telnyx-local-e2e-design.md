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
2. Make a read-only Telnyx lookup using the exact stored phone number as the
   filter. Confirm that the returned provider identity matches the stored
   provider identity, is managed by the configured account, and is attached to
   one of the two configured connections. Do not display provider response
   bodies or identifiers. For this legacy account, the expected pre-Go-live
   drift is an active Telnyx connection with an inactive durable projection.
3. Create the private override and validate the rendered Compose configuration
   without printing secrets.
4. Recreate only API and worker. Preserve PostgreSQL, Redis, MinIO, agent, and
   web containers unless a health check proves a restart is required.
5. Verify both recreated services report `TELEPHONY_MODE=telnyx`, required
   Telnyx settings are present, API health is green, and the worker starts
   without configuration failures.
6. The account owner clicks Go live once. This is the authorized mutation: the
   worker idempotently assigns the exact existing number to the configured
   active connection, confirms the provider response, and only then reconciles
   the inactive durable projection to active.
7. Verify the outbox event is delivered, the phone projection becomes active,
   activation completes, and no safe failure code remains. Then place one real
   inbound call and inspect API, worker, and agent logs for the complete voice
   path.
8. Keep the override active until the owner explicitly chooses either to keep
   the number live or to open a separate reviewed offline-workflow issue. The
   current product has no reversible Go-offline operation: do not call Telnyx
   directly or remove the override while the application considers the number
   active. For this execution, the owner selected 28A Keep live.

## Failure handling

- A failed read-only preflight stops the procedure before any provider
  mutation.
- A matching provider identity on the configured active connection is accepted
  only for the documented legacy drift. Any connection other than the exact
  configured active or disabled connection stops the procedure.
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
- the preflight proves exact provider identity and classifies the legacy remote
  connection as the configured active connection without exposing its value;
- the real `phone.enable` event is delivered exactly once for the successful
  attempt;
- the durable activation and phone projections are active;
- a real inbound call reaches LiveKit and the agent, with the expected API and
  worker callbacks succeeding;
- logs contain no credentials, raw provider identifiers, or unredacted phone
  numbers;
- the final keep-live or separate-offline-workflow decision is explicitly
  chosen and its stated consistency check passes;
- temporary override files remain retained for Keep live. Removing them is
  permitted only inside the separately reviewed offline workflow after
  application and provider state are safely resolved.

No production code change is required for Issue 24. The durable record of the
root cause and selected solution is this design; the private runtime override
must remain uncommitted.

## Accepted recording limitations

The owner selected Issue 26C and Issue 27C for this local E2E session:

- **Issue 26C — recording unsupported in this local topology.** LiveKit Cloud
  cannot upload egress output to the Docker-only `http://minio:9000` endpoint.
  A future production-aligned fix should configure a private, externally
  reachable HTTPS S3-compatible bucket. The failed recording from this session
  is not recoverable.
- **Issue 27C — premature recording availability remains deferred.** Recording
  start currently publishes the call playback projection before egress upload
  completes, so a failed egress can appear recorded in list projections while
  detail playback correctly returns unavailable. The recommended future fix is
  to publish playback availability only after an exact successful terminal
  egress result and to cover failed, aborted, limit-reached, reconciliation, and
  recovery paths with tests.

These limitations do not invalidate the voice-path E2E. Completion now requires
successful Go live, one dispatched and completed real call, durable transcript
and summary evidence, no call failure, and an explicit final provider-state
decision. Recording success is intentionally excluded until Issue 26 is
reopened.
