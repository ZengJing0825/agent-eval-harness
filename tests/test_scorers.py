import os
import unittest
from unittest import mock

from harness import judge, scorers


class ScorerTests(unittest.TestCase):
    def test_exact_normalises_case_and_whitespace(self):
        s = scorers.exact({"answer": "  aapl "}, {"expected": "AAPL"})
        self.assertTrue(s.passed)
        self.assertFalse(scorers.exact({"answer": "aapl"}, {"expected": "AAPL", "case_sensitive": True}).passed)

    def test_contains_accepts_list_and_reports_missing(self):
        ok = scorers.contains({"answer": "The ticker is MSFT, date 2026-10-27"}, {"expected": ["MSFT", "2026-10-27"]})
        self.assertEqual(ok.score, 1.0)
        bad = scorers.contains({"answer": "MSFT"}, {"expected": ["MSFT", "2026"]})
        self.assertFalse(bad.passed)
        self.assertIn("2026", bad.detail)

    def test_regex(self):
        self.assertTrue(scorers.regex({"answer": "ticker GOOGL."}, {"pattern": r"\bGOOGL\b"}).passed)
        self.assertFalse(scorers.regex({"answer": "GOOGLE"}, {"pattern": r"\bGOOGL\b"}).passed)

    def test_numeric_tolerance_and_formatting(self):
        self.assertTrue(scorers.numeric({"answer": "That is a 25.00% change."}, {"expected": 25, "tolerance": 0.01}).passed)
        self.assertTrue(scorers.numeric({"answer": "-9.94%"}, {"expected": -9.9, "tolerance": 0.05}).passed)
        self.assertTrue(scorers.numeric({"answer": "about $1,250"}, {"expected": 1250}).passed)
        self.assertFalse(scorers.numeric({"answer": "no digits here"}, {"expected": 1}).passed)
        self.assertTrue(scorers.numeric({"answer": "110"}, {"expected": 100, "tolerance": 0.2, "relative": True}).passed)

    def test_json_key_from_data_and_from_text(self):
        self.assertTrue(scorers.json_key({"answer": "x", "data": {"ticker": "MSFT"}}, {"path": "ticker", "expected": "MSFT"}).passed)
        self.assertTrue(scorers.json_key({"answer": '{"a": {"b": [1, 2]}}'}, {"path": "a.b.1", "expected": 2}).passed)
        self.assertFalse(scorers.json_key({"answer": "plain text"}, {"path": "a", "expected": 1}).passed)
        self.assertFalse(scorers.json_key({"answer": "", "data": {}}, {"path": "missing", "expected": 1}).passed)

    def test_policy_forbidden_phrases(self):
        chk = {"forbidden": ["strong buy", "buy now"]}
        self.assertTrue(scorers.policy({"answer": "I can't give advice."}, chk).passed)
        self.assertFalse(scorers.policy({"answer": "Consensus is a Strong Buy."}, chk).passed)

    def test_citation_min(self):
        self.assertTrue(scorers.citation({"answer": "", "citations": ["src"]}, {}).passed)
        self.assertFalse(scorers.citation({"answer": "", "citations": ["", None]}, {}).passed)
        self.assertFalse(scorers.citation({"answer": "", "citations": ["a"]}, {"min": 2}).passed)

    def test_llm_judge_skips_without_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", judge.ENV_VAR: "auto"}):
            judge.configure()
            s = scorers.llm_judge({"answer": "x"}, {"rubric": "r"})
        self.assertTrue(s.skipped)
        self.assertIsNone(s.passed)

    def test_run_check_unknown_type(self):
        with self.assertRaises(KeyError):
            scorers.run_check({"answer": ""}, {"type": "nope"})


if __name__ == "__main__":
    unittest.main()
