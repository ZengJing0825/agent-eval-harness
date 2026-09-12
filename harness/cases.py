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
    set_label: "2026-09-12"         # optional human label; default: the file's last git commit date, else today
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
        answer:                     # optional: one owner writes, one peer reviews
          owner: "MSFT"             # the answer, written by the case owner
          peer:                     # the review record (not a second answer)
            reviewer: "peer-a"
            verdict: agree          # agree | disagree | null (not reviewed yet)
            note: null
          calculation: null         # how the answer was derived, if any
          source: "fixture:..."     # where it can be verified
          status: agreed            # draft | agreed | disputed | skipped_unsupported; derived when absent

Dynamic-tier cases may use ``{today}`` / ``{as_of}`` in prompt, context and
check values, and name a *resolver* (``resolver: agents.resolvers:earnings_date``
plus ``resolver_args``) whose returned keys become further placeholders, so
the expected value moves with ``run --as-of``.

Internally every case is normalised to the long form (a list of checks).
Cases without an ``answer`` block count as ``agreed`` (``harness lint``
warns about the missing peer review); ``draft`` and ``disputed`` cases are
skipped by ``run`` unless ``--include-unagreed`` is given.

The old string form ``peer: "MSFT"`` (a second, independently written
answer) is still accepted: equal to ``owner`` it becomes a review with
verdict ``agree``, different it becomes ``disagree`` with the note
"peer wrote a different answer".
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

DEFAULT_GOLDEN_DIR = Path("cases") / "golden"

#: Tiers in execution order. A gate on an earlier tier can stop the later ones.
TIERS = ("unit", "complex", "external", "dynamic")
DEFAULT_TIER = "unit"

STATUSES = ("draft", "agreed", "disputed", "skipped_unsupported")
#: ``skipped_unsupported``: the tool or data the case needs is not supported yet;
#: ``run`` always skips it (reason "unsupported") and ``report`` counts it separately.
ANSWER_KEYS = ("owner", "peer", "calculation", "source", "status")
PEER_KEYS = ("reviewer", "verdict", "note")
VERDICTS = ("agree", "disagree")
LEGACY_PEER_NOTE = "peer wrote a different answer"


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
    resolver: Optional[str] = None  # "module.path:function" for dynamic expectations
    resolver_args: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # file the case came from, for error messages

    @property
    def status(self) -> str:
        return str(self.answer.get("status") or "agreed")

    @property
    def agreed(self) -> bool:
        return self.status == "agreed"

    @property
    def unsupported(self) -> bool:
        return self.status == "skipped_unsupported"

    @property
    def review(self) -> dict[str, Any]:
        """The peer review record (``reviewer`` / ``verdict`` / ``note``), empty when unreviewed."""
        return dict(self.answer.get("peer") or empty_review())


def tier_index(tier: str) -> int:
    """Position of a tier in the execution order (unknown tiers sort last)."""
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


def validate_tier(tier: Any, where: str) -> str:
    tier = str(tier or DEFAULT_TIER)
    if tier not in TIERS:
        raise ValueError(f"{where}: unknown tier {tier!r}; known: {list(TIERS)}")
    return tier


def normalise_answer_text(value: Any) -> str:
    """Compare answers loosely: case, whitespace, trailing punctuation, number formatting."""
    if value is None:
        return ""
    text = " ".join(str(value).split()).casefold().strip().rstrip(".")
    try:
        num = float(text.replace(",", "").replace("$", "").rstrip("%"))
        return f"{num:.10g}"
    except ValueError:
        return re.sub(r"\s+", " ", text)


def empty_review() -> dict[str, Any]:
    return {"reviewer": None, "verdict": None, "note": None}


def normalise_peer(raw: Any, owner: Any, where: str) -> dict[str, Any]:
    """Validate the ``peer`` review record.

    A mapping is validated against :data:`PEER_KEYS`; ``None`` means "not
    reviewed yet"; a bare string is the legacy form (the peer's own answer)
    and is converted to a verdict by comparing it with ``owner``.
    """
    if raw is None or raw == "":
        return empty_review()
    if isinstance(raw, dict):
        unknown = sorted(set(raw) - set(PEER_KEYS))
        if unknown:
            raise ValueError(f"{where}: unknown answer.peer keys {unknown}; allowed: {list(PEER_KEYS)}")
        out = {k: raw.get(k) for k in PEER_KEYS}
        if out["verdict"] is not None:
            out["verdict"] = str(out["verdict"]).strip().lower()
            if out["verdict"] not in VERDICTS:
                raise ValueError(f"{where}: answer.peer.verdict must be one of {list(VERDICTS)} or null, "
                                 f"got {out['verdict']!r}")
        for k in ("reviewer", "note"):
            if out[k] is not None:
                out[k] = str(out[k])
        return out
    if isinstance(raw, (str, int, float)):  # legacy: peer wrote an independent answer
        same = normalise_answer_text(owner) == normalise_answer_text(raw)
        return {"reviewer": None, "verdict": "agree" if same else "disagree",
                "note": None if same else f"{LEGACY_PEER_NOTE}: {str(raw)!r}"}
    raise ValueError(f"{where}: answer.peer must be a mapping with keys {list(PEER_KEYS)} (or a legacy string)")


def derive_status(review: dict[str, Any]) -> str:
    """``agree`` -> agreed, ``disagree`` -> disputed, no verdict -> draft."""
    verdict = (review or {}).get("verdict")
    return "agreed" if verdict == "agree" else "disputed" if verdict == "disagree" else "draft"


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
    out["peer"] = normalise_peer(raw.get("peer"), out["owner"], where)
    out["status"] = str(out["status"]) if out.get("status") else derive_status(out["peer"])
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
    reserved = {"id", "prompt", "context", "tags", "scorer", "tool", "note", "tier", "answer",
                "resolver", "resolver_args", "original_prompt"}
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
                resolver=(str(raw["resolver"]) if raw.get("resolver") else None),
                resolver_args=dict(raw.get("resolver_args") or {}),
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


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_git_date_cache: dict[tuple[str, int, int], Optional[str]] = {}


def clear_git_date_cache() -> None:
    """Forget cached git dates (the cache is per process, keyed by path + mtime + size)."""
    _git_date_cache.clear()


def file_git_date(path: Path) -> Optional[str]:
    """``YYYY-MM-DD`` of the last commit touching ``path``; None when untracked, modified or not in git.

    Cached per process by (path, mtime, size): committing an unchanged file
    is not noticed until the next process, which is fine for CLI use.
    """
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    if key not in _git_date_cache:
        _git_date_cache[key] = _file_git_date_uncached(path)
    return _git_date_cache[key]


def _file_git_date_uncached(path: Path) -> Optional[str]:
    try:
        log = subprocess.run(["git", "log", "-1", "--format=%cs", "--", path.name], cwd=str(path.parent),
                             capture_output=True, text=True, timeout=10)
        if log.returncode != 0 or not log.stdout.strip():
            return None
        status = subprocess.run(["git", "status", "--porcelain", "--", path.name], cwd=str(path.parent),
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0 or status.stdout.strip():
        return None  # edited since the last commit: the label is "today"
    return log.stdout.strip()


def set_labels(golden_dir: Path | str = DEFAULT_GOLDEN_DIR) -> dict[str, str]:
    """``{"policy.yaml": "2026-09-12", ...}`` - a human label per case file.

    The file's ``set_label`` wins; otherwise the date of its last git commit;
    otherwise today. Runs store this next to ``set_version`` (the content
    hash) so an experiment can be named "set + date".
    """
    out: dict[str, str] = {}
    for path in golden_files(golden_dir):
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        label = doc.get("set_label")
        out[path.name] = str(label) if label not in (None, "") else (file_git_date(path) or _today())
    return out


def set_label_summary(labels: Optional[dict[str, str]]) -> str:
    """One label for a whole set: the common label, or ``oldest..newest`` when files differ."""
    values = sorted({str(v) for v in (labels or {}).values()})
    if not values:
        return "?"
    return values[0] if len(values) == 1 else f"{values[0]}..{values[-1]}"


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
