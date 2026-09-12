"""Judge calibration: sample judged cases for human labelling, then measure agreement.

``harness audit run.json`` writes an *audit sheet*. The default sampling
rule is ``all-fails,low-first,pass:10``: every judged failure, then every
judged check that passed with a score below 0.5 (low first), then a random
10 of the remaining passes. A human fills in ``human_passed`` (yes/no) or
``human_score`` (0-1), ``reviewer`` and ``human_note``. ``harness audit
--apply sheet.csv`` then computes judge-vs-human agreement overall, per
judge prompt version and per tier, with the number of judgements the human
*overturned to pass* (judge 0, human 1) and *overturned to fail* (judge 1,
human 0), and stores it under ``runs/audits/``.

The sheet carries the judge's ``reason`` so the labeller sees *why* the
judge decided, not just what - a wrong reason with a right verdict is
still a calibration problem. Agreement is tracked per judge version; when
it drops after a prompt change, the judge is revised, not the agent.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from harness.cases import tier_index
from harness.scorers import JUDGED

DEFAULT_AUDITS_DIR = Path("runs") / "audits"
DEFAULT_SAMPLE = "all-fails,low-first,pass:10"
LOW_SCORE = 0.5  # judged passes below this are always in the sheet (``low-first``)
COLUMNS = ["run", "agent", "case_id", "tier", "tool", "check_index", "check_type", "judge", "backend",
           "judge_score", "judge_passed", "judge_reason", "bucket", "prompt", "answer",
           "human_passed", "human_score", "reviewer", "human_note"]
TRUE_WORDS = {"yes", "y", "true", "1", "pass", "passed", "ok", "agree"}
FALSE_WORDS = {"no", "n", "false", "0", "fail", "failed", "wrong", "disagree"}


def judged_checks(run: dict[str, Any], run_path: str = "") -> list[dict[str, Any]]:
    """Every check in the run that an LLM judge actually decided (skips and fallbacks excluded)."""
    rows = []
    for case in run["cases"]:
        for i, chk in enumerate(case.get("checks") or []):
            extra = chk.get("extra") or {}
            if chk["type"] not in JUDGED:
                continue
            if chk.get("score") is None or not extra.get("judge"):
                continue  # skipped, or deterministic fallback
            rows.append({
                "run": run_path, "agent": run["agent"], "case_id": case["id"], "tier": case.get("tier", "unit"),
                "tool": case["tool"], "check_index": i, "check_type": chk["type"],
                "judge": extra.get("judge"), "backend": extra.get("backend"),
                "judge_score": chk["score"], "judge_passed": bool(chk["passed"]),
                "judge_reason": extra.get("reason", ""), "bucket": "", "prompt": case["prompt"],
                "answer": case["answer"], "human_passed": "", "human_score": "", "reviewer": "", "human_note": "",
            })
    return rows


def parse_sample(spec: Optional[str]) -> dict[str, Any]:
    """Parse a ``--sample`` rule into ``{"fails": bool, "low_first": bool, "passes": int | None}``.

    Tokens (comma-separated, additive): ``all-fails`` (every judged failure),
    ``low-first`` (every judged pass scored below 0.5, listed before the
    sampled passes), ``pass:<N>`` (random N of the remaining passes;
    ``pass:all`` keeps them all). ``all`` means everything; the legacy
    ``random:<N>`` means ``all-fails,pass:<N>``. Default: ``all-fails,low-first,pass:10``.
    """
    spec = (spec or DEFAULT_SAMPLE).strip().lower()
    rule: dict[str, Any] = {"fails": False, "low_first": False, "passes": 0}
    if spec == "all":
        return {"fails": True, "low_first": True, "passes": None}
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if token == "all-fails":
            rule["fails"] = True
        elif token == "low-first":
            rule["low_first"] = True
        elif token.startswith("pass:") or token.startswith("random:"):
            kind, value = token.split(":", 1)
            if kind == "random":
                rule["fails"] = True
            if value == "all":
                rule["passes"] = None
                continue
            try:
                n = int(value)
            except ValueError:
                raise ValueError(f"--sample {spec!r}: {kind}:<N> needs an integer, got {value!r}") from None
            if n < 0:
                raise ValueError(f"--sample {spec!r}: {kind}:<N> needs N >= 0")
            rule["passes"] = n
        else:
            raise ValueError(f"--sample {spec!r}: unknown token {token!r}; use all-fails, low-first, pass:<N>, "
                             "all or random:<N>")
    return rule


def build_sheet(run: dict[str, Any], run_path: str = "", sample: Optional[str] = None,
                seed: int = 0) -> list[dict[str, Any]]:
    """Rows in sheet order: judged failures (lowest score first), low-scoring passes, sampled passes.

    Deterministic for a given seed. Each row's ``bucket`` says why it is in
    the sheet: ``fail``, ``low`` or ``pass``.
    """
    rule = parse_sample(sample)
    rows = judged_checks(run, run_path)
    score = lambda r: (float(r["judge_score"] or 0.0), r["case_id"], r["check_index"])  # noqa: E731
    failed = sorted((r for r in rows if not r["judge_passed"]), key=score) if rule["fails"] else []
    passed = [r for r in rows if r["judge_passed"]]
    low = sorted((r for r in passed if float(r["judge_score"] or 0.0) < LOW_SCORE), key=score) if rule["low_first"] else []
    rest = [r for r in passed if r not in low]
    if rule["passes"] is not None and len(rest) > rule["passes"]:
        rest = random.Random(seed).sample(rest, rule["passes"])
    rest.sort(key=lambda r: (r["case_id"], r["check_index"]))
    for group, bucket in ((failed, "fail"), (low, "low"), (rest, "pass")):
        for r in group:
            r["bucket"] = bucket
    return failed + low + rest


def write_sheet(rows: list[dict[str, Any]], out: Path | str) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in COLUMNS})
    return path


def read_sheet(path: Path | str) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_label(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return None


def _to_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in TRUE_WORDS


def human_label(row: dict[str, Any]) -> Optional[bool]:
    """The human verdict: ``human_passed`` if filled in, else ``human_score`` >= 0.5, else None."""
    label = parse_label(row.get("human_passed"))
    if label is not None:
        return label
    try:
        return float(str(row.get("human_score", "")).strip()) >= LOW_SCORE
    except ValueError:
        return None


def agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge-vs-human agreement overall, per judge version and per tier; unlabelled rows are ignored.

    ``overturned_to_pass`` counts judgements the human flipped from fail to
    pass (judge 0, human 1); ``overturned_to_fail`` the reverse.
    """
    def bucket(group: list[dict[str, Any]]) -> dict[str, Any]:
        st = {"n": 0, "agree": 0, "overturned_to_pass": 0, "overturned_to_fail": 0}
        for r in group:
            human = human_label(r)
            if human is None:
                continue
            judge = _to_bool(r.get("judge_passed"))
            st["n"] += 1
            if judge == human:
                st["agree"] += 1
            elif judge and not human:
                st["overturned_to_fail"] += 1  # judge accepted what a human rejected
            else:
                st["overturned_to_pass"] += 1  # judge rejected what a human accepted
        st["agreement"] = (st["agree"] / st["n"]) if st["n"] else None
        return st

    by_judge: dict[str, list[dict[str, Any]]] = {}
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_judge.setdefault(str(r.get("judge") or "?"), []).append(r)
        by_tier.setdefault(str(r.get("tier") or "?"), []).append(r)
    return {
        "labelled": sum(1 for r in rows if human_label(r) is not None),
        "unlabelled": sum(1 for r in rows if human_label(r) is None),
        "overall": bucket(rows),
        "per_judge": {j: bucket(g) for j, g in sorted(by_judge.items())},
        "per_tier": {t: bucket(g) for t, g in sorted(by_tier.items(), key=lambda kv: tier_index(kv[0]))},
        "reviewers": sorted({str(r.get("reviewer") or "") for r in rows if human_label(r) is not None} - {""}),
        "disagreements": [{"case_id": r["case_id"], "tier": r.get("tier"), "judge": r.get("judge"),
                           "judge_passed": _to_bool(r.get("judge_passed")), "human_passed": human_label(r),
                           "reviewer": r.get("reviewer", ""), "judge_reason": r.get("judge_reason", ""),
                           "human_note": r.get("human_note", "")}
                          for r in rows if human_label(r) is not None and human_label(r) != _to_bool(r.get("judge_passed"))],
    }


def apply(sheet_path: Path | str, audits_dir: Path | str = DEFAULT_AUDITS_DIR) -> tuple[dict[str, Any], Path]:
    """Compute agreement for a labelled sheet and store it under ``audits_dir``."""
    rows = read_sheet(sheet_path)
    result = agreement(rows)
    result["sheet"] = str(sheet_path)
    result["runs"] = sorted({str(r.get("run") or "") for r in rows})
    result["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    audits_dir = Path(audits_dir)
    audits_dir.mkdir(parents=True, exist_ok=True)
    out = audits_dir / f"audit-{result['timestamp'].replace(':', '').replace('-', '')}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result, out


def render_agreement(result: dict[str, Any]) -> str:
    from harness.report import table

    def row(name: str, st: dict[str, Any]) -> list[str]:
        rate = "n/a" if st["agreement"] is None else f"{st['agreement'] * 100:.1f}%"
        return [name, str(st["n"]), rate, str(st["overturned_to_pass"]), str(st["overturned_to_fail"])]

    out = [f"Judge audit: {result['labelled']} labelled, {result['unlabelled']} unlabelled rows"
           + (f"  reviewers: {', '.join(result['reviewers'])}" if result.get("reviewers") else ""), ""]
    headers = ["", "n", "agreement", "overturned to pass", "overturned to fail"]
    rows = [row(j, st) for j, st in result["per_judge"].items()] + [row("ALL", result["overall"])]
    out.append("Per judge version:")
    out.append(table(["judge"] + headers[1:], rows))
    if result.get("per_tier"):
        out += ["", "Per tier:", table(["tier"] + headers[1:], [row(t, st) for t, st in result["per_tier"].items()])]
    if result["disagreements"]:
        out += ["", "Disagreements:"]
        for d in result["disagreements"]:
            out.append(f"  - {d['case_id']} [{d['judge']}] judge={'pass' if d['judge_passed'] else 'fail'} "
                       f"human={'pass' if d['human_passed'] else 'fail'}"
                       + (f" ({d['reviewer']})" if d.get("reviewer") else "") + f": {d['judge_reason'][:80]}"
                       + (f" | note: {d['human_note'][:60]}" if d.get("human_note") else ""))
    return "\n".join(out)
