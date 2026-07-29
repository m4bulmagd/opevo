# Workspace Header Caller Status Design

**Date:** 2026-07-29

**Status:** Approved

**Scope:** Authenticated workspace header and Agent navigation label in
`apps/web`

## Summary

Recompose the desktop workspace header in this order:

1. call search;
2. caller status;
3. Live call;
4. notifications;
5. Call history;
6. account or environment control;
7. theme control.

The caller status presents the current active caller when a call snapshot is
available and a stable ready state at other times. The Agent configuration
destination uses the fixed label **Agent** in desktop and mobile navigation,
while the customer's configured agent name remains visible in runtime status
and Agent-page context.

## Caller Status

### Active call

Use this identity fallback order:

1. matched contact name;
2. formatted caller phone number;
3. `Unknown caller`.

The supporting line reads:

`{agent_name} is answering this call`

A matched name may provide initials in the status avatar. Number-only,
unknown-caller, and idle states use a phone icon rather than invented
initials.

### No active call

Render a stable status block instead of removing it:

- primary line: `No active call`;
- supporting line: `{agent_name} is ready`.

This prevents the desktop controls from shifting as call state changes.

### Lookup failure

An active-call lookup failure must not break the authenticated shell. Render
the ready state and leave existing page data and navigation available.

## Data Flow and Future Contacts Seam

The authenticated dashboard layout requests the newest in-progress call from
the existing call-history API:

```ts
listCalls({ limit: 1, status: "in_progress" })
```

The API already returns an optional `caller_number`. The layout maps the first
result into a small optional caller-identity value for `WorkspaceShell` and
`WorkspaceHeader`. The presentation value supports both an optional contact
name and optional phone number, but this change does not add a contact-name
field to the existing call API or fabricate a contact match.

The future contacts directory can enrich this same identity boundary with a
matched name after contact upload and normalized-number matching are
implemented. Contact storage, upload, matching, and management UI are outside
this change.

The snapshot is server-rendered when the authenticated layout loads. This
change does not introduce browser polling, WebSockets, or a claim of realtime
updates.

## Header Composition and Responsive Behavior

On wide desktop screens, the complete order is:

`Search → Caller status → Live call → Notifications → Call history → Account/environment → Theme`

The header keeps the existing mobile navigation trigger and Presvo identity.
At narrower breakpoints, search and caller details may hide progressively so
the navigation trigger and action controls remain usable without horizontal
overflow. Their wide-screen order must not change.

All interactive controls keep accessible names, keyboard focus treatment, and
minimum touch targets. The caller status is informative rather than
interactive.

## Navigation Naming

The `/dashboard/agent` destination title becomes `Agent` in both desktop and
mobile workspace navigation.

The configured agent name remains the source for:

- the header status copy;
- the sidebar runtime card;
- Agent page context;
- other customer-facing runtime descriptions.

Blank configured names continue to normalize to `Receptionist` where a runtime
name is required.

## Failure Boundaries

- An active-call lookup error degrades to the ready state.
- An empty in-progress result renders the ready state.
- A call with no matched name renders its formatted number.
- A call with neither name nor number renders `Unknown caller`.
- Header failure handling does not suppress account, agent, or route content.

## Verification

- Add a red-green shell test for active caller name, number fallback, unknown
  fallback, ready state, lookup failure, and exact wide-header DOM order.
- Update navigation model and shell tests to require the fixed `Agent` label on
  desktop and mobile.
- Preserve the existing header route, Preview, account-control, responsive,
  keyboard, and accessibility contracts.
- Run the focused shell and navigation tests, TypeScript, Biome, and the full
  web test suite.
