# Opevo Landing Page Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public landing page at `/` that matches the approved reference structure and swaps auth actions based on server session state.

**Architecture:** Keep the landing page inside the existing `apps/web` Next.js app as the root server component. Read auth state through the current server-session helper, render auth-aware CTAs, and update shared branding metadata to `Opevo`.

**Tech Stack:** Next.js App Router, React 19, Tailwind CSS v4, shadcn/ui, Clerk, Vitest, Testing Library

---

## Chunk 1: Root Route Behavior

### Task 1: Replace redirect coverage with landing-page coverage

**Files:**
- Modify: `apps/web/tests/app/root-page.test.tsx`

- [ ] Step 1: Write failing tests for guest and authenticated landing-page states
- [ ] Step 2: Run `npm run test -- --run tests/app/root-page.test.tsx` from `apps/web` and confirm failure
- [ ] Step 3: Implement the landing page in `apps/web/src/app/page.tsx`
- [ ] Step 4: Re-run `npm run test -- --run tests/app/root-page.test.tsx`

## Chunk 2: Branding And Metadata

### Task 2: Update shared product naming

**Files:**
- Modify: `apps/web/src/config/app-config.ts`
- Modify: `apps/web/src/app/(app)/dashboard/_components/sidebar/app-sidebar.tsx`

- [ ] Step 1: Change visible branding and metadata strings to `Opevo`
- [ ] Step 2: Verify the existing dashboard shell test still passes or remains unaffected

## Chunk 3: Final Verification

### Task 3: Validate the web app

**Files:**
- No additional code changes expected

- [ ] Step 1: Run targeted web tests for the landing page
- [ ] Step 2: Run `npm run lint`
- [ ] Step 3: Run `npm run build`
- [ ] Step 4: Report any remaining risk if a command fails
