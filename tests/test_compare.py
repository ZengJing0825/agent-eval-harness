import unittest

from harness.compare import compare_runs, outcome
from harness.runner import summarise


def _run(agent, scores):
    rows = [{"id": f"c{i}", "tool": "t" if i % 2 else "u", "prompt": "p", "answer": str(s),
             "score": s, "passed": (None if s is None else s >= 1.0)} for i, s in enumerate(scores)]
    return {"agent": agent, "timestamp": "2026-01-01T00:00:00Z", "n_cases": len(rows),
            "summary": summarise(rows), "cases": rows}


class CompareTests(unittest.TestCase):
    def test_outcome(self):
        self.assertEqual(outcome(0.0, 1.0), "win")
        self.assertEqual(outcome(1.0, 0.0), "loss")
        self.assertEqual(outcome(0.5, 0.5), "tie")
        self.assertEqual(outcome(None, 1.0), "skip")

    def test_tally_and_per_tool(self):
        cmp = compare_runs(_run("a", [1.0, 0.0, 0.5, None]), _run("b", [1.0, 1.0, 0.0, 1.0]))
        self.assertEqual(cmp["tally"], {"win": 1, "loss": 1, "tie": 1, "skip": 1})
        self.assertEqual(cmp["per_tool"]["t"], {"win": 1, "loss": 0, "tie": 0, "skip": 1})
        self.assertEqual(cmp["per_tool"]["u"], {"win": 0, "loss": 1, "tie": 1, "skip": 0})

    def test_unmatched_cases_are_reported(self):
        a, b = _run("a", [1.0, 1.0]), _run("b", [1.0])
        b["cases"].append({**b["cases"][0], "id": "extra"})
        cmp = compare_runs(a, b)
        self.assertEqual(cmp["unmatched"], {"only_in_a": ["c1"], "only_in_b": ["extra"]})
        self.assertEqual(len(cmp["cases"]), 1)

    def test_summarise_excludes_skipped(self):
        s = summarise(_run("a", [1.0, 0.0, None])["cases"])
        self.assertEqual(s["overall"]["skipped"], 1)
        self.assertAlmostEqual(s["overall"]["pass_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
