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
        raise CoverageDataError(
            f"invalid coverage report: {key} must be a non-negative integer"
        )
    return value


def _percentage(*, covered: int, total: int, label: str) -> Decimal:
    if total <= 0:
        raise CoverageDataError(f"invalid coverage report: {label} total must be positive")
    if covered > total:
        raise CoverageDataError(
            f"invalid coverage report: covered {label} exceeds total"
        )
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
        raise CoverageDataError(f"cannot create coverage baseline: {error}") from error
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
