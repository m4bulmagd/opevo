# Workspace Shell Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align every authenticated page with the workspace header’s outer desktop edges while preserving the existing smaller-screen insets.

**Architecture:** Keep alignment ownership in the shared `WorkspaceShell`. Remove only the main element’s desktop width constraint and horizontal inset; route components, the header, and the command rail retain their existing responsibilities.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4, Vitest, Testing Library

## Global Constraints

- Modify only the authenticated workspace shell and its existing layout test.
- Preserve mobile and tablet horizontal padding.
- Preserve vertical padding, content gaps, route composition, API behavior, navigation behavior, and visual tokens.
- Keep the existing 16-pixel desktop shell gap from the command rail and right viewport edge.
- Do not modify `Opevo_frontend`.

---

## File Map

- Modify `apps/web/src/components/workspace/workspace-shell.tsx`: own the authenticated content frame’s responsive width and padding.
- Modify `apps/web/tests/app/app-shell.test.tsx`: lock the main frame’s full-width desktop alignment and responsive inset contract.

### Task 1: Align the Authenticated Main Frame

**Files:**

- Modify: `apps/web/tests/app/app-shell.test.tsx:325-363`
- Modify: `apps/web/src/components/workspace/workspace-shell.tsx:39-45`

**Interfaces:**

- Consumes: the existing `main#workspace-main` element rendered by `WorkspaceShell`.
- Produces: a full-width main frame with `px-4`, `sm:px-6`, `md:px-8`, and `lg:px-0`.

- [ ] **Step 1: Write the failing layout contract**

In the existing `"exposes labelled desktop and mobile shell compositions"` test, query the main frame and add these assertions after the `workspaceContent` assertions:

```tsx
const workspaceMain = view.container.querySelector("#workspace-main");
expect(workspaceMain).toHaveClass(
  "flex",
  "w-full",
  "px-4",
  "sm:px-6",
  "md:px-8",
  "lg:px-0",
);
expect(workspaceMain).not.toHaveClass("mx-auto", "max-w-7xl", "lg:px-10");
```

- [ ] **Step 2: Run the focused test and verify that it fails**

Run:

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
```

Expected: the shell composition test fails because `#workspace-main` still has
`mx-auto`, `max-w-7xl`, and `lg:px-10`, and does not yet have `lg:px-0`.

- [ ] **Step 3: Implement the minimal responsive alignment change**

Replace the `main` element’s class string in
`apps/web/src/components/workspace/workspace-shell.tsx` with:

```tsx
className="flex w-full flex-col gap-5 px-4 py-5 sm:px-6 md:gap-7 md:px-8 md:py-8 lg:px-0"
```

This keeps the mobile and tablet insets and removes the desktop centering,
maximum width, and horizontal inset.

- [ ] **Step 4: Run focused verification**

Run:

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
```

Expected: all tests in `app-shell.test.tsx` pass.

- [ ] **Step 5: Run static verification**

Run:

```bash
cd apps/web
npm run typecheck
npm run check
```

Expected: TypeScript and Biome both pass.

- [ ] **Step 6: Review the final diff**

Run:

```bash
git diff --check
git diff -- apps/web/src/components/workspace/workspace-shell.tsx apps/web/tests/app/app-shell.test.tsx
```

Expected: no whitespace errors; the diff contains only the responsive main
frame class change and its focused regression assertions.

- [ ] **Step 7: Commit the implementation**

```bash
git add apps/web/src/components/workspace/workspace-shell.tsx apps/web/tests/app/app-shell.test.tsx
git commit -m "fix(web): align workspace header and main content"
```
