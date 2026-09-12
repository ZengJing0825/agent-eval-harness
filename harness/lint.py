"""``harness lint``: static checks on the golden set before anyone runs it.

Errors (exit code 1):
* duplicate case ids, files that do not load
* unknown scorer types
* ``answer.status: disputed`` - the two authors have not converged
* owner and peer answers differ (after normalisation) while ``status: agreed``

Warnings (exit code 0):
* missing peer answer (one person wrote the expected value); external-tier
  cases whose file records a ``source`` are exempt - the benchmark is the
  second author
* numeric tolerance implausibly small for the magnitude of ``expected``
  (abs tolerance < 0.1% of |expected| when |expected| >= 1000)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from harness.cases import DEFAULT_GOLDEN_DIR, Case, load_file
from harness.rubric import RUBRICS_DIR, load_rubric
from harness.scorers import SCORERS

TOLERANCE_MAGNITUDE = 1000.0
TOLERANCE_MIN_FRACTION = 0.001


@dataclass
class Issue:
    level: str  # "error" | "warning"
    case_id: str
    file: str
    message: str

    def __str__(self) -> str:
        tag = "ERROR" if self.level == "error" else "WARN "
        where = f"{self.case_id} ({Path(self.file).name})" if self.case_id else Path(self.file).name
        return f"{tag}  {where}: {self.message}"


def normalise_answer_text(value: Any) -> str:
    """Compare owner/peer answers loosely: case, whitespace, trailing punctuation, number formatting."""
    if value is None:
        return ""
    text = " ".join(str(value).split()).casefold().strip().rstrip(".")
    try:
        num = float(text.replace(",", "").replace("$", "").rstrip("%"))
        return f"{num:.10g}"
    except ValueError:
        return re.sub(r"\s+", " ", text)


def _tolerance_of(check: dict[str, Any]) -> Optional[tuple[float, float]]:
    """(|expected|, absolute tolerance) for checks that carry a numeric tolerance."""
    t = check.get("type")
    try:
        expected = abs(float(check.get("expected")))
    except (TypeError, ValueError):
        return None
    if t == "numeric" and not check.get("relative"):
        return expected, float(check.get("tolerance", 1e-6))
    if t == "tolerance" and "rel" not in check:
        return expected, float(check.get("abs", 0.0))
    return None


def lint_case(case: Case) -> list[Issue]:
    issues: list[Issue] = []

    def add(level: str, msg: str) -> None:
        issues.append(Issue(level, case.id, case.source, msg))

    for i, check in enumerate(case.checks):
        if check.get("type") not in SCORERS:
            add("error", f"check {i}: unknown scorer type {check.get('type')!r}")
            continue
        if check.get("type") == "rubric":
            try:
                load_rubric(check.get("name") or "", check.get("rubrics_dir") or RUBRICS_DIR)
            except (FileNotFoundError, ValueError) as exc:
                add("error", f"check {i}: rubric problem: {exc}")
        tol = _tolerance_of(check)
        if tol and tol[0] >= TOLERANCE_MAGNITUDE and tol[1] < tol[0] * TOLERANCE_MIN_FRACTION:
            add("warning", f"check {i}: abs tolerance {tol[1]:g} is < 0.1% of |expected| {tol[0]:g}; "
                           "is that intended?")

    ans = case.answer
    status = case.status
    if not ans:
        add("warning", "no answer block: expected value was not peer-written (missing peer)")
        return issues
    if status == "disputed":
        add("error", "answer.status is 'disputed' - resolve it before the case can run")
    if ans.get("peer") in (None, ""):
        if not (case.tier == "external" and case.provenance.get("source")):
            add("warning", f"missing peer answer (status={status})")
    elif status == "agreed" and normalise_answer_text(ans.get("owner")) != normalise_answer_text(ans.get("peer")):
        add("error", f"owner and peer answers differ but status=agreed: {ans.get('owner')!r} vs {ans.get('peer')!r}")
    return issues


def lint(golden_dir: Path | str = DEFAULT_GOLDEN_DIR) -> tuple[list[Issue], int, int]:
    """Lint every file under ``golden_dir``. Returns (issues, n_cases, n_files)."""
    golden_dir = Path(golden_dir)
    issues: list[Issue] = []
    seen: dict[str, str] = {}
    n_cases = n_files = 0
    for path in sorted(golden_dir.glob("*.yaml")) + sorted(golden_dir.glob("*.yml")):
        n_files += 1
        try:
            cases = load_file(path)
        except (ValueError, KeyError) as exc:
            issues.append(Issue("error", "", str(path), f"does not load: {exc}"))
            continue
        for case in cases:
            n_cases += 1
            if case.id in seen:
                issues.append(Issue("error", case.id, case.source, f"duplicate id (also in {Path(seen[case.id]).name})"))
            seen.setdefault(case.id, case.source)
            issues.extend(lint_case(case))
    return issues, n_cases, n_files


def render(issues: list[Issue], n_cases: int, n_files: int) -> str:
    errors = sum(1 for i in issues if i.level == "error")
    warnings = len(issues) - errors
    lines = [str(i) for i in issues]
    lines.append(f"Lint: {errors} error(s), {warnings} warning(s) across {n_cases} cases in {n_files} files")
    return "\n".join(lines)


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
