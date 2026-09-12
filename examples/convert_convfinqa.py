#!/usr/bin/env python3
"""ConvFinQA (conversation-level train/dev/test.json) -> JSONL, one row per dialogue turn.

Each row's prompt is the filing excerpt, the earlier turns with their gold answers, and the
current question; expected is that turn's execution result (``annotation.exe_ans_list[i]``).

    python examples/convert_convfinqa.py data/test.json convfinqa_test.jsonl
    python -m harness import --jsonl convfinqa_test.jsonl --map "id=id,note=note" \\
        --source convfinqa --license MIT --scorer contains
"""
import json
import sys


def rows(text):
    return "\n".join(" | ".join(str(c) for c in r) for r in text) if text and isinstance(text[0], list) else "\n".join(text)


def convert(src, dst):
    with open(src, encoding="utf-8") as fh:
        conversations = json.load(fh)
    with open(dst, "w", encoding="utf-8") as out:
        for conv in conversations:
            ann = conv["annotation"]
            context = "\n".join(filter(None, [rows(conv.get("pre_text", [])), rows(conv.get("table", [])), rows(conv.get("post_text", []))]))
            history = []
            for i, (question, answer) in enumerate(zip(ann["dialogue_break"], ann["exe_ans_list"])):
                prompt = context + "\n\n" + "".join(f"Q: {q}\nA: {a}\n" for q, a in history) + f"Q: {question}"
                out.write(json.dumps({"id": f"{conv['id']}-t{i}", "prompt": prompt, "expected": str(answer),
                                      "note": f"turn {i + 1} of {len(ann['dialogue_break'])}"}, ensure_ascii=False) + "\n")
                history.append((question, answer))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(sys.argv[1], sys.argv[2])
