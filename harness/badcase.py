"""Bad-case feedback loop.

A *bad case* is a production sample where the agent got it wrong. Capturing
it takes one command; promoting it into the golden set takes another. The
backlog is a directory of small YAML files (one per case) so entries can be
reviewed, edited and diffed like code.

    cases/backlog/bc-20260912-a1b2.yaml   ->  cases/golden/promoted.yaml
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BACKLOG_DIR = Path("cases") / "backlog"
DEFAULT_PROMOTED_FILE = Path("cases") / "golden" / "promoted.yaml"


def new_id() -> str:
    return f"bc-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(2)}"


def add(agent: str, prompt: str, expected: str, note: str = "", tool: str = "backlog",
        scorer: str = "contains", observed: str = "",
        backlog_dir: Path | str = DEFAULT_BACKLOG_DIR) -> Path:
    """Capture a failing sample. Returns the path of the new backlog entry."""
    backlog_dir = Path(backlog_dir)
    backlog_dir.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "id": new_id(),
        "status": "open",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent": agent,
        "tool": tool,
        "prompt": prompt,
        "observed": observed,
        "expected": expected,
        "scorer": scorer,
        "note": note,
    }
    path = backlog_dir / f"{entry['id']}.yaml"
    path.write_text(yaml.safe_dump(entry, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def list_entries(backlog_dir: Path | str = DEFAULT_BACKLOG_DIR) -> list[dict[str, Any]]:
    entries = []
    for p in sorted(Path(backlog_dir).glob("*.yaml")):
        with open(p, encoding="utf-8") as fh:
            entries.append(yaml.safe_load(fh))
    return entries


def to_golden_case(entry: dict[str, Any]) -> dict[str, Any]:
    """Shape a backlog entry as a golden case (short form)."""
    scorer = entry.get("scorer", "contains")
    case: dict[str, Any] = {
        "id": entry["id"],
        "tool": entry.get("tool", "backlog"),
        "prompt": entry["prompt"],
        "scorer": scorer,
        "tags": ["promoted", f"from:{entry.get('agent', '?')}"],
    }
    # Each scorer names its argument differently; ``expected`` on the CLI maps onto it.
    if scorer == "policy":
        case["forbidden"] = [entry["expected"]]
    elif scorer == "regex":
        case["pattern"] = entry["expected"]
    elif scorer == "numeric":
        case["expected"], case["tolerance"] = float(entry["expected"]), 0.01
    elif scorer == "citation":
        case["min"] = int(entry.get("expected") or 1)
    else:
        case["expected"] = entry["expected"]
    if entry.get("note"):
        case["note"] = entry["note"]
    return case


def promote(case_id: str, backlog_dir: Path | str = DEFAULT_BACKLOG_DIR,
            golden_file: Path | str = DEFAULT_PROMOTED_FILE) -> Path:
    """Move a backlog entry into the golden set and bump the file version."""
    backlog_dir, golden_file = Path(backlog_dir), Path(golden_file)
    src = backlog_dir / f"{case_id}.yaml"
    if not src.exists():
        raise FileNotFoundError(f"no backlog entry {case_id!r} in {backlog_dir}")
    with open(src, encoding="utf-8") as fh:
        entry = yaml.safe_load(fh)

    if golden_file.exists():
        with open(golden_file, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    else:
        doc = {"version": 0, "tool": "backlog", "cases": []}
    doc.setdefault("cases", [])
    if any(c.get("id") == case_id for c in doc["cases"]):
        raise ValueError(f"case {case_id!r} already promoted")
    doc["cases"].append(to_golden_case(entry))
    doc["version"] = int(doc.get("version", 0)) + 1  # every promotion is a new golden-set version

    golden_file.parent.mkdir(parents=True, exist_ok=True)
    golden_file.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    src.unlink()  # the backlog entry now lives in the golden set
    return golden_file
