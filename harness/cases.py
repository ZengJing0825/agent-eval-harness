"""Golden-set loader.

Cases live in ``cases/golden/*.yaml``. Each file groups cases for one *tool*
(the capability being exercised) and carries a ``version`` so the set can
evolve over time without silently changing what old runs meant.

File shape::

    version: 1
    tool: ticker_resolution
    cases:
      - id: ticker-001
        prompt: "What is the ticker symbol for Apple?"
        context: {}                 # optional, passed straight to the agent
        scorer: exact               # short form: one check
        expected: AAPL
      - id: ticker-002
        prompt: "..."
        checks:                     # long form: several checks, averaged
          - {type: contains, expected: MSFT}
          - {type: citation, min: 1}

Internally every case is normalised to the long form (a list of checks).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

DEFAULT_GOLDEN_DIR = Path("cases") / "golden"


@dataclass
class Case:
    id: str
    tool: str
    prompt: str
    checks: list[dict[str, Any]]
    context: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    source: str = ""  # file the case came from, for error messages


def normalise_checks(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the short form (``scorer`` + ``expected`` + extras) into ``checks``."""
    if "checks" in raw:
        checks = raw["checks"]
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"case {raw.get('id')!r}: 'checks' must be a non-empty list")
        for c in checks:
            if "type" not in c:
                raise ValueError(f"case {raw.get('id')!r}: every check needs a 'type'")
        return checks
    if "scorer" not in raw:
        raise ValueError(f"case {raw.get('id')!r}: needs 'scorer' or 'checks'")
    # Everything that is not a known case-level key becomes a check argument.
    reserved = {"id", "prompt", "context", "tags", "scorer", "tool", "note"}
    check = {"type": raw["scorer"]}
    check.update({k: v for k, v in raw.items() if k not in reserved})
    return [check]


def load_file(path: Path) -> list[Case]:
    """Load one YAML file and return its cases."""
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if "version" not in doc:
        raise ValueError(f"{path}: missing 'version'")
    tool = doc.get("tool")
    if not tool:
        raise ValueError(f"{path}: missing 'tool'")
    cases: list[Case] = []
    for raw in doc.get("cases") or []:
        if "id" not in raw or "prompt" not in raw:
            raise ValueError(f"{path}: every case needs 'id' and 'prompt'")
        cases.append(
            Case(
                id=str(raw["id"]),
                tool=str(raw.get("tool", tool)),
                prompt=str(raw["prompt"]),
                checks=normalise_checks(raw),
                context=dict(raw.get("context") or {}),
                tags=list(raw.get("tags") or []),
                source=str(path),
            )
        )
    return cases


def load_cases(golden_dir: Path | str = DEFAULT_GOLDEN_DIR,
               tools: Iterable[str] | None = None) -> list[Case]:
    """Load every ``*.yaml`` under ``golden_dir`` (sorted for determinism).

    Raises ``ValueError`` on duplicate ids so the set stays unambiguous.
    """
    golden_dir = Path(golden_dir)
    cases: list[Case] = []
    for path in sorted(golden_dir.glob("*.yaml")) + sorted(golden_dir.glob("*.yml")):
        cases.extend(load_file(path))
    seen: dict[str, str] = {}
    for c in cases:
        if c.id in seen:
            raise ValueError(f"duplicate case id {c.id!r} in {seen[c.id]} and {c.source}")
        seen[c.id] = c.source
    if tools:
        wanted = set(tools)
        cases = [c for c in cases if c.tool in wanted]
    return cases
