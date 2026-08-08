# Python Test Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Parent decision record:** [Agent/API Architecture and Engineering Review Decision Record](../../engineering/2026-07-30-agent-api-review-decisions.md)

**Goal:** Make Python 3.13 the explicit API/agent runtime contract, stabilize the agent observability cancellation regression test, bound every Python test, and establish independently enforced line and branch coverage ratchets from measured baselines.

**Architecture:** Keep runtime declarations and pytest configuration inside each independently locked Python application, with one repository-level Python pin for local tooling. Apply approved amendment **9A-1R** by stabilizing the focused cancellation regression test while leaving production observability unchanged. Reuse one small, standard-library coverage-gate CLI across both applications so line and branch thresholds are explicit without duplicating comparison logic. Generate baseline files only from complete CI-equivalent suites; CI may check them but may never initialize or overwrite them.

**Tech Stack:** CPython 3.13, uv 0.11.19, pytest 9, pytest-timeout 2.4, pytest-cov 7.1, coverage.py, AnyIO, GitHub Actions, PostgreSQL 17, Redis 7.

## Global Constraints

- This wave implements approved decisions **9A-1R** and **10A only**.
- Do not change contracts, authentication, outbox behavior, LiveKit versions, transcript behavior, realtime behavior, or production performance settings.
- Do not change production application behavior. The apparent observability
  cancellation bug was a test-teardown artifact, and the proposed production
  patch was rejected.
- Follow strict RED → GREEN → REFACTOR for the coverage checker. Preserve the
  systematic diagnosis and repeated-run evidence for the test-only
  cancellation amendment.
- Do not add coverage exclusions merely to increase a percentage. Any new `# pragma: no cover`, omit rule, or excluded file requires separate review and a concrete untestability justification.
- Do not guess coverage thresholds. Initialize them from complete CI-equivalent
  runs on Python 3.13, and raise them only for repeatable improvements
  attributable to code or test changes.
- Preserve decision **11C**: credentialed LiveKit behavior evaluations remain manual. Give those tests a longer explicit timeout so the global unit-test deadline does not disable them.
- Preserve decision **12C**: do not add a real agent process to E2E in this wave.
- Keep focused local pytest commands free of global coverage enforcement. Coverage gates run only on complete app suites.
- Do not edit or stage the existing untracked `Opevo_frontend/` tree.
- Do not commit until implementation is explicitly authorized. The commit commands below are checkpoints for the later implementation turn.

---

## Initial Evidence and Intended File Structure

Mismatches at plan approval:

- `apps/api/pyproject.toml` and `apps/agent/pyproject.toml` declare Python `>=3.11`.
- Ruff, mypy, both Dockerfiles, and CI already target Python 3.13.
- The existing ignored API and agent virtual environments are Python 3.12.13, which allowed the local verification runtime to drift.
- Neither app currently installs `pytest-timeout` or `pytest-cov`.
- The original focused observability cancellation test used
  `asyncio.to_thread(threading.Event.wait)`. Systematic diagnosis showed that
  the worker-thread wait interacting with AnyIO/`asyncio.Runner` teardown—not
  production cancellation handling—caused the apparent hang.
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
apps/agent/pyproject.toml
apps/agent/tests/evals/test_receptionist_behavior.py
apps/agent/tests/test_observability.py
apps/agent/uv.lock
docs/engineering/2026-07-30-agent-api-review-decisions.md
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
set -o pipefail
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  --trace-config --collect-only 2>&1 | rg 'pytest_cov|pytest_timeout'

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv sync --python 3.13 --frozen --all-groups
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -c \
  'import sys; assert sys.version_info[:2] == (3, 13), sys.version'
set -o pipefail
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  --trace-config --collect-only 2>&1 | rg 'pytest_cov|pytest_timeout'
```

Expected: both version assertions pass, collection succeeds, and both plugin
names appear for both apps. Run each command from its application directory.
`set -o pipefail` is required so a pytest collection failure cannot be hidden by
a successful output filter. If dependency installation requires network
access, request approval rather than weakening or bypassing the frozen
environment.

- [ ] **Step 7: Checkpoint the toolchain change**

```bash
git add .python-version \
  apps/api/pyproject.toml apps/api/uv.lock \
  apps/agent/pyproject.toml apps/agent/uv.lock \
  apps/agent/tests/evals/test_receptionist_behavior.py
git commit -m "test: standardize Python 3.13 and bound pytest"
```

---

## Task 2: Implement approved 9A-1R test-only cancellation stabilization

**Files:**

- Modify: `apps/agent/tests/test_observability.py`

Production observability remains unchanged. Systematic diagnosis superseded
the original production-fix proposal.

- [ ] **Step 1: Put a focused deadline on the regression**

Place `@pytest.mark.timeout(2)` immediately above the existing
`@pytest.mark.anyio` decorator on
`test_cancelled_shutdown_finishes_cleanup_before_allowing_reinitialization`.

```python
@pytest.mark.timeout(2)
@pytest.mark.anyio
```

Those assertions cover:

- the original shutdown surfaces `CancelledError`;
- provider shutdown still happens exactly once;
- reinitialization succeeds after cleanup.

Do not replace these assertions with checks of private cancellation counters.

- [ ] **Step 2: Preserve the systematic diagnosis**

The initial focused test timed out while its startup synchronization awaited
`asyncio.to_thread(threading.Event.wait)`. The worker-thread wait interacting
with AnyIO/`asyncio.Runner` teardown caused the apparent RED. A candidate
production `uncancel()` change did not affect the timeout, so it was rejected.
This evidence supersedes the original production-cancellation diagnosis.

- [ ] **Step 3: Replace thread-backed test synchronization**

Retain the thread event used by the fake synchronous provider, but poll its
condition from the async test with a bounded loop. After cancellation, yield
once so the shutdown task observes cancellation before the provider is
released:

```python
    for _ in range(50):
        if flush_started.is_set():
            break
        await asyncio.sleep(0.01)
    assert flush_started.is_set()
    shutdown_task.cancel()
    await asyncio.sleep(0)
    release_flush.set()
```

- [ ] **Step 4: Prove the focused test is stable**

```bash
cd apps/agent
for run in {1..20}; do
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
    tests/test_observability.py::test_cancelled_shutdown_finishes_cleanup_before_allowing_reinitialization \
    -q || exit 1
done
```

Expected: 20 consecutive passes under the two-second focused deadline.

- [ ] **Step 5: Run the observability and noncredentialed agent suites**

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_observability.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: all 20 observability tests pass, and the noncredentialed agent suite
passes with only the explicitly credential-gated LiveKit evaluations skipped.
The focused test continues to observe cleanup, cancellation propagation, and
reinitialization.

- [ ] **Step 6: Checkpoint the test-only amendment**

```bash
git add apps/agent/tests/test_observability.py
git commit -m "test(agent): stabilize cancellation regression"
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

`initialize` serializes a complete baseline, stages and flushes it in the
destination directory, then atomically installs it without overwriting an
existing baseline. `check` compares current unrounded line and branch
percentages against the stored minimums and expands diagnostic precision only
when two-decimal displays would be contradictory.

- [ ] **Step 1: Write CLI behavior tests first**

Create `apps/api/tests/tooling/test_python_coverage_gate.py` with real
subprocess tests. Use literal fixtures; do not import or duplicate the
checker's percentage implementation. Cover downward rounding of
non-terminating ratios, raw comparison at an equal-looking two-decimal
boundary, and a missing baseline as data-error exit 2. The one staged-install
fault-injection test may import the real checker module to inject an
`os.link()` failure and prove both the final and staging paths are absent.

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

Expected final-review RED: the atomic-install fault test fails because the
checker has no staged no-clobber install seam, and the raw-boundary test exposes
the contradictory `89.54% is below 89.54%` diagnostic. The downward-rounding
and missing-baseline subprocess cases already pass, characterizing existing
correct behavior.

- [ ] **Step 3: Implement the smallest shared checker**

Create `scripts/check_python_coverage.py` as a standard-library-only CLI. Keep these interfaces and validations:

```python
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import tempfile
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


def _regression_displays(measured: Decimal, minimum: Decimal) -> tuple[str, str]:
    measured_display = _display(measured)
    minimum_display = _display(minimum)
    if measured_display != minimum_display:
        return measured_display, minimum_display
    for decimal_places in range(3, 7):
        measured_display = f"{measured:.{decimal_places}f}"
        minimum_display = f"{minimum:.{decimal_places}f}"
        if measured_display != minimum_display:
            return measured_display, minimum_display
    return str(measured), str(minimum)


def _install_baseline_exclusively(path: Path, payload: str) -> None:
    staging_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as staging_file:
            staging_path = Path(staging_file.name)
            staging_file.write(payload)
            staging_file.flush()
            os.fsync(staging_file.fileno())
        os.link(staging_path, path)
    except FileExistsError as error:
        raise CoverageDataError(f"coverage baseline already exists: {path}") from error
    except OSError as error:
        raise CoverageDataError(f"cannot create coverage baseline: {error}") from error
    finally:
        if staging_path is not None:
            try:
                staging_path.unlink(missing_ok=True)
            except OSError as error:
                raise CoverageDataError(
                    f"cannot remove staged coverage baseline: {error}"
                ) from error


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
    payload = json.dumps(baseline, indent=2) + "\n"
    _install_baseline_exclusively(baseline_path, payload)
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
        measured_display, minimum_display = _regression_displays(
            measured.line,
            minimum.line,
        )
        failures.append(
            f"line coverage {measured_display}% is below {minimum_display}%"
        )
    if measured.branch < minimum.branch:
        measured_display, minimum_display = _regression_displays(
            measured.branch,
            minimum.branch,
        )
        failures.append(
            f"branch coverage {measured_display}% is below {minimum_display}%"
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
- `check()` rounds before comparing raw percentages;
- equal-looking raw regressions return a contradictory two-decimal diagnostic;
- `initialize()` rounds a non-terminating percentage upward;
- staged installation overwrites an existing baseline;
- a staged install failure leaves a partial final file or staging file;
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

Baseline raises require repeatable improvements attributable to code or test
changes. Do not raise a baseline from one higher unchanged-code run. The
original genuine API run measured 89.545% line and 76.469% branch, producing
the current downward-rounded 89.54% and 76.46% floors. A later unchanged-code
run measured 89.588% and 76.664%, isolated to alternate
`billing_service.py` paths. Both runs pass the current floors; the stochastic
increase does not authorize a baseline edit.

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

Expected: initialization creates the file once, and the immediate check passes.
Review the two generated numeric strings; do not round them upward and do not
lower them manually. After initialization, raise a committed value only when
the improvement repeats and is attributable to the code or test changes under
review.

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
- state that a baseline is raised to the new measured, downward-rounded value
  in the same change only for a repeatable improvement attributable to that
  change, never for one stochastic higher run;
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
- `Opevo_frontend/` is untouched;
- no whitespace errors exist.

- [ ] **Step 5: Review scope and failure semantics**

Confirm from the final diff:

- no production application behavior changed;
- the test-only 9A-1R amendment retains observable cleanup, cancellation
  propagation, and reinitialization assertions;
- no coverage exclusions were added;
- line and branch coverage are compared separately at raw precision;
- CI cannot initialize or lower baselines;
- baseline raises require repeatable improvements attributable to code or test
  changes;
- focused local pytest commands still work without running whole-app coverage;
- timeouts cover fixtures and teardown;
- the manual LiveKit evaluation exception is explicit and limited to that module.

- [ ] **Step 6: Final implementation checkpoint**

If the task was implemented as separate checkpoint commits, inspect them and retain that history. Otherwise:

```bash
git add .python-version .gitignore .github/workflows/ci.yml CONTRIBUTING.md \
  apps/api/pyproject.toml apps/api/uv.lock apps/api/coverage-baseline.json \
  apps/api/tests/tooling/test_python_coverage_gate.py \
  apps/agent/pyproject.toml \
  apps/agent/tests/evals/test_receptionist_behavior.py \
  apps/agent/tests/test_observability.py apps/agent/uv.lock \
  apps/agent/coverage-baseline.json \
  docs/engineering/2026-07-30-agent-api-review-decisions.md \
  docs/engineering/ci-and-branch-protection.md \
  docs/superpowers/plans/2026-07-30-python-test-foundation.md \
  scripts/check_python_coverage.py
git commit -m "test: harden Python runtime and coverage gates"
```

---

## Rollback Boundaries

1. **Toolchain rollback:** `.python-version`, both pyprojects, both lockfiles, and the LiveKit evaluation timeout marker can be reverted together without touching runtime behavior.
2. **Test-stabilization rollback:** the focused test's timeout, bounded async
   condition polling, and post-cancellation scheduling yield form one test-only
   unit. Production observability is not part of this boundary.
3. **Coverage rollback:** the checker, checker tests, coverage configuration, baseline files, CI steps, ignore rules, and documentation form one unit. Removing only CI enforcement would silently invalidate decision 10A.

## Completion Criteria

- Both applications install and run under CPython 3.13 from frozen lockfiles.
- A missing timeout or coverage plugin causes pytest configuration to fail immediately.
- The focused cancellation regression has a two-second deadline, uses no
  thread-backed startup wait, passes repeatedly, and production observability
  remains unchanged.
- Every API test has a 60-second deadline; every ordinary agent test has a 30-second deadline; manual credentialed LiveKit evaluations have an explicit 180-second deadline.
- Complete API coverage is measured with PostgreSQL and Redis available.
- Complete agent coverage is measured with only the approved credentialed evaluations skipped.
- Line and branch percentages are independently checked at raw precision
  against committed, measured baselines.
- Coverage decreases fail CI; baseline decreases are prohibited, and baseline
  raises require repeatable improvements attributable to code or test changes.
- Focused test runs remain fast and do not invoke whole-app coverage gates.
- No unrelated code or `Opevo_frontend/` file is changed.
