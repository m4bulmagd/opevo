# Final review fixes implementation plan

**Goal:** Resolve approved Issues 29A, 30A, 31A, and 32A without weakening the Clerk-first or real-Telnyx work already verified.

**Decisions:**

- 29A: local authentication has no published fallback credential. Every local runtime must provide its credential explicitly.
- 30A: one strict provisioning result drives both activation policy and the web-visible number readiness projection.
- 31A: Clerk sign-out composition lives in a statically imported client leaf that owns exactly one child.
- 32A: the same account/sign-out capability is available in the mobile workspace navigation.

**Constraints:**

- Follow red-green-refactor for every behavior change and record the exact red and green commands.
- Preserve Clerk as the default Compose mode and keep synthetic local authentication test-only and explicit.
- Do not expose credentials in tests, output, or public browser configuration.
- Do not mutate Telnyx, application data, or the retained live runtime state.
- Prefer one canonical predicate/component over parallel near-duplicates.
- Keep server/client boundaries explicit; do not make an async Client Component or pass non-serializable props.

## Task 1: Require an explicit local credential

**Files:**

- Modify `apps/api/app/core/config.py`.
- Modify `apps/api/tests/test_deployment_readiness.py`.
- Modify examples or tests only if the new fail-closed default proves they depend on the published fallback.

Add a regression that constructs local-mode settings without `LOCAL_AUTH_TOKEN`, then proves runtime validation rejects it using only the safe setting name. Observe the test fail because `Settings.local_auth_token` currently supplies the known token. Change the default to empty and keep explicit local test/runtime credentials unchanged. Run focused deployment/auth tests and Ruff.

## Task 2: Canonicalize number readiness

**Files:**

- Modify `apps/api/app/services/activation_snapshot_service.py`.
- Modify `apps/api/tests/activation/test_activation_snapshot_service.py`.
- Modify `apps/web/tests/app/number-milestone.test.tsx` only for a meaningful cross-boundary regression.

Extend the existing mismatched-linkage and blank-provider-ID cases to prove the serialized `provider_ready` field is false as well as the `number_provisioned` milestone being absent. Observe the focused API test fail. Reuse the already-computed strict `number_provisioned` result for `ActivationNumberResponse.provider_ready`; do not introduce a second predicate. Preserve the valid linked/succeeded case. Add or retain a web contract test proving an assigned number with `provider_ready=false` is not presented as ready. Run focused API/web tests, Ruff, and type-check.

## Task 3: Make Clerk sign-out client-owned and responsive

**Files:**

- Add one client-only Clerk sign-out component under `apps/web/src/components/auth/`.
- Modify `apps/web/src/app/(activation)/activate/layout.tsx`.
- Modify `apps/web/src/components/workspace/workspace-header.tsx`.
- Modify `apps/web/src/components/workspace/mobile-workspace-navigation.tsx`.
- Modify the smallest necessary workspace shell/layout plumbing.
- Modify `apps/web/tests/app/activation-page.test.tsx` and `apps/web/tests/app/app-shell.test.tsx`, or add one focused component test when that produces a more realistic Clerk contract.

First add regressions that fail against the current code: Clerk sign-out must be owned by a client leaf with a single child, and the mobile workspace dialog must expose a labelled sign-out/account control. Prefer observable rendered behavior over source-text assertions. Then statically import the client leaf from server components, remove dynamic Clerk imports from those server components, and render an explicit mobile variant in the drawer. Local mode must remain visibly labelled without importing or invoking Clerk. Reuse one component with an explicit visual variant instead of copying sign-out behavior. Run focused web tests, type-check, lint, and a real Clerk-mode runtime log/request smoke if available.

## Verification and review

After each task, use a fresh independent reviewer. Before integration run:

```bash
npm --prefix apps/web run test:ci
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
apps/api/.venv/bin/python -m pytest apps/api/tests/test_deployment_readiness.py apps/api/tests/auth apps/api/tests/activation -q
apps/api/.venv/bin/ruff check apps/api/app apps/api/tests
git diff --check
```

Keep Issues 33, 34, and the recorded minor documentation findings out of this implementation until the owner chooses their directions.
