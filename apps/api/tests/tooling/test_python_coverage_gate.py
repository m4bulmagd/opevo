import importlib.util
import json
import os
import subprocess
import sys
from types import ModuleType
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


def _write_baseline(path: Path, *, line: object = "90.00", branch: object = "75.00") -> None:
    path.write_text(
        json.dumps(
            {
                "minimum_line_percent": line,
                "minimum_branch_percent": branch,
            }
        ),
        encoding="utf-8",
    )


def _run_checker(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def _load_checker_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_python_coverage_under_test",
        CHECKER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load coverage checker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_initialize_writes_measured_line_and_branch_minimums(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)

    result = _run_checker(
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


def test_initialize_rounds_non_terminating_percentages_down(
    tmp_path: Path,
) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(
        report,
        covered_lines=2,
        num_statements=3,
        covered_branches=1,
        num_branches=6,
    )

    result = _run_checker(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 0
    assert json.loads(baseline.read_text(encoding="utf-8")) == {
        "minimum_line_percent": "66.66",
        "minimum_branch_percent": "16.66",
    }


def test_initialize_refuses_to_replace_a_reviewed_baseline(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)
    baseline.write_text('{"reviewed": true}', encoding="utf-8")

    result = _run_checker(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert json.loads(baseline.read_text(encoding="utf-8")) == {"reviewed": True}
    assert "already exists" in result.stderr


def test_initialize_removes_staging_file_when_atomic_install_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_checker_module()
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)

    def fail_install(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected install failure")

    monkeypatch.setattr(os, "link", fail_install)

    with pytest.raises(
        checker.CoverageDataError,
        match="cannot create coverage baseline: injected install failure",
    ):
        checker.initialize(report, baseline)

    assert not baseline.exists()
    assert [path.name for path in tmp_path.iterdir()] == ["coverage.json"]


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
    _write_baseline(baseline)
    _write_report(report, **report_counts)

    result = _run_checker(
        "check",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


def test_check_rejects_raw_regression_when_two_decimal_displays_match(
    tmp_path: Path,
) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(
        report,
        covered_lines=17_907,
        num_statements=20_000,
    )
    _write_baseline(baseline, line="89.54")

    result = _run_checker(
        "check",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 1
    assert result.stderr == "line coverage 89.535% is below 89.540%\n"


def test_check_accepts_coverage_at_the_reviewed_minimum(tmp_path: Path) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)
    _write_baseline(baseline)

    result = _run_checker(
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
        {
            "covered_lines": 11,
            "num_statements": 10,
            "covered_branches": 3,
            "num_branches": 4,
        },
    ],
)
def test_initialize_rejects_invalid_counts(tmp_path: Path, totals: dict[str, object]) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    report.write_text(json.dumps({"totals": totals}), encoding="utf-8")

    result = _run_checker(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert not baseline.exists()
    assert "invalid coverage report" in result.stderr


@pytest.mark.parametrize("contents", ["{", "[]"])
def test_initialize_rejects_non_object_or_malformed_report(
    tmp_path: Path, contents: str
) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    report.write_text(contents, encoding="utf-8")

    result = _run_checker(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert not baseline.exists()
    assert "invalid coverage report" in result.stderr


def test_check_rejects_a_missing_report(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline)

    result = _run_checker(
        "check",
        "--report",
        str(tmp_path / "missing.json"),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert "invalid coverage report" in result.stderr


def test_check_rejects_a_missing_baseline_with_a_safe_data_error(
    tmp_path: Path,
) -> None:
    report = tmp_path / "coverage.json"
    missing_baseline = tmp_path / "missing-baseline.json"
    _write_report(report)

    result = _run_checker(
        "check",
        "--report",
        str(report),
        "--baseline",
        str(missing_baseline),
    )

    assert result.returncode == 2
    assert result.stderr.startswith("invalid coverage baseline:")
    assert "missing-baseline.json" in result.stderr
    assert "Traceback" not in result.stderr


def test_initialize_rejects_zero_branch_total_with_valid_line_total(
    tmp_path: Path,
) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report, covered_branches=0, num_branches=0)

    result = _run_checker(
        "initialize",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert not baseline.exists()
    assert "invalid coverage report: branch total must be positive" in result.stderr


@pytest.mark.parametrize("line", [True, "NaN", "Infinity", "-0.01", "100.01"])
def test_check_rejects_invalid_baseline_values(tmp_path: Path, line: object) -> None:
    report = tmp_path / "coverage.json"
    baseline = tmp_path / "baseline.json"
    _write_report(report)
    _write_baseline(baseline, line=line)

    result = _run_checker(
        "check",
        "--report",
        str(report),
        "--baseline",
        str(baseline),
    )

    assert result.returncode == 2
    assert "invalid coverage baseline" in result.stderr
