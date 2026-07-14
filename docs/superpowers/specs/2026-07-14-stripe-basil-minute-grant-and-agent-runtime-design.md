# Stripe Basil Minute Grant and Agent Runtime Design

## Goal

Ensure a successful payment activates the starter subscription, grants its 60
call minutes exactly once, and leaves the local LiveKit voice worker available
to accept dispatches.

## Scope

This change covers the current Stripe `2025-08-27.basil` invoice payload,
repairs the missed grant for invoice `in_1Tt4mtFUdMpDTQmClZA1X7UM`, and starts
the existing Compose voice-agent service. It does not add legacy Stripe payload
compatibility, change plan pricing, modify LiveKit agent behavior, or redesign
the billing lifecycle.

## Payment Contract

The authenticated webhook route verifies the Stripe signature before passing
events to `BillingService`. Within that verified boundary, an
`invoice.paid` event whose invoice object has `status == "paid"` is the payment
authority. The removed top-level Invoice `paid` boolean is not consulted.

Non-paid invoice statuses remain ineligible. Existing invoice-ID uniqueness in
the usage ledger remains the exactly-once boundary, so repeated delivery of the
same invoice cannot grant minutes twice.

## Data Flow

1. Stripe sends a signed `invoice.paid` event.
2. The webhook verifies the signature and routes the event to
   `BillingService._handle_invoice_paid`.
3. The billing policy accepts only invoice status `paid`.
4. Billing resolves or bootstraps the subscription, sets it to `active`, and
   applies the starter plan's 60-minute allocation.
5. `UsageAccountingService.grant_invoice` creates one invoice-sourced ledger
   record with `balance_after = 60`.
6. Duplicate events resolve to the existing grant without changing the
   balance again.
7. With positive balance and the already-active number and agent config, the
   SIP webhook can create a call and enqueue `livekit.dispatch`.

## Existing-Invoice Repair

The missed invoice is repaired through `UsageAccountingService.grant_invoice`,
not a raw ledger insert. The operation uses the real invoice ID as `source_id`,
the existing user ID, and the subscription's configured 60-minute allocation.
The service's lock and uniqueness behavior make the repair safe to rerun.

After commit, verification must show exactly one invoice grant for the invoice,
a current balance of 60, and a true dispatch-eligibility balance condition.

## Voice-Agent Runtime

No LiveKit agent code or SDK API changes are required. The existing `agent`
service is started through the `voice` Compose profile after its environment is
validated. Verification requires a running `bmad-opevo-agent-1` container and
startup logs showing successful connection and worker registration rather than
an immediate configuration or provider failure.

## Error Handling

- A non-paid invoice status does not activate or grant minutes.
- A duplicate invoice ID returns the prior grant and creates no additional
  ledger row.
- A missing user, subscription, or invalid allocation aborts the repair
  transaction without a partial ledger write.
- If the agent cannot start or register, preserve the billing repair and report
  the exact runtime/configuration failure without masking it.

## Testing and Verification

Implementation follows red-green-refactor:

1. Add a unit test proving `status == "paid"` is sufficient without the removed
   boolean and that other statuses remain rejected.
2. Add or adapt a webhook test with a Basil-shaped invoice lacking `paid`, then
   assert subscription activation and one 60-minute grant.
3. Run the focused tests red before production changes and green afterward.
4. Run the relevant billing, subscription-policy, usage, and dispatch tests.
5. Verify the repaired database records and current balance directly.
6. Start the voice service and inspect its container state and registration
   logs.

## Success Criteria

- A current signed `invoice.paid` payload with invoice status `paid` activates
  the subscription and grants the plan allocation exactly once.
- The affected user has one invoice grant for
  `in_1Tt4mtFUdMpDTQmClZA1X7UM` and a 60-minute current balance.
- The voice-agent container is running and registered with the configured
  LiveKit project.
- No unrelated user files or worktree changes are modified.
