# Self-Serve France MVP Design

## Summary

This spec defines the shortest path from the current partially verified product to a real self-serve MVP for France. The launch scope stays deliberately narrow: one country, one paid plan, one voice pipeline, automatic number provisioning after successful checkout, required agent setup before going live, and a customer-visible retry path if provisioning fails.

The design keeps the current backend foundation, worker-based provisioning, LiveKit call handling, and dashboard shell. The main change is turning the existing backend-first system into a product-complete self-serve flow with explicit onboarding states, customer-visible operational status, and removal of hidden manual prerequisites.

## Goal

Allow a brand-new France-based customer to sign up, pay, receive a French number automatically, complete agent setup, enable routing, and successfully handle a real inbound call without staff intervention.

## Non-Goals

- Launching outside France.
- Supporting multiple paid plans in the first self-serve release.
- Exposing `sts` as a launch pipeline option.
- Adding outbound calling, team accounts, or advanced analytics.
- Building a full automated refund or account-recovery flow for provisioning failures.

## Launch Scope

The self-serve MVP launch includes:

- France-only number provisioning
- one paid plan: `starter`
- one customer-facing voice pipeline: `stt_llm_tts`
- automatic phone-number provisioning after successful Stripe activation
- required agent setup before routing can be enabled
- manual retry for failed number provisioning
- customer-visible assigned number and onboarding state in the dashboard

The launch does not include:

- `sts` pipeline exposure in the UI
- plan comparison or plan switching UX
- concierge provisioning as the default path
- hidden operational steps required for normal customers

## Current State

- The repo already contains the core backend, agent, and dashboard applications.
- Stripe activation already persists subscription state and allocates minutes.
- Phone provisioning already exists as an async job, but can intentionally stop in `phone_number_provisioning_review_required` instead of completing self-serve assignment.
- Agent configuration already supports runtime fields and live enable/disable switching.
- Calls, transcripts, recordings metadata, summaries, and usage charging already exist at the backend contract level.
- The staging smoke path is only partially verified, and several provider-backed steps remain unproven end to end.

## Problem Statement

The current product shape is close to an MVP backend, but not yet a self-serve customer product.

The main gaps are:

- number provisioning is not guaranteed to complete automatically
- the dashboard does not expose a true onboarding/readiness state model
- routing can fail because backend prerequisites are hidden from the user
- the UI still exposes more option surface than the launch should support
- the full Stripe -> provisioning -> setup -> routing -> first real call path is not yet proven end to end for a normal customer

## Proposed Approaches

### Approach A: Synchronous provisioning during checkout

After Stripe checkout succeeds, attempt number purchase and assignment inline before the user fully returns to the app.

Pros:

- Simple mental model
- Immediate success/failure

Cons:

- Couples checkout to Telnyx latency and failure modes
- Riskier user experience
- Harder to recover cleanly

### Approach B: Async self-serve provisioning after subscription activation

Stripe activates the subscription, then a background job automatically provisions the French number. The dashboard shows provisioning progress, success, or failure and provides a manual retry action.

Pros:

- Operationally safer
- Matches the existing queue-backed design
- Easier to recover from provider failures
- Strongest path to a real self-serve product

Cons:

- Requires more explicit product state in the UI

### Approach C: Manual ops fallback

Stripe activates the subscription, but number assignment still relies on internal staff or manual review.

Pros:

- Fastest to ship

Cons:

- Not truly self-serve
- Conflicts with the launch goal

## Recommendation

Use Approach B.

The product should treat subscription activation and phone provisioning as separate but connected lifecycle states. Stripe remains the source of truth for payment, while the app owns customer-visible provisioning status and recovery. This preserves the current backend architecture while making the customer flow robust enough for self-serve launch.

## Design

### User Flow

1. User signs up and enters the dashboard.
2. User starts checkout for the single paid plan.
3. Stripe persists the subscription, but automatic phone provisioning does not start until the first fresh `invoice.paid` event confirms the paid `starter` subscription.
4. Dashboard shows `Subscription active` and `Number provisioning in progress`.
5. If provisioning succeeds, the assigned French number becomes visible in the dashboard.
6. User completes required setup:
   - agent name
   - owner or business context
   - prompt or lightweight knowledge
7. Only after setup is complete does the UI allow `Enable routing`.
8. User enables routing successfully.
9. First real inbound call produces:
   - persisted call record
   - transcript
   - summary
   - recording access when available
   - minute deduction

### Customer-Visible State Model

The product should stop inferring readiness indirectly and instead expose explicit onboarding and operational states.

Minimum customer-visible states:

- `not_subscribed`
- `subscription_active`
- `provisioning_number`
- `number_ready`
- `setup_required`
- `ready_to_enable`
- `live`
- `provisioning_failed`

`number_ready` is a domain condition, not necessarily the final dashboard banner state. The onboarding read model should expose both detailed fields and one derived `overall_status`.

`overall_status` must be derived with explicit precedence in this order:

1. `live`
2. `provisioning_failed`
3. `provisioning_number`
4. `setup_required`
5. `ready_to_enable`
6. `subscription_active`
7. `not_subscribed`

Interpretation rules:

- `live`: routing is enabled successfully
- `provisioning_failed`: latest provisioning record is failed and retryable or support action is needed
- `provisioning_number`: subscription is active and provisioning is queued or running, or payment succeeded but the provisioning record has not yet completed
- `setup_required`: provisioning succeeded and a number exists, but the agent-setup gate is still incomplete
- `ready_to_enable`: provisioning succeeded, a number exists, setup is complete, and routing is still off
- `subscription_active`: paid subscription exists, but provisioning has not started yet due to webhook sequencing or state reconciliation
- `not_subscribed`: no active paid subscription exists

The dashboard should show one primary status surface that includes:

- current onboarding status
- assigned number when available
- routing state
- retry action when provisioning failed
- support guidance when provisioning remains unresolved

### Backend Design

#### Billing Activation

Retain Stripe webhook-driven activation as the source of truth for paid access.

For launch, the provisioning trigger must be explicit:

- `customer.subscription.created` may create or update the local subscription record, but it must not allocate minutes and must not trigger provisioning
- automatic provisioning must start only after a fresh `invoice.paid` event for the `starter` subscription
- provisioning must not start for incomplete, unpaid, or ambiguous subscription states

The webhook state machine for launch should be:

1. `customer.subscription.created`
   - upsert the local subscription shell
   - persist plan and Stripe identifiers
   - do not allocate minutes yet
   - do not enqueue provisioning
2. first fresh `invoice.paid` for that subscription
   - create or reconcile the local subscription row if the created event did not arrive first
   - mark the subscription active
   - allocate starter minutes through the activation path
   - enqueue provisioning if no assigned number exists and no successful provisioning record already exists
3. later fresh `invoice.paid` events for the same subscription
   - perform the normal period reset
   - do not enqueue provisioning again when the number is already assigned or provisioning already succeeded

Webhook idempotency must prevent duplicate credits and duplicate provisioning jobs for the same Stripe event id.

After the qualifying first `invoice.paid` event:

- persist subscription state
- allocate minutes
- enqueue the phone provisioning job automatically

This job should now be treated as a first-class customer flow, not an internal side effect. The plan and implementation should update the current backend behavior so provisioning no longer starts on `customer.subscription.created`.

#### Phone Provisioning

Provisioning should target France-only launch rules.

Requirements:

- remove any implicit fallback to `US`
- store or force the launch country as `FR` for self-serve users
- reject provisioning for unsupported non-`FR` country values during this launch
- search only France-compatible numbers for launch
- purchase and assign automatically when the candidate passes cost and availability checks
- persist a machine-readable provisioning result for UI consumption
- preserve a failed state when provisioning does not complete
- support a user-triggered manual retry endpoint

The existing `phone_number_provisioning_review_required` behavior should be refactored so the product no longer depends on internal review as the normal outcome. Failure states may still produce notifications or logs, but the dashboard must have a stable way to render them and recover through retry.

For durability, provisioning status must not be inferred only from notifications. Add a dedicated persisted provisioning-attempt record, for example a `phone_number_provisionings` table keyed by user, that tracks:

- current status
- attempt count
- last error or failure reason
- latest provider candidate metadata when useful
- whether retry is allowed
- linked assigned phone number when provisioning succeeds

This record is the source of truth for onboarding read APIs before a real `phone_numbers` row exists.

#### First-Run Bootstrap

A brand-new synced user must have a real persisted agent-config row before the onboarding flow starts.

The launch path should create a default `agent_configs` row when the user is first created through Clerk sync. As a safety net, the authenticated config read path may also self-heal by creating the default row if it is missing. The product must not rely on a frontend-only fallback object for first-run setup.

#### Agent Setup Gating

Routing enablement must be gated by visible prerequisites.

The app should only allow `Enable routing` when all of the following are true:

- subscription is active
- assigned phone number exists
- provisioning is complete
- required agent setup fields are complete under one deterministic rule:
  - `agent_name.trim()` is non-empty and is not the untouched default placeholder value
  - `owner_context.trim()` is non-empty
  - at least one of `system_prompt.trim()` or `knowledge_base.trim()` is non-empty

This removes the current product flaw where users can attempt to enable routing and only then discover a backend precondition is missing.

The backend contract must enforce the same gate, not only the UI. When `PATCH /api/agent/config` attempts to turn `is_enabled` on, the backend should reject the request unless:

- the active subscription exists
- the assigned number exists and provisioning status is successful
- the required setup fields are complete

The current phone-number-only guard is insufficient for launch.

Whitespace-only values must count as incomplete in both the frontend and backend gate.

#### Routing State

Once the user reaches `ready_to_enable`, enabling routing should continue to use the existing immediate Telnyx active/disabled switching model. This keeps the product expectation simple: the toggle reflects real telephony state.

### Frontend Design

#### Dashboard Home

The dashboard home should become the single source of truth for customer readiness.

It should add:

- a primary onboarding and status card
- assigned number display
- provisioning progress or failure messaging
- retry action when provisioning failed
- support guidance for unresolved provisioning

The current setup checklist should be expanded so it no longer treats the product as ready based only on config fields.

#### Billing UI

For launch, billing should support one plan only: `starter`.

The billing page should:

- present one clear subscribe action for `starter`
- show active subscription state after checkout
- handle the “subscription active but number still provisioning” case
- avoid exposing multi-plan complexity before it is needed

The backend contract should be aligned with this launch scope. During MVP launch mode, checkout requests for any tier other than `starter` should be rejected or disabled so the public UI, backend behavior, and docs do not drift.

#### Agent UI

The agent settings page should:

- hide `sts` from the launch UI
- keep `stt_llm_tts` as the only launch pipeline
- explain that setup is required before routing can go live
- disable or guard the routing toggle until prerequisites are satisfied

#### Calls UI

The calls surface is already close to MVP. It should remain focused on:

- call list
- call detail
- transcript review
- summary review
- recording access when available

No major scope expansion is needed here before launch.

## API And Contract Additions

The existing contracts are not sufficient for a polished self-serve launch because the frontend cannot clearly render provisioning status yet.

Add the minimum product-facing contract needed to support self-serve onboarding:

- a read surface for onboarding or operational readiness state
- a read surface for assigned number and provisioning status
- a retry action for failed provisioning

These can be added either by:

- extending existing billing and agent reads with onboarding fields, or
- adding a focused onboarding-status endpoint

Recommendation:

Use a focused onboarding-status read model rather than overloading unrelated endpoints. This keeps customer-readiness concerns explicit and easier to evolve.

This read model should be assembled from:

- subscription state and usage state
- the durable provisioning-attempt record
- assigned phone number state when provisioning has succeeded
- persisted agent-config completeness

Minimum fields should include:

- `subscription_status`
- `plan_tier`
- `minutes_remaining`
- `phone_number`
- `phone_number_status`
- `routing_enabled`
- `agent_setup_complete`
- `overall_status`
- `can_retry_provisioning`

Recommended write surface:

- `POST /api/onboarding/retry-provisioning`

This endpoint should only succeed when:

- the user has an active `starter` subscription
- no successful assigned number already exists
- the latest provisioning record is in a retryable failed state

## Error Handling

### Provisioning In Progress

When provisioning is underway:

- do not block dashboard access
- show a non-fatal in-progress state
- tell the user to check back shortly

### Provisioning Failure

When provisioning fails:

- preserve subscription state
- show a clear failure message
- offer manual retry
- show contact-support guidance if retry does not resolve the issue

### Routing Guardrails

If the user attempts to enable routing before prerequisites are met:

- the UI should prevent the action
- the backend should still return a clear failure if called directly

### Provider Failure

Provider-side failures should not leave the app in an ambiguous state. Persisted onboarding status must remain consistent with the actual external system state as far as the app can determine.

## Testing Strategy

Add or extend tests for:

- Stripe activation enqueues automatic France provisioning
- successful provisioning persists assigned number and ready state
- provisioning failure persists customer-visible failed state
- manual retry re-enqueues or re-runs provisioning correctly
- dashboard read model reflects all onboarding states
- routing toggle is disabled or rejected before prerequisites are met
- `sts` is not exposed in the launch UI
- one-plan billing UI and checkout flow
- end-to-end first-call persistence with transcript, summary, recording metadata, and minute deduction

## Acceptance Criteria

This work is complete when all of the following are true:

1. A new France-based customer can sign up and subscribe without staff intervention.
2. Successful subscription activation automatically starts phone provisioning.
3. The app automatically provisions a French number when provider conditions allow.
4. The dashboard shows provisioning progress, success, or failure clearly.
5. The assigned French number is visible once provisioning succeeds.
6. The customer can manually retry provisioning after failure.
7. The customer must complete required setup before routing can go live.
8. The routing toggle cannot succeed while hidden prerequisites are missing.
9. The only launch pipeline exposed in the UI is `stt_llm_tts`.
10. The only launch billing option exposed in the UI is the single paid plan.
11. One real inbound call for a self-serve customer produces call record, transcript, summary, recording access when available, and usage deduction.

## Rollout Notes

- Treat this as the launch contract, not an intermediate experiment.
- Hide or remove UI choices that are not launch-ready.
- Prefer explicit state modeling over trying to infer readiness from existing scattered fields.
- Do not call the product self-serve until the full customer path is manually verified end to end with real provider credentials.
