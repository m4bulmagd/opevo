# Workspace Shell Alignment Design

**Date:** 2026-07-29

**Status:** Approved

**Scope:** Authenticated workspace shell in `apps/web`

## Summary

Align the authenticated workspace header and main content to the same desktop
left and right edges. The content canvas will keep the existing 16-pixel outer
gap from the command rail and viewport edge while using the full available
workspace width.

## Current Problem

`WorkspaceHeader` spans the full column beside the command rail. The main
element in `WorkspaceShell` instead uses `mx-auto`, `max-w-7xl`, and desktop
horizontal padding. On wide screens this centers the page content in a narrower
area, so page introductions and product surfaces no longer align with the
header border.

## Approved Design

- Remove the centered maximum-width constraint from the authenticated main
  element.
- Remove horizontal padding from that element at the desktop `lg` breakpoint.
- Preserve the existing mobile and tablet horizontal padding.
- Preserve vertical padding, vertical gaps, responsive page composition, and
  the 16-pixel shell gap around the desktop command rail and content column.
- Apply the change at the shared `WorkspaceShell` boundary so every
  authenticated page uses the same alignment.

At desktop widths, the header border, page introduction, cards, tables, and
other route content will share the same outer left and right edges. At smaller
breakpoints, the current inset content remains unchanged.

## Implementation Boundary

Modify `apps/web/src/components/workspace/workspace-shell.tsx`. Update the
existing workspace shell test to assert that the main element is full width,
has no maximum-width constraint, and drops horizontal padding at `lg`.

No route data, API calls, navigation behavior, visual tokens, card internals,
or public and activation layouts will change.

## Verification

- Run the focused workspace shell test.
- Run the web typecheck and repository check commands.
- Confirm the desktop shell has matching header and main edges and retains
  responsive padding below the desktop breakpoint.
