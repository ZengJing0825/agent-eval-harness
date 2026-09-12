"""Bad-case feedback loop.

A *bad case* is a production sample where the agent got it wrong. Capturing
it takes one command; promoting it into the golden set takes another. The
backlog is a directory of small YAML files (one per case) so entries can be
reviewed, edited and diffed like code.

    cases/backlog/bc-20260912-a1b2.yaml   ->  cases/golden/promoted.yaml

Every bad case carries a *category* that says what actually broke:

    data         the agent had wrong or missing data
    tool_choice  it picked the wrong tool / capability
    ambiguity    the question itself was unclear - fix the question, not the agent
    reasoning    right data, right tool, wrong conclusion
    judge        the judge (not the agent) got it wrong

``ambiguity`` cases can only be promoted with ``--rewrite``: the golden set
gets the clarified prompt and keeps the original for the record. Every
promotion appends a line to ``cases/CHANGELOG.md`` with the golden-set
version before and after.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from harness.cases import set_version

DEFAULT_BACKLOG_DIR = Path("cases") / "backlog"
DEFAULT_PROMOTED_FILE = Path("cases") / "golden" / "promoted.yaml"
DEFAULT_CHANGELOG = Path("cases") / "CHANGELOG.md"
CATEGORIES = ("data", "tool_choice", "ambiguity", "reasoning", "judge")
CHANGELOG_HEADER = ("# Golden-set changelog\n\n"
                    "One line per promotion, appended by `harness badcase promote`.\n"
                    "Columns: date, case id, category, golden-set version before -> after, note.\n\n"
                    "| date | id | category | set version | note |\n|---|---|---|---|---|\n")


def new_id() -> str:
    return f"bc-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(2)}"


def validate_category(category: Optional[str]) -> str:
    if not category:
        raise ValueError(f"--category is required; one of {list(CATEGORIES)}")
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; one of {list(CATEGORIES)}")
    return category


def add(agent: str, prompt: str, expected: str, note: str = "", tool: str = "backlog",
        scorer: str = "contains", observed: str = "", category: Optional[str] = None,
        owner: str = "", backlog_dir: Path | str = DEFAULT_BACKLOG_DIR) -> Path:
    """Capture a failing sample. Returns the path of the new backlog entry."""
    category = validate_category(category)
    backlog_dir = Path(backlog_dir)
    backlog_dir.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "id": new_id(),
        "status": "open",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent": agent,
        "category": category,
        "tool": tool,
        "prompt": prompt,
        "observed": observed,
        "expected": expected,
        "scorer": scorer,
        "note": note,
        "owner": owner,
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


def group_by_category(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Entries grouped in the canonical category order (unknown/missing last)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        groups.setdefault(str(e.get("category") or "uncategorised"), []).append(e)
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return {k: groups[k] for k in sorted(groups, key=lambda k: (order.get(k, len(order)), k))}


def review_record(reviewer: Optional[str] = None, agree: bool = False,
                  peer: Optional[str] = None) -> dict[str, Any]:
    """The ``answer.peer`` review written by ``promote``.

    ``reviewer`` names who reviewed, ``agree`` records verdict ``agree``.
    ``peer`` is the deprecated ``--peer "<answer>"`` form: it is recorded as
    an ``agree`` verdict with the peer's answer kept in the note.
    """
    review: dict[str, Any] = {"reviewer": reviewer, "verdict": None, "note": None}
    if peer is not None:
        review["verdict"] = "agree"
        review["note"] = f"recorded via deprecated --peer; peer answer was {str(peer)!r}"
    if agree:
        review["verdict"] = "agree"
    return review


def to_golden_case(entry: dict[str, Any], rewrite: Optional[str] = None,
                   reviewer: Optional[str] = None, agree: bool = False,
                   peer: Optional[str] = None) -> dict[str, Any]:
    """Shape a backlog entry as a golden case (short form).

    For ``ambiguity`` entries ``rewrite`` is mandatory: the promoted prompt is
    the clarified question and the original is kept as ``original_prompt``.
    The answer block records the reviewer's verdict (``--reviewer NAME
    --agree``); without a verdict the case is a ``draft`` that ``run`` skips.
    """
    scorer = entry.get("scorer", "contains")
    category = entry.get("category")
    if category == "ambiguity" and not rewrite:
        raise ValueError(f"{entry['id']}: category 'ambiguity' needs --rewrite \"<clarified prompt>\" - "
                         "the fix is the question, not the agent")
    case: dict[str, Any] = {
        "id": entry["id"],
        "tool": entry.get("tool", "backlog"),
        "prompt": rewrite or entry["prompt"],
        "scorer": scorer,
        "tags": ["promoted", f"from:{entry.get('agent', '?')}", f"category:{category or 'uncategorised'}"],
    }
    if rewrite:
        case["original_prompt"] = entry["prompt"]
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
    review = review_record(reviewer, agree, peer)
    case["answer"] = {"owner": entry["expected"], "peer": review, "calculation": None,
                      "source": f"badcase {entry['id']} ({entry.get('agent', '?')})",
                      "status": "agreed" if review["verdict"] == "agree" else "draft"}
    return case


def append_changelog(changelog: Path | str, case_id: str, category: str, before: str, after: str,
                     note: str = "") -> Path:
    changelog = Path(changelog)
    changelog.parent.mkdir(parents=True, exist_ok=True)
    if not changelog.exists():
        changelog.write_text(CHANGELOG_HEADER, encoding="utf-8")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note = " ".join(str(note).split()).replace("|", "/")
    with open(changelog, "a", encoding="utf-8") as fh:
        fh.write(f"| {date} | {case_id} | {category} | {before} -> {after} | {note} |\n")
    return changelog


def promote(case_id: str, backlog_dir: Path | str = DEFAULT_BACKLOG_DIR,
            golden_file: Path | str = DEFAULT_PROMOTED_FILE, rewrite: Optional[str] = None,
            reviewer: Optional[str] = None, agree: bool = False,
            peer: Optional[str] = None, changelog: Optional[Path | str] = None) -> Path:
    """Move a backlog entry into the golden set, bump the file version, log the change."""
    backlog_dir, golden_file = Path(backlog_dir), Path(golden_file)
    golden_dir = golden_file.parent
    changelog = Path(changelog) if changelog else golden_dir.parent / "CHANGELOG.md"
    src = backlog_dir / f"{case_id}.yaml"
    if not src.exists():
        raise FileNotFoundError(f"no backlog entry {case_id!r} in {backlog_dir}")
    with open(src, encoding="utf-8") as fh:
        entry = yaml.safe_load(fh)
    new_case = to_golden_case(entry, rewrite=rewrite, reviewer=reviewer, agree=agree, peer=peer)  # validates first

    if golden_file.exists():
        with open(golden_file, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    else:
        doc = {"version": 0, "tool": "backlog", "cases": []}
    doc.setdefault("cases", [])
    if any(c.get("id") == case_id for c in doc["cases"]):
        raise ValueError(f"case {case_id!r} already promoted")
    before = set_version(golden_dir) if golden_dir.exists() else "none"
    doc["cases"].append(new_case)
    doc["version"] = int(doc.get("version", 0)) + 1  # every promotion is a new golden-set version

    golden_dir.mkdir(parents=True, exist_ok=True)
    golden_file.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    src.unlink()  # the backlog entry now lives in the golden set
    after = set_version(golden_dir)
    note = entry.get("note", "")
    if rewrite:
        note = f"prompt rewritten (was: {entry['prompt']!r}); {note}"
    append_changelog(changelog, case_id, str(entry.get("category") or "uncategorised"), before, after, note)
    return golden_file
