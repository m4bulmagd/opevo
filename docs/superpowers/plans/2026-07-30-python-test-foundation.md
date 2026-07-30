# Python Test Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Parent decision record:** [Agent/API Architecture and Engineering Review Decision Record](../../engineering/2026-07-30-agent-api-review-decisions.md)

**Goal:** Make Python 3.13 the explicit API/agent runtime contract, fix the agent observability cancellation hang, bound every Python test, and establish independently enforced line and branch coverage ratchets from measured baselines.

**Architecture:** Keep runtime declarations and pytest configuration inside each independently locked Python application, with one repository-level Python pin for local tooling. Keep the cancellation fix local to the agent observability lifecycle. Reuse one small, standard-library coverage-gate CLI across both applications so line and branch thresholds are explicit without duplicating comparison logic. Generate baseline files only from complete CI-equivalent suites; CI may check them but may never initialize or overwrite them.

**Tech Stack:** CPython 3.13, uv 0.11.19, pytest 9, pytest-timeout 2.4, pytest-cov 7.1, coverage.py, AnyIO, GitHub Actions, PostgreSQL 17, Redis 7.

## Global Constraints

- This wave implements approved decisions **9A** and **10A only**.
- Do not change contracts, authentication, outbox behavior, LiveKit versions, transcript behavior, realtime behavior, or production performance settings.
- The only production behavior change is the bounded, cancellation-safe agent observability shutdown.
- Follow strict RED → GREEN → REFACTOR for the cancellation bug and the coverage checker.
- Do not add coverage exclusions merely to increase a percentage. Any new `# pragma: no cover`, omit rule, or excluded file requires separate review and a concrete untestability justification.
- Do not guess coverage thresholds. Initialize them from complete CI-equivalent runs after the cancellation fix passes on Python 3.13.
- Preserve decision **11C**: credentialed LiveKit behavior evaluations remain manual. Give those tests a longer explicit timeout so the global unit-test deadline does not disable them.
- Preserve decision **12C**: do not add a real agent process to E2E in this wave.
- Keep focused local pytest commands free of global coverage enforcement. Coverage gates run only on complete app suites.
- Do not edit or stage the existing untracked `Presvo_frontend/` tree.
- Do not commit until implementation is explicitly authorized. The commit commands below are checkpoints for the later implementation turn.

---

## Current Evidence and Intended File Structure

Current mismatches:

- `apps/api/pyproject.toml` and `apps/agent/pyproject.toml` declare Python `>=3.11`.
- Ruff, mypy, both Dockerfiles, and CI already target Python 3.13.
- The existing ignored API and agent virtual environments are Python 3.12.13, which allowed the local verification runtime to drift.
- Neither app currently installs `pytest-timeout` or `pytest-cov`.
- `apps/agent/tests/test_observability.py::test_cancelled_shutdown_finishes_cleanup_before_allowing_reinitialization` can spin indefinitely after cancellation because the task suppresses `CancelledError` without consuming its cancellation request before awaiting again.
- CI has only job-level timeouts and no line or branch coverage gate.

Files created:

```text
.python-version
scripts/check_python_coverage.py
apps/api/coverage-baseline.json
apps/api/tests/tooling/test_python_coverage_gate.py
apps/agent/coverage-baseline.json
docs/superpowers/plans/2026-07-30-python-test-foundation.md
```

Files modified:

```text
.gitignore
.github/workflows/ci.yml
CONTRIBUTING.md
apps/api/pyproject.toml
apps/api/uv.lock
apps/agent/agent/observability.py
apps/agent/pyproject.toml
apps/agent/tests/evals/test_receptionist_behavior.py
apps/agent/tests/test_observability.py
apps/agent/uv.lock
docs/engineering/ci-and-branch-protection.md
```

The two baseline JSON files contain measured numbers produced during Task 4. They are intentionally data, not duplicated logic.

---

## Task 1: Make Python 3.13 and bounded pytest execution explicit

**Files:**

- Create: `.python-version`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Modify: `apps/agent/pyproject.toml`
- Modify: `apps/agent/uv.lock`
- Modify: `apps/agent/tests/evals/test_receptionist_behavior.py`

- [ ] **Step 1: Pin the repository's local Python selection**

Create `.python-version` with exactly:

```text
3.13
```

This is a developer-tool pin, while each app's `requires-python` remains the authoritative package constraint.

- [ ] **Step 2: Narrow both application package contracts**

In both `apps/api/pyproject.toml` and `apps/agent/pyproject.toml`, replace:

```toml
requires-python = ">=3.11"
```

with:

```toml
requires-python = ">=3.13,<3.14"
```

The upper bound prevents an unreviewed Python 3.14 adoption while allowing 3.13 patch updates.

- [ ] **Step 3: Add the maintained test plugins to both dev groups**

Use uv rather than editing lockfiles by hand:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv add --dev --python 3.13 \
  'pytest-cov>=7.1,<8' \
  'pytest-timeout>=2.4,<3'

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv add --dev --python 3.13 \
  'pytest-cov>=7.1,<8' \
  'pytest-timeout>=2.4,<3'
```

The ranges use the current major versions and leave patch/minor security fixes available inside each committed uv lockfile.

- [ ] **Step 4: Require the plugins and set app-appropriate default deadlines**

Keep the API's existing `testpaths` and add:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
required_plugins = [
  "pytest-cov>=7.1,<8",
  "pytest-timeout>=2.4,<3",
]
timeout = 60
```

Keep the agent's existing `livekit_eval` marker and add:

```toml
[tool.pytest.ini_options]
markers = [
  "livekit_eval: credentialed LiveKit model behavior evaluation",
]
required_plugins = [
  "pytest-cov>=7.1,<8",
  "pytest-timeout>=2.4,<3",
]
timeout = 30
```

Do not set `timeout_method`. The plugin's platform-aware default uses signals on supported main-thread POSIX runs and retains its fallback behavior elsewhere. The timeout must cover setup and teardown as well as the test body, because leaked fixtures are also hangs.

- [ ] **Step 5: Preserve the slower manual LiveKit evaluation path**

In `apps/agent/tests/evals/test_receptionist_behavior.py`, add a module-level timeout marker to the existing `pytestmark` list:

```python
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.livekit_eval,
    pytest.mark.timeout(180),
    # existing skipif marker follows
]
```

This does not enable the evaluations in CI and does not change decision 11C. It only prevents the 30-second unit-test default from making an explicitly credentialed manual run unusable.

- [ ] **Step 6: Recreate or select Python 3.13 environments and verify the contract**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv sync --python 3.13 --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -c \
  'import sys; assert sys.version_info[:2] == (3, 13), sys.version'
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync pytest --trace-config \
  2>&1 | rg 'pytest_cov|pytest_timeout'

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv sync --python 3.13 --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -c \
  'import sys; assert sys.version_info[:2] == (3, 13), sys.version'
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync pytest --trace-config \
  2>&1 | rg 'pytest_cov|pytest_timeout'
```

Expected: both version assertions pass and both plugin names appear for both apps. If dependency installation requires network access, request approval rather than weakening or bypassing the frozen environment.

- [ ] **Step 7: Checkpoint the toolchain change**

```bash
git add .python-version \
  apps/api/pyproject.toml apps/api/uv.lock \
  apps/agent/pyproject.toml apps/agent/uv.lock \
  apps/agent/tests/evals/test_receptionist_behavior.py
git commit -m "test: standardize Python 3.13 and bound pytest"
```

---

## Task 2: Fix cancellation-safe observability shutdown with the existing regression

**Files:**

- Modify: `apps/agent/tests/test_observability.py`
- Modify: `apps/agent/agent/observability.py`

- [ ] **Step 1: Put a focused deadline on the regression**

Place `@pytest.mark.timeout(2)` immediately above the existing
`@pytest.mark.anyio` decorator on
`test_cancelled_shutdown_finishes_cleanup_before_allowing_reinitialization`.
Retain the test body unchanged; its existing assertions already observe the
required behavior.

```python
@pytest.mark.timeout(2)
@pytest.mark.anyio
```

Those assertions cover:

- the original shutdown surfaces `CancelledError`;
- provider shutdown still happens exactly once;
- a fresh adapter cannot replace the old one until cleanup completes;
- reinitialization succeeds after cleanup.

Do not replace these assertions with checks of private cancellation counters.

- [ ] **Step 2: Run the regression and observe RED**

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_observability.py::test_cancelled_shutdown_finishes_cleanup_before_allowing_reinitialization \
  -vv
```

Expected before the production fix: pytest-timeout fails the test after two seconds. Confirm the failure is the repeated post-cancellation await, not an import, fixture, or dependency error.

- [ ] **Step 3: Consume suppressed cancellation requests while cleanup is shielded**

In `shutdown_observability`, retain the shielded cleanup task and explicit re-raise, but consume each cancellation request when it is temporarily suppressed:

```python
    cleanup_task = asyncio.create_task(
        _close_adapter(adapter, timeout_seconds=timeout_seconds)
    )
    current_task = asyncio.current_task()
    cancellation: asyncio.CancelledError | None = None
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
            if current_task is not None:
                current_task.uncancel()

    cleanup_task.result()
    if cancellation is not None:
        raise cancellation
```

Why this shape:

- `shield` prevents the caller's cancellation from cancelling provider cleanup.
- `uncancel()` is required when deliberately suppressing cancellation and then awaiting again on Python 3.13.
- preserving the first `CancelledError` keeps the caller-visible cancellation contract.
- the loop still tolerates another cancellation request while cleanup is in progress.
- `cleanup_task.result()` still propagates an unexpected internal cleanup defect.

Do not move provider shutdown back onto the event-loop thread and do not permit reinitialization before the old provider's actions return.

- [ ] **Step 4: Run the focused test and observe GREEN**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_observability.py::test_cancelled_shutdown_finishes_cleanup_before_allowing_reinitialization \
  -vv
```

Expected: PASS in well under two seconds.

- [ ] **Step 5: Run the complete observability lifecycle tests**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_observability.py -q
```

Expected: all tests pass. In particular, the timed-out-provider test must still keep reinitialization blocked until the provider thread returns.

- [ ] **Step 6: Perform the mutation check**

Verify mentally and, if necessary, temporarily mutate locally:

- removing `uncancel()` makes the focused test hit its two-second timeout;
- removing `shield()` lets cancellation abort cleanup and breaks the shutdown assertion;
- resetting initialization before the provider completes breaks the reinitialization assertions;
- omitting the final `raise cancellation` breaks the `pytest.raises(CancelledError)` assertion.

Revert every temporary mutation before continuing.

- [ ] **Step 7: Checkpoint the bug fix**

```bash
git add apps/agent/agent/observability.py apps/agent/tests/test_observability.py
git commit -m "fix(agent): finish telemetry cleanup after cancellation"
```

---

## Task 3: Build one independently tested line/branch coverage checker

**Files:**

- Create: `apps/api/tests/tooling/test_python_coverage_gate.py`
- Create: `scripts/check_python_coverage.py`

The checker has two commands:

```text
check_python_coverage.py initialize --report REPORT --baseline BASELINE
check_python_coverage.py check      --report REPORT --baseline BASELINE
```

`initialize` exclusively creates a baseline and refuses to overwrite one. `check` compares current unrounded line and branch percentages against the stored two-decimal minimums.

- [ ] **Step 1: Write CLI behavior tests first**

Create `apps/api/tests/tooling/test_python_coverage_gate.py` with real subprocess tests. Use literal fixtures; do not import or duplicate the checker's percentage implementation.

```python
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_python_coverage.py"


def _write_report(
    path: Path,
    *,
    covered_lines: int = 9,
    num_statements: int = 10,
    covered_branches: int = 3,
    num_branches: int = 4,
) -> None:
    path.write_text(
        json.dumps(
            {
                "totals": {
                    "covered_lines": covered_lines,
                    "num_statements": num_statements,
                    "covered_branches": covered_branches,
                    "num_branches": num_branches,
                }
            }
        ),
        encoding="utf-8",
    )


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_initialize_writes_measured_line_and_branch_minimums(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)

    result = _run(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0
    assert json.loads(baseline.read_text(encoding="utf-8")) == {
        "minimum_line_percent": "90.00",
        "minimum_branch_percent": "75.00",
    }


def test_initialize_refuses_to_replace_a_reviewed_baseline(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)
    baseline.write_text('{"reviewed": true}', encoding="utf-8")

    result = _run(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"reviewed": True}
    assert "already exists" in result.stderr


@pytest.mark.parametrize(
    ("report_counts", "expected_error"),
    [
        (
            {
                "covered_lines": 8,
                "num_statements": 10,
                "covered_branches": 3,
                "num_branches": 4,
            },
            "line coverage 80.00% is below 90.00%",
        ),
        (
            {
                "covered_lines": 9,
                "num_statements": 10,
                "covered_branches": 2,
                "num_branches": 4,
            },
            "branch coverage 50.00% is below 75.00%",
        ),
    ],
)
def test_check_rejects_each_independent_coverage_regression(
    tmp_path: Path,
    report_counts: dict[str, int],
    expected_error: str,
) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "minimum_line_percent": "90.00",
                "minimum_branch_percent": "75.00",
            }
        ),
        encoding="utf-8",
    )
    _write_report(report, **report_counts)

    result = _run(
        "check",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_check_accepts_coverage_at_the_reviewed_minimum(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)
    baseline.write_text(
        json.dumps(
            {
                "minimum_line_percent": "90.00",
                "minimum_branch_percent": "75.00",
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        "check",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0
    assert "line=90.00%" in result.stdout
    assert "branch=75.00%" in result.stdout


@pytest.mark.parametrize(
    "totals",
    [
        {},
        {
            "covered_lines": 0,
            "num_statements": 0,
            "covered_branches": 0,
            "num_branches": 0,
        },
        {
            "covered_lines": True,
            "num_statements": 10,
            "covered_branches": 3,
            "num_branches": 4,
        },
    ],
)
def test_initialize_rejects_malformed_or_empty_reports(
    tmp_path: Path,
    totals: dict[str, object],
) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    report.write_text(json.dumps({"totals": totals}), encoding="utf-8")

    result = _run(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert not baseline.exists()
    assert "invalid coverage report" in result.stderr


def test_check_rejects_a_boolean_baseline_value(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)
    baseline.write_text(
        json.dumps(
            {
                "minimum_line_percent": True,
                "minimum_branch_percent": "75.00",
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        "check",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert "invalid coverage baseline" in result.stderr
```

- [ ] **Step 2: Run the new test file and observe RED**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/tooling/test_python_coverage_gate.py -q
```

Expected: failures because `scripts/check_python_coverage.py` does not exist. The failure proves the tests exercise the real CLI.

- [ ] **Step 3: Implement the smallest shared checker**

Create `scripts/check_python_coverage.py` as a standard-library-only CLI. Keep these interfaces and validations:

```python
#!/usr/bin/env python3
import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Sequence


_TWO_DECIMAL_PLACES = Decimal("0.01")


class CoverageDataError(ValueError):
    pass


@dataclass(frozen=True)
class CoveragePercentages:
    line: Decimal
    branch: Decimal


def _load_json_object(path: Path, *, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CoverageDataError(f"invalid {description}: {error}") from error
    if not isinstance(value, dict):
        raise CoverageDataError(f"invalid {description}: expected a JSON object")
    return value


def _required_count(values: dict[str, Any], key: str) -> int:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageDataError(f"invalid coverage report: {key} must be a non-negative integer")
    return value


def _percentage(*, covered: int, total: int, label: str) -> Decimal:
    if total <= 0:
        raise CoverageDataError(f"invalid coverage report: {label} total must be positive")
    if covered > total:
        raise CoverageDataError(f"invalid coverage report: covered {label} exceeds total")
    return Decimal(covered) * Decimal(100) / Decimal(total)


def load_report(path: Path) -> CoveragePercentages:
    document = _load_json_object(path, description="coverage report")
    totals = document.get("totals")
    if not isinstance(totals, dict):
        raise CoverageDataError("invalid coverage report: totals must be a JSON object")
    covered_lines = _required_count(totals, "covered_lines")
    num_statements = _required_count(totals, "num_statements")
    covered_branches = _required_count(totals, "covered_branches")
    num_branches = _required_count(totals, "num_branches")
    return CoveragePercentages(
        line=_percentage(
            covered=covered_lines,
            total=num_statements,
            label="line",
        ),
        branch=_percentage(
            covered=covered_branches,
            total=num_branches,
            label="branch",
        ),
    )


def _required_percent(values: dict[str, Any], key: str) -> Decimal:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CoverageDataError(f"invalid coverage baseline: {key}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise CoverageDataError(f"invalid coverage baseline: {key}") from error
    if not parsed.is_finite() or not Decimal(0) <= parsed <= Decimal(100):
        raise CoverageDataError(f"invalid coverage baseline: {key}")
    return parsed


def load_baseline(path: Path) -> CoveragePercentages:
    document = _load_json_object(path, description="coverage baseline")
    return CoveragePercentages(
        line=_required_percent(document, "minimum_line_percent"),
        branch=_required_percent(document, "minimum_branch_percent"),
    )


def _display(value: Decimal) -> str:
    return f"{value.quantize(_TWO_DECIMAL_PLACES):.2f}"


def initialize(report_path: Path, baseline_path: Path) -> int:
    measured = load_report(report_path)
    baseline = {
        "minimum_line_percent": format(
            measured.line.quantize(_TWO_DECIMAL_PLACES, rounding=ROUND_DOWN),
            ".2f",
        ),
        "minimum_branch_percent": format(
            measured.branch.quantize(_TWO_DECIMAL_PLACES, rounding=ROUND_DOWN),
            ".2f",
        ),
    }
    try:
        with baseline_path.open("x", encoding="utf-8") as baseline_file:
            json.dump(baseline, baseline_file, indent=2)
            baseline_file.write("\n")
    except FileExistsError as error:
        raise CoverageDataError(
            f"coverage baseline already exists: {baseline_path}"
        ) from error
    except OSError as error:
        raise CoverageDataError(
            f"cannot create coverage baseline: {error}"
        ) from error
    print(
        f"initialized line={baseline['minimum_line_percent']}% "
        f"branch={baseline['minimum_branch_percent']}%"
    )
    return 0


def check(report_path: Path, baseline_path: Path) -> int:
    measured = load_report(report_path)
    minimum = load_baseline(baseline_path)
    failures: list[str] = []
    if measured.line < minimum.line:
        failures.append(
            f"line coverage {_display(measured.line)}% is below "
            f"{_display(minimum.line)}%"
        )
    if measured.branch < minimum.branch:
        failures.append(
            f"branch coverage {_display(measured.branch)}% is below "
            f"{_display(minimum.branch)}%"
        )
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(
        f"coverage line={_display(measured.line)}% "
        f"(minimum {_display(minimum.line)}%), "
        f"branch={_display(measured.branch)}% "
        f"(minimum {_display(minimum.branch)}%)"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("initialize", "check"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--report", required=True, type=Path)
        subparser.add_argument("--baseline", required=True, type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    try:
        if parsed.command == "initialize":
            return initialize(parsed.report, parsed.baseline)
        return check(parsed.report, parsed.baseline)
    except CoverageDataError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

During implementation, format long lines with Ruff without changing the specified behavior.

- [ ] **Step 4: Run the checker tests and observe GREEN**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/tooling/test_python_coverage_gate.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  tests/tooling/test_python_coverage_gate.py ../../scripts/check_python_coverage.py
```

Expected: all checker behavior tests and Ruff pass.

- [ ] **Step 5: Mutation-check the independent gates**

Verify the tests fail independently if:

- `check()` stops comparing line coverage;
- `check()` stops comparing branch coverage;
- `initialize()` opens the baseline in overwrite mode;
- booleans are accepted as integer counts;
- zero branch totals are treated as valid branch coverage.

- [ ] **Step 6: Checkpoint the reusable gate**

```bash
git add scripts/check_python_coverage.py \
  apps/api/tests/tooling/test_python_coverage_gate.py
git commit -m "test: add independent Python coverage gates"
```

---

## Task 4: Measure and commit honest API and agent coverage baselines

**Files:**

- Modify: `.gitignore`
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/coverage-baseline.json`
- Modify: `apps/agent/pyproject.toml`
- Create: `apps/agent/coverage-baseline.json`

- [ ] **Step 1: Configure coverage collection without forcing it on focused tests**

Add to `apps/api/pyproject.toml`:

```toml
[tool.coverage.run]
branch = true
relative_files = true
source = ["app"]

[tool.coverage.report]
precision = 2
show_missing = true
skip_covered = false
```

Add the same sections to `apps/agent/pyproject.toml`, changing only:

```toml
source = ["agent"]
```

Do not put `--cov` in pytest `addopts`; a focused test should not fail because it does not cover the entire application.

- [ ] **Step 2: Ignore local coverage artifacts, not reviewed baselines**

Add to `.gitignore`:

```gitignore
.coverage
coverage.json
htmlcov/
```

Do not ignore `coverage-baseline.json`.

- [ ] **Step 3: Run the complete API suite against PostgreSQL and Redis**

Start the isolated services exactly as documented in `CONTRIBUTING.md`, then:

```bash
cd apps/api
export APP_ENV=test
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/ai_call_test
export REDIS_URL=redis://127.0.0.1:6379/0
export TEST_REDIS_URL=redis://127.0.0.1:6379/0
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
```

Expected: the full API suite passes, PostgreSQL-dependent tests do not skip for missing services, and `coverage.json` contains non-zero line and branch totals.

- [ ] **Step 4: Exclusively initialize and verify the API baseline**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py initialize \
  --report coverage.json \
  --baseline coverage-baseline.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

Expected: initialization creates the file once, and the immediate check passes. Review the two generated numeric strings; do not round them upward and do not lower them manually.

- [ ] **Step 5: Run the complete agent suite on Python 3.13**

```bash
cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=agent \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
```

Expected: all non-credentialed agent tests pass, the four credentialed LiveKit evaluations skip, the cancellation regression completes within two seconds, and `coverage.json` contains non-zero line and branch totals.

- [ ] **Step 6: Exclusively initialize and verify the agent baseline**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py initialize \
  --report coverage.json \
  --baseline coverage-baseline.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

Expected: initialization creates the file once and the immediate check passes.

- [ ] **Step 7: Verify the baseline cannot hide a regression**

Create temporary copies under `/tmp`, lower one covered-line count in the API report and one covered-branch count in the agent report enough to cross their respective baselines, and run `check` against the temporary reports. Each command must exit 1 with the correct dimension in its error. Do not edit the real reports or reviewed baselines for this proof.

- [ ] **Step 8: Checkpoint measured baselines**

```bash
git add .gitignore \
  apps/api/pyproject.toml apps/api/coverage-baseline.json \
  apps/agent/pyproject.toml apps/agent/coverage-baseline.json
git commit -m "test: establish Python coverage baselines"
```

---

## Task 5: Enforce the gates in CI and document the local workflow

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/engineering/ci-and-branch-protection.md`

- [ ] **Step 1: Collect and check API coverage in CI**

Replace the API pytest step with:

```yaml
      - name: Tests and coverage on PostgreSQL
        run: >-
          uv run --frozen --no-sync python -m pytest -q
          --cov=app
          --cov-report=term-missing
          --cov-report=json:coverage.json
      - name: Enforce API line and branch coverage
        run: >-
          uv run --frozen --no-sync python
          ../../scripts/check_python_coverage.py check
          --report coverage.json
          --baseline coverage-baseline.json
```

Keep the PostgreSQL and Redis service configuration unchanged.

- [ ] **Step 2: Collect and check agent coverage in CI**

Replace the agent pytest step with:

```yaml
      - name: Tests and coverage
        run: >-
          uv run --frozen --no-sync python -m pytest -q
          --cov=agent
          --cov-report=term-missing
          --cov-report=json:coverage.json
      - name: Enforce agent line and branch coverage
        run: >-
          uv run --frozen --no-sync python
          ../../scripts/check_python_coverage.py check
          --report coverage.json
          --baseline coverage-baseline.json
```

CI must call `check`, never `initialize`.

- [ ] **Step 3: Update contributor commands**

In `CONTRIBUTING.md`:

- keep Python 3.13 and uv 0.11.19 as explicit prerequisites;
- replace the API and agent plain pytest commands with the exact coverage collection and checker commands from Task 4;
- state that focused pytest runs omit coverage flags;
- state that a coverage decrease requires tests, not a lowered baseline;
- state that when coverage increases, the baseline should be raised to the new measured, downward-rounded value in the same change;
- explain that `pytest-timeout` applies 60 seconds per API test and 30 seconds per agent test, with 180 seconds only for manual LiveKit evaluations.

- [ ] **Step 4: Update CI documentation**

In `docs/engineering/ci-and-branch-protection.md`, update the API and agent job descriptions to say:

- both run on Python 3.13;
- every test has a bounded deadline;
- both collect branch-aware coverage;
- line and branch regressions are checked independently against committed measured baselines;
- PostgreSQL-backed API tests remain mandatory;
- the CI jobs never rewrite baselines.

No new required GitHub check names are introduced because the coverage steps remain inside `CI / API` and `CI / Agent`.

- [ ] **Step 5: Check YAML and documentation consistency**

```bash
rg -n "python -m pytest|check_python_coverage|coverage-baseline|timeout" \
  .github/workflows/ci.yml CONTRIBUTING.md \
  docs/engineering/ci-and-branch-protection.md
```

Expected: the API and agent commands agree across CI and contributor docs, and only the manual LiveKit evaluation path documents the 180-second exception.

- [ ] **Step 6: Checkpoint CI and documentation**

```bash
git add .github/workflows/ci.yml CONTRIBUTING.md \
  docs/engineering/ci-and-branch-protection.md
git commit -m "ci: enforce Python test quality gates"
```

---

## Task 6: Complete first-wave verification

**Files:**

- Verify all files listed above

- [ ] **Step 1: Verify both lockfiles and Python runtimes**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -c \
  'import sys; assert sys.version_info[:2] == (3, 13), sys.version'

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -c \
  'import sys; assert sys.version_info[:2] == (3, 13), sys.version'
```

- [ ] **Step 2: Run API static checks and the PostgreSQL-backed coverage suite**

With the documented isolated PostgreSQL and Redis services running:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app tests ../../scripts/check_python_coverage.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

Expected: Ruff, mypy, the complete API suite, and both API coverage dimensions pass. Confirm PostgreSQL integration tests did not skip because `TEST_DATABASE_URL` was absent.

- [ ] **Step 3: Run agent static checks and the complete coverage suite**

```bash
cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  agent tests ../../scripts/check_python_coverage.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=agent \
  --cov-report=term-missing \
  --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
```

Expected: Ruff, mypy, all non-credentialed tests, and both agent coverage dimensions pass; exactly the credential-gated LiveKit evaluation cases remain skipped when their environment is absent.

- [ ] **Step 4: Verify generated artifacts remain untracked**

```bash
cd ../..
git status --short
git check-ignore -v apps/api/.coverage apps/api/coverage.json \
  apps/agent/.coverage apps/agent/coverage.json
git diff --check
```

Expected:

- coverage data files are ignored;
- both `coverage-baseline.json` files are tracked;
- `Presvo_frontend/` is untouched;
- no whitespace errors exist.

- [ ] **Step 5: Review scope and failure semantics**

Confirm from the final diff:

- no app behavior changed outside `apps/agent/agent/observability.py`;
- the cancellation fix still re-raises cancellation after cleanup;
- no coverage exclusions were added;
- line and branch coverage are compared separately;
- CI cannot initialize or lower baselines;
- focused local pytest commands still work without running whole-app coverage;
- timeouts cover fixtures and teardown;
- the manual LiveKit evaluation exception is explicit and limited to that module.

- [ ] **Step 6: Final implementation checkpoint**

If the task was implemented as separate checkpoint commits, inspect them and retain that history. Otherwise:

```bash
git add .python-version .gitignore .github/workflows/ci.yml CONTRIBUTING.md \
  apps/api/pyproject.toml apps/api/uv.lock apps/api/coverage-baseline.json \
  apps/api/tests/tooling/test_python_coverage_gate.py \
  apps/agent/agent/observability.py apps/agent/pyproject.toml \
  apps/agent/tests/evals/test_receptionist_behavior.py \
  apps/agent/tests/test_observability.py apps/agent/uv.lock \
  apps/agent/coverage-baseline.json \
  docs/engineering/ci-and-branch-protection.md \
  scripts/check_python_coverage.py
git commit -m "test: harden Python runtime and coverage gates"
```

---

## Rollback Boundaries

1. **Toolchain rollback:** `.python-version`, both pyprojects, both lockfiles, and the LiveKit evaluation timeout marker can be reverted together without touching runtime behavior.
2. **Cancellation rollback:** the observability production change and its focused test marker form one isolated unit. Do not revert only the test.
3. **Coverage rollback:** the checker, checker tests, coverage configuration, baseline files, CI steps, ignore rules, and documentation form one unit. Removing only CI enforcement would silently invalidate decision 10A.

## Completion Criteria

- Both applications install and run under CPython 3.13 from frozen lockfiles.
- A missing timeout or coverage plugin causes pytest configuration to fail immediately.
- The cancellation regression fails quickly without the fix and passes with it.
- Every API test has a 60-second deadline; every ordinary agent test has a 30-second deadline; manual credentialed LiveKit evaluations have an explicit 180-second deadline.
- Complete API coverage is measured with PostgreSQL and Redis available.
- Complete agent coverage is measured with only the approved credentialed evaluations skipped.
- Line and branch percentages are independently checked against committed, measured baselines.
- Coverage decreases fail CI; baseline decreases are prohibited by review policy.
- Focused test runs remain fast and do not invoke whole-app coverage gates.
- No unrelated code or `Presvo_frontend/` file is changed.
