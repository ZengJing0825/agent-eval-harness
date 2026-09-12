import tempfile
import unittest
from pathlib import Path

from harness.cases import TIERS, load_cases
from harness.runner import parse_gates, run_agent, summarise

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"

YAML = """
version: 1
tool: t
tier: complex
cases:
  - {id: a, prompt: "a", scorer: exact, expected: ok}
  - {id: b, prompt: "b", scorer: exact, expected: ok, tier: unit}
  - {id: c, prompt: "c", scorer: exact, expected: ok, tier: unit}
  - {id: d, prompt: "d", scorer: exact, expected: ok, tier: dynamic}
"""


def _write(tmp: str, text: str = YAML, name: str = "t.yaml") -> Path:
    p = Path(tmp) / name
    p.write_text(text)
    return Path(tmp)


class TierTests(unittest.TestCase):
    def test_file_default_and_per_case_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = {c.id: c.tier for c in load_cases(_write(tmp))}
        self.assertEqual(cases, {"a": "complex", "b": "unit", "c": "unit", "d": "dynamic"})

    def test_default_tier_is_unit_and_unknown_tier_rejected(self):
        self.assertTrue(all(c.tier == "unit" for c in load_cases(GOLDEN, tools=["policy"])))
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "version: 1\ntool: t\ntier: nope\ncases: []\n")
            with self.assertRaises(ValueError):
                load_cases(tmp)

    def test_tier_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = sorted(c.id for c in load_cases(_write(tmp), tiers=["unit", "dynamic"]))
        self.assertEqual(ids, ["b", "c", "d"])
        self.assertIn("complex", {c.tier for c in load_cases(GOLDEN)})

    def test_parse_gates(self):
        self.assertEqual(parse_gates(["unit:0.9", "complex:0.5,external:1"]), {"unit": 0.9, "complex": 0.5, "external": 1.0})
        self.assertEqual(parse_gates(None), {})
        for bad in ("unit", "nope:0.5", "unit:2", "unit:x"):
            with self.assertRaises(ValueError):
                parse_gates([bad])

    def test_gate_stops_later_tiers_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = load_cases(_write(tmp))
        agent = lambda prompt, ctx: {"answer": "ok" if prompt == "b" else "no", "citations": []}  # noqa: E731
        run = run_agent("x", agent, cases, gates={"unit": 0.9})
        by_id = {r["id"]: r for r in run["cases"]}
        self.assertEqual([r["tier"] for r in run["cases"]], ["unit", "unit", "complex", "dynamic"])  # tier order
        self.assertEqual(run["gates"], [{"tier": "unit", "threshold": 0.9, "pass_rate": 0.5, "passed": False, "n_scored": 2}])
        self.assertEqual(set(run["skipped_tiers"]), {"complex", "dynamic"})
        self.assertIsNone(by_id["a"]["score"])
        self.assertIn("gate unit:0.9 failed", by_id["a"]["skip_reason"])
        self.assertEqual(run["summary"]["per_tier"]["complex"]["skipped"], 1)
        self.assertEqual(sum(run["summary"]["skip_reasons"].values()), 2)

    def test_gate_passes_and_all_tiers_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = load_cases(_write(tmp))
        run = run_agent("x", lambda p, c: {"answer": "ok"}, cases, gates={"unit": 0.9, "complex": 0.5})
        self.assertEqual([g["passed"] for g in run["gates"]], [True, True])
        self.assertEqual(run["skipped_tiers"], {})
        self.assertEqual(run["summary"]["per_tier_tool"]["dynamic"]["t"]["pass_rate"], 1.0)

    def test_summarise_per_tier_ordering(self):
        rows = [{"id": i, "tool": "t", "tier": tier, "score": 1.0, "passed": True}
                for i, tier in enumerate(reversed(TIERS))]
        self.assertEqual(list(summarise(rows)["per_tier"]), list(TIERS))


if __name__ == "__main__":
    unittest.main()
