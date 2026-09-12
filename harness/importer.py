"""``harness import``: turn an external benchmark (CSV / JSONL) into a case file.

External sets are kept in their own tier (``external``) and file, with the
``source`` and ``license`` recorded at file level, so a run can always say
which numbers came from a third-party set and under which terms.

    harness import --csv bench.csv --map "prompt=question,expected=answer,tool=category" \\
        --tier external --source "sample-bench" --license "CC BY 4.0" \\
        --out cases/golden/external_sample-bench.yaml
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from harness.cases import TIERS

CASE_FIELDS = ("id", "prompt", "expected", "tool", "note", "tags")
DEFAULT_MAP = {"prompt": "prompt", "expected": "expected"}
DETERMINISTIC_SCORERS = ("contains", "exact", "correctness", "regex", "numeric")


def parse_map(spec: Optional[str]) -> dict[str, str]:
    """``"prompt=question,expected=answer,tool=category"`` -> ``{"prompt": "question", ...}``."""
    mapping = dict(DEFAULT_MAP)
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"--map entry {part!r}: expected <case field>=<column>")
        field, column = (x.strip() for x in part.split("=", 1))
        if field not in CASE_FIELDS:
            raise ValueError(f"--map: unknown case field {field!r}; known: {list(CASE_FIELDS)}")
        mapping[field] = column
    return mapping


def read_rows(csv_path: Optional[str] = None, jsonl_path: Optional[str] = None) -> list[dict[str, Any]]:
    if bool(csv_path) == bool(jsonl_path):
        raise ValueError("give exactly one of --csv or --jsonl")
    if csv_path:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    rows = []
    with open(jsonl_path, encoding="utf-8") as fh:  # type: ignore[arg-type]
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"{jsonl_path}:{n}: invalid JSON ({exc})") from None
    return rows


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).casefold()).strip("-") or "external"


def _coerce_expected(value: Any, scorer: str) -> Any:
    if scorer == "numeric":
        return float(str(value).replace(",", ""))
    if scorer == "correctness":
        try:
            return float(str(value).replace(",", "")) if re.fullmatch(r"[-+]?[\d,]*\.?\d+", str(value).strip()) else str(value)
        except ValueError:
            return str(value)
    return str(value)


def build_document(rows: Iterable[dict[str, Any]], mapping: dict[str, str], source: str, license_text: str,
                   tier: str = "external", tool: str = "external", scorer: str = "contains",
                   status: str = "agreed") -> dict[str, Any]:
    """Shape rows as a golden-set file document (``version: 1``)."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; known: {list(TIERS)}")
    if scorer not in DETERMINISTIC_SCORERS:
        raise ValueError(f"--scorer must be deterministic for imports: {list(DETERMINISTIC_SCORERS)}")
    prefix = f"ext-{slug(source)}"
    cases = []
    for n, row in enumerate(rows, 1):
        def col(field: str) -> Any:
            column = mapping.get(field)
            return row.get(column) if column else None

        prompt, expected = col("prompt"), col("expected")
        if prompt in (None, "") or expected in (None, ""):
            raise ValueError(f"row {n}: missing prompt ({mapping.get('prompt')!r}) or expected ({mapping.get('expected')!r})")
        case: dict[str, Any] = {"id": str(col("id") or f"{prefix}-{n:03d}"), "prompt": str(prompt)}
        row_tool = col("tool")
        if row_tool:
            case["tool"] = str(row_tool)
        case["scorer"] = scorer
        case[("pattern" if scorer == "regex" else "expected")] = _coerce_expected(expected, scorer)
        if scorer == "numeric":
            case["tolerance"] = 0.01
        tags = col("tags")
        if tags:
            case["tags"] = [t.strip() for t in str(tags).split(",") if t.strip()] if isinstance(tags, str) else list(tags)
        note = col("note")
        if note:
            case["note"] = str(note)
        case["answer"] = {"owner": str(expected), "peer": None, "calculation": None, "source": source, "status": status}
        cases.append(case)
    if not cases:
        raise ValueError("no rows to import")
    return {"version": 1, "tool": tool, "tier": tier, "source": source, "license": license_text, "cases": cases}


def write_document(doc: dict[str, Any], out: Path | str, origin: str = "") -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# Imported by `harness import` from {origin}.\n" if origin else "") + \
             f"# source: {doc['source']}  license: {doc['license']}\n" \
             "# Peer answers are the benchmark's own; edit answer.status to draft to hold a case back.\n"
    out.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    return out
