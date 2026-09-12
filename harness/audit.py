"""Judge calibration: sample judged cases for human labelling, then measure agreement.

``harness audit run.json`` writes an *audit sheet* with every judged check
that failed plus a random sample of judged checks that passed. A human
fills in ``human_passed`` (yes/no) and ``human_note``. ``harness audit
--apply sheet.csv`` then computes judge-vs-human agreement, overall and
per judge prompt version, and stores it under ``runs/audits/``.

The sheet carries the judge's ``reason`` so the labeller sees *why* the
judge decided, not just what - a wrong reason with a right verdict is
still a calibration problem.
"""
from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from harness.scorers import JUDGED

DEFAULT_AUDITS_DIR = Path("runs") / "audits"
COLUMNS = ["run", "agent", "case_id", "tier", "tool", "check_index", "check_type", "judge", "backend",
           "judge_score", "judge_passed", "judge_reason", "prompt", "answer", "human_passed", "human_note"]
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
                "judge_reason": extra.get("reason", ""), "prompt": case["prompt"], "answer": case["answer"],
                "human_passed": "", "human_note": "",
            })
    return rows


def parse_sample(spec: Optional[str]) -> tuple[str, int]:
    """``"all"`` -> ("all", 0); ``"random:20"`` -> ("random", 20). Default random:20."""
    spec = (spec or "random:20").strip().lower()
    if spec == "all":
        return "all", 0
    if spec.startswith("random:"):
        try:
            n = int(spec.split(":", 1)[1])
        except ValueError:
            raise ValueError(f"--sample {spec!r}: expected random:<N>") from None
        if n < 0:
            raise ValueError("--sample random:<N> needs N >= 0")
        return "random", n
    raise ValueError(f"--sample {spec!r}: expected 'all' or 'random:<N>'")


def build_sheet(run: dict[str, Any], run_path: str = "", sample: Optional[str] = None,
                seed: int = 0) -> list[dict[str, Any]]:
    """All failed judged checks + a sample of passed ones (deterministic for a given seed)."""
    mode, n = parse_sample(sample)
    rows = judged_checks(run, run_path)
    failed = [r for r in rows if not r["judge_passed"]]
    passed = [r for r in rows if r["judge_passed"]]
    if mode == "random" and len(passed) > n:
        passed = random.Random(seed).sample(passed, n)
    passed.sort(key=lambda r: (r["case_id"], r["check_index"]))
    return failed + passed


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


def agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge-vs-human agreement overall and per judge version; unlabelled rows are ignored."""
    def bucket(group: list[dict[str, Any]]) -> dict[str, Any]:
        st = {"n": 0, "agree": 0, "false_pass": 0, "false_fail": 0}
        for r in group:
            human = parse_label(r.get("human_passed"))
            if human is None:
                continue
            judge = _to_bool(r.get("judge_passed"))
            st["n"] += 1
            if judge == human:
                st["agree"] += 1
            elif judge and not human:
                st["false_pass"] += 1  # judge accepted what a human rejected
            else:
                st["false_fail"] += 1
        st["agreement"] = (st["agree"] / st["n"]) if st["n"] else None
        return st

    by_judge: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_judge.setdefault(str(r.get("judge") or "?"), []).append(r)
    return {
        "labelled": sum(1 for r in rows if parse_label(r.get("human_passed")) is not None),
        "unlabelled": sum(1 for r in rows if parse_label(r.get("human_passed")) is None),
        "overall": bucket(rows),
        "per_judge": {j: bucket(g) for j, g in sorted(by_judge.items())},
        "disagreements": [{"case_id": r["case_id"], "judge": r.get("judge"), "judge_passed": _to_bool(r.get("judge_passed")),
                           "human_passed": parse_label(r.get("human_passed")), "judge_reason": r.get("judge_reason", ""),
                           "human_note": r.get("human_note", "")}
                          for r in rows if parse_label(r.get("human_passed")) is not None
                          and parse_label(r.get("human_passed")) != _to_bool(r.get("judge_passed"))],
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
        return [name, str(st["n"]), rate, str(st["false_pass"]), str(st["false_fail"])]

    out = [f"Judge audit: {result['labelled']} labelled, {result['unlabelled']} unlabelled rows", ""]
    rows = [row(j, st) for j, st in result["per_judge"].items()] + [row("ALL", result["overall"])]
    out.append(table(["judge", "n", "agreement", "judge pass/human fail", "judge fail/human pass"], rows))
    if result["disagreements"]:
        out += ["", "Disagreements:"]
        for d in result["disagreements"]:
            out.append(f"  - {d['case_id']} [{d['judge']}] judge={'pass' if d['judge_passed'] else 'fail'} "
                       f"human={'pass' if d['human_passed'] else 'fail'}: {d['judge_reason'][:80]}"
                       + (f" | note: {d['human_note'][:60]}" if d.get("human_note") else ""))
    return "\n".join(out)
