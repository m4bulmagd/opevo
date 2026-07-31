# CI and Branch Protection

This document records Presvo's current GitHub Actions checks and the repository
ruleset required for `main`. The workflow file remains authoritative; update
this guide whenever [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
changes.

## Workflow triggers and toolchain

The `CI` workflow runs for every pull request, every push to `main`, and manual
dispatch. Concurrent runs for the same pull request or Git reference cancel an
older in-progress run.

CI uses Ubuntu 24.04, Python 3.13, uv 0.11.19, and Node.js 22.19.0. Third-party
actions are pinned to full commit SHAs. The workflow has read-only repository
contents permission, and checkout steps do not persist credentials.

## Current jobs

- `CI / API` runs on Python 3.13, installs the frozen API dependency graph,
  checks the lockfile, runs Ruff and mypy, and runs the complete PostgreSQL-
  backed pytest suite. PostgreSQL-backed tests remain mandatory. It supplies
  PostgreSQL 17.8 and Redis 7.4.7 with explicit `DATABASE_URL`,
  `TEST_DATABASE_URL`, `REDIS_URL`, and `TEST_REDIS_URL` values so integration
  tests cannot silently skip for lack of test services. Every API test has a
  60-second deadline, including setup and teardown. The job collects
  branch-aware coverage and independently checks line and branch results
  against the committed measured `coverage-baseline.json`; it never rewrites
  that baseline.
- `CI / Agent` runs on Python 3.13, installs the frozen agent dependency graph,
  checks the lockfile, runs Ruff and mypy, and runs the complete pytest suite.
  Every ordinary agent test has a 30-second deadline, including setup and
  teardown; credentialed manual LiveKit evaluations have an explicit
  180-second deadline. The job collects branch-aware coverage and independently
  checks line and branch results against the committed measured
  `coverage-baseline.json`; it never rewrites that baseline.
- `CI / Shared contracts` runs on Python 3.13 in `libs/shared`, checks the
  frozen shared-package lockfile, installs all locked groups, and runs Ruff,
  mypy, and the complete shared-contract pytest suite independently of either
  application.
- `CI / Web` installs with `npm ci`, runs Biome, TypeScript, Vitest, and the
  Next.js production build. Its build uses explicit non-secret Clerk and local
  API/application placeholders; it does not use provider secrets.
- `CI / Migrations` starts a dedicated PostgreSQL 17.8 service with an empty
  `ai_call_migrations` database and runs `alembic -c alembic.ini upgrade head`.
  This blank-database upgrade is required migration verification and must stay
  required even when API tests pass.
- `CI / Dependency audit / api`, `CI / Dependency audit / agent`,
  `CI / Dependency audit / shared`, and `CI / Dependency audit / web` audit
  the committed uv/npm lockfiles. Python exports omit local workspace sources
  before hash auditing; those packages are reviewed directly from repository
  source while every third-party dependency remains hash-audited. The agent's
  five time-limited exceptions are verified exactly as documented in [the
  dependency exception register](../security/dependency-exceptions.md).
- `CI / Gitleaks` scans full Git history with redaction enabled.
- `CI / Docker context hygiene` exports the filtered repository-root context
  and runs the dangling-sentinel and cleanup-ownership safety checks.
- `CI / Container scan / api`, `CI / Container scan / agent`, and
  `CI / Container scan / web` build each application image and fail on fixed
  HIGH or CRITICAL vulnerabilities. API and agent builds use the repository
  root as their Docker context with explicit application Dockerfiles, so their
  non-editable shared-contract installation is part of the tested image.
  Python runtime images also import the shared package before scanning. Only
  the agent image receives the reviewed exceptions in `.trivyignore.yaml`.
- `CI / Required` depends on every job group above and fails unless each group
  completed successfully.

Dependabot checks API, agent, and shared-package uv dependencies, npm, GitHub
Actions, and Docker dependencies weekly.

## Required GitHub ruleset for `main`

After the workflow has completed successfully on the remote default branch,
create an active branch ruleset targeting `main` with all of these settings:

- Require changes through a pull request with at least one approving review.
- Dismiss stale pull-request approvals when new commits are pushed.
- Require all conversations to be resolved before merging.
- Require status checks to pass and require branches to be up to date before
  merging.
- Require linear history.
- Require signed commits.
- Block force pushes and branch deletion.
- Leave the bypass list empty, including for administrators and repository
  roles during beta.

Require every check below. `CI / Required` is the stable aggregate, but it does
not replace the individual migration, dependency, secret, or container checks:

- `CI / API`
- `CI / Agent`
- `CI / Shared contracts`
- `CI / Web`
- `CI / Migrations`
- `CI / Dependency audit / api`
- `CI / Dependency audit / agent`
- `CI / Dependency audit / shared`
- `CI / Dependency audit / web`
- `CI / Gitleaks`
- `CI / Docker context hygiene`
- `CI / Container scan / api`
- `CI / Container scan / agent`
- `CI / Container scan / web`
- `CI / Required`

The ruleset is GitHub configuration, not repository state. Before saving it,
select these exact check names from a successful remote workflow run; do not
type guessed names or substitute job identifiers such as `ci-required`.
Coverage enforcement remains within `CI / API` and `CI / Agent`, so it does not
introduce additional required GitHub check names.

## Migration changes

Every schema change requires an Alembic revision and must preserve the
blank-database `upgrade head` path. Run focused migration tests while
iterating, then confirm `CI / Migrations` succeeds from its dedicated empty
database before merge. Do not rely on an already-migrated development database
as migration proof.
