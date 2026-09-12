"""Golden-set loader.

Cases live in ``cases/golden/*.yaml``. Each file groups cases for one *tool*
(the capability being exercised), carries a ``version`` so the set can
evolve over time without silently changing what old runs meant, and sits in
one *tier* (``unit`` < ``complex`` < ``external`` < ``dynamic``) so objective
checks can gate the open-ended ones.

File shape::

    version: 1
    tool: ticker_resolution
    tier: unit                      # file default, optional (default: unit)
    cases:
      - id: ticker-001
        prompt: "What is the ticker symbol for Apple?"
        context: {}                 # optional, passed straight to the agent
        scorer: exact               # short form: one check
        expected: AAPL
      - id: ticker-002
        prompt: "..."
        tier: complex               # per-case override
        checks:                     # long form: several checks, averaged
          - {type: contains, expected: MSFT}
          - {type: citation, min: 1}
        answer:                     # optional: two people write every answer
          owner: "MSFT"             # written by the case owner
          peer: "MSFT"              # written independently by a peer
          calculation: null         # how the answer was derived, if any
          source: "fixture:..."     # where it can be verified
          status: agreed            # draft | agreed | disputed

Internally every case is normalised to the long form (a list of checks).
Cases without an ``answer`` block count as ``agreed`` (``harness lint``
warns about the missing peer answer); ``draft`` and ``disputed`` cases are
skipped by ``run`` unless ``--include-unagreed`` is given.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

DEFAULT_GOLDEN_DIR = Path("cases") / "golden"

#: Tiers in execution order. A gate on an earlier tier can stop the later ones.
TIERS = ("unit", "complex", "external", "dynamic")
DEFAULT_TIER = "unit"

STATUSES = ("draft", "agreed", "disputed")
ANSWER_KEYS = ("owner", "peer", "calculation", "source", "status")


@dataclass
class Case:
    id: str
    tool: str
    prompt: str
    checks: list[dict[str, Any]]
    context: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    tier: str = DEFAULT_TIER
    answer: dict[str, Any] = field(default_factory=dict)  # owner/peer/calculation/source/status
    provenance: dict[str, Any] = field(default_factory=dict)  # file-level source/license (external sets)
    source: str = ""  # file the case came from, for error messages

    @property
    def status(self) -> str:
        return str(self.answer.get("status") or "agreed")

    @property
    def agreed(self) -> bool:
        return self.status == "agreed"


def tier_index(tier: str) -> int:
    """Position of a tier in the execution order (unknown tiers sort last)."""
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


def validate_tier(tier: Any, where: str) -> str:
    tier = str(tier or DEFAULT_TIER)
    if tier not in TIERS:
        raise ValueError(f"{where}: unknown tier {tier!r}; known: {list(TIERS)}")
    return tier


def normalise_answer(raw: Any, where: str) -> dict[str, Any]:
    """Validate an ``answer`` block; missing block -> ``{}`` (treated as agreed)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: 'answer' must be a mapping with keys {list(ANSWER_KEYS)}")
    unknown = sorted(set(raw) - set(ANSWER_KEYS))
    if unknown:
        raise ValueError(f"{where}: unknown answer keys {unknown}; allowed: {list(ANSWER_KEYS)}")
    out = {k: raw.get(k) for k in ANSWER_KEYS}
    out["status"] = str(out["status"] or "draft")
    if out["status"] not in STATUSES:
        raise ValueError(f"{where}: answer.status must be one of {list(STATUSES)}, got {out['status']!r}")
    return out


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
    reserved = {"id", "prompt", "context", "tags", "scorer", "tool", "note", "tier", "answer"}
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
    file_tier = validate_tier(doc.get("tier"), str(path))
    provenance = {k: doc[k] for k in ("source", "license") if doc.get(k)}
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
                tier=validate_tier(raw.get("tier", file_tier), f"{path} case {raw['id']!r}"),
                answer=normalise_answer(raw.get("answer"), f"{path} case {raw['id']!r}"),
                provenance=provenance,
                source=str(path),
            )
        )
    return cases


def load_cases(golden_dir: Path | str = DEFAULT_GOLDEN_DIR,
               tools: Optional[Iterable[str]] = None,
               tiers: Optional[Iterable[str]] = None) -> list[Case]:
    """Load every ``*.yaml`` under ``golden_dir`` (sorted for determinism).

    Raises ``ValueError`` on duplicate ids so the set stays unambiguous.
    ``tools`` / ``tiers`` filter the result; the order is file order, so
    callers that care about tier order sort with :func:`tier_index`.
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
    if tiers:
        wanted_tiers = set(tiers)
        for t in wanted_tiers:
            validate_tier(t, "tier filter")
        cases = [c for c in cases if c.tier in wanted_tiers]
    return cases


def golden_files(golden_dir: Path | str = DEFAULT_GOLDEN_DIR) -> list[Path]:
    golden_dir = Path(golden_dir)
    return sorted(golden_dir.glob("*.yaml")) + sorted(golden_dir.glob("*.yml"))


def set_files(golden_dir: Path | str = DEFAULT_GOLDEN_DIR) -> dict[str, Any]:
    """``{"policy.yaml": 2, ...}`` - the declared ``version`` of every case file."""
    out: dict[str, Any] = {}
    for path in golden_files(golden_dir):
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        out[path.name] = doc.get("version")
    return out


def set_provenance(golden_dir: Path | str = DEFAULT_GOLDEN_DIR) -> dict[str, dict[str, Any]]:
    """``{"external_x.yaml": {"tier": "external", "source": ..., "license": ...}}`` for files that declare a source."""
    out: dict[str, dict[str, Any]] = {}
    for path in golden_files(golden_dir):
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        if doc.get("source") or doc.get("license"):
            out[path.name] = {"tier": doc.get("tier", DEFAULT_TIER), "source": doc.get("source"),
                              "license": doc.get("license")}
    return out


def set_version(golden_dir: Path | str = DEFAULT_GOLDEN_DIR) -> str:
    """Content hash of the whole golden set (file names, declared versions, bytes).

    Two runs are only comparable as equals when their set versions match;
    bumping a file's ``version`` or editing any case changes it.
    """
    h = hashlib.sha256()
    for path in golden_files(golden_dir):
        with open(path, "rb") as fh:
            data = fh.read()
        declared = (yaml.safe_load(data) or {}).get("version")
        h.update(f"{path.name}:{declared}:".encode("utf-8"))
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()[:12]
