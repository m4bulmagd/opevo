# Opevo Landing Page Design

## Goal

Replace the current redirecting root route with a public landing page for `Opevo` that closely follows the provided reference layout while aligning the copy to this product's AI voice assistant workflow.

## Users

- Professional individuals
- Small businesses

Primary jobs:

- understand what the product does in a few seconds
- trust it enough to sign up
- access the dashboard quickly if already authenticated

## Product Direction

The landing page should feel:

- calm
- capable
- polished
- light-first

It should stay visually close to the supplied design reference rather than introducing a new marketing language.

## Route Behavior

- `/` renders the landing page for everyone
- authenticated users see `Dashboard` CTAs
- unauthenticated users see `Log in` and `Sign up` CTAs
- `/dashboard` remains the authenticated application surface

## Composition

The page should include:

- top navigation with brand and auth-aware actions
- hero with soft blue atmospheric background
- feature cards
- operational benefit list
- problem statement section
- blue `How it works` band with three steps
- secondary feature row
- FAQ
- dark footer

## Copy Direction

Use `Opevo` as the brand name and position it as AI voice assistance for professionals and small businesses. Copy should emphasize:

- missed-call prevention
- 24/7 coverage
- fast setup
- summaries and transcripts
- easier follow-up

## Implementation Notes

- replace `apps/web/src/app/page.tsx`
- reuse existing auth session helper
- reuse current Next.js, Tailwind, shadcn, and Clerk setup
- update app metadata and visible brand labels to `Opevo`

## Verification

- root route no longer redirects
- guests see `Log in` and `Sign up`
- authenticated users see `Dashboard`
- web tests, lint, and build run successfully
