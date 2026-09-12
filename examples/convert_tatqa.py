#!/usr/bin/env python3
"""TAT-QA (dataset_raw/tatqa_dataset_{train,dev,test}.json) -> JSONL, one row per question.

Each context holds a table, paragraphs and several questions; the prompt is the paragraphs,
the table and the question (plus the expected unit/scale when the set gives one), expected is
the answer (multi-span answers joined with ``, ``), tool is the answer_type
(span / multi-span / arithmetic / count).

    python examples/convert_tatqa.py dataset_raw/tatqa_dataset_dev.json tatqa_dev.jsonl
    python -m harness import --jsonl tatqa_dev.jsonl --map "id=id,tool=tool,note=note" \\
        --source tat-qa --license "CC BY 4.0"
"""
import json
import sys


def convert(src, dst):
    with open(src, encoding="utf-8") as fh:
        contexts = json.load(fh)
    with open(dst, "w", encoding="utf-8") as out:
        for ctx in contexts:
            paragraphs = "\n".join(p["text"] for p in sorted(ctx.get("paragraphs", []), key=lambda p: p.get("order", 0)))
            table = "\n".join(" | ".join(str(c) for c in row) for row in ctx.get("table", {}).get("table", []))
            for q in ctx.get("questions", []):
                if "answer" not in q:  # test split before the 2024 ground-truth release
                    continue
                answer = q["answer"]
                expected = ", ".join(str(a) for a in answer) if isinstance(answer, list) else str(answer)
                scale = q.get("scale") or ""
                prompt = f"{paragraphs}\n\n{table}\n\n{q['question']}" + (f" (answer in {scale})" if scale else "")
                out.write(json.dumps({"id": q["uid"], "prompt": prompt, "expected": expected, "tool": q.get("answer_type", "tatqa"),
                                      "note": f"answer_from={q.get('answer_from', '')} scale={scale}"}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(sys.argv[1], sys.argv[2])
