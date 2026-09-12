import tempfile
import unittest
from pathlib import Path

from harness.cases import load_cases, normalise_checks

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"


class LoaderTests(unittest.TestCase):
    def test_loads_bundled_golden_set(self):
        cases = load_cases(GOLDEN)
        self.assertGreaterEqual(len(cases), 15)
        tools = {c.tool for c in cases}
        self.assertTrue({"ticker_resolution", "earnings_date", "pct_change", "policy", "citation"} <= tools)
        self.assertEqual(len({c.id for c in cases}), len(cases))

    def test_tool_filter(self):
        cases = load_cases(GOLDEN, tools=["policy"])
        self.assertTrue(cases and all(c.tool == "policy" for c in cases))

    def test_short_form_normalised_to_checks(self):
        checks = normalise_checks({"id": "x", "prompt": "p", "scorer": "numeric", "expected": 1, "tolerance": 0.5})
        self.assertEqual(checks, [{"type": "numeric", "expected": 1, "tolerance": 0.5}])
        with self.assertRaises(ValueError):
            normalise_checks({"id": "x", "prompt": "p"})

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.yaml", "b.yaml"):
                (Path(tmp) / name).write_text(
                    "version: 1\ntool: t\ncases:\n  - {id: dup, prompt: p, scorer: exact, expected: x}\n"
                )
            with self.assertRaises(ValueError):
                load_cases(tmp)


if __name__ == "__main__":
    unittest.main()
