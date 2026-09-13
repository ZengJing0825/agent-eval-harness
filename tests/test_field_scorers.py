import unittest
from pathlib import Path

from harness import judge, scorers


def _latest(kind: str) -> str:
    """`<kind>.v<highest>` - the prompt version the harness picks automatically."""
    from harness import judge as _judge
    return f"{kind}.v{_judge.list_prompt_versions()[kind][-1]}"


class FieldScorerTests(unittest.TestCase):
    def tearDown(self):
        judge.configure("none")

    def test_correctness_string_and_number(self):
        self.assertTrue(scorers.correctness({"answer": " AAPL "}, {"expected": "aapl"}).passed)
        self.assertTrue(scorers.correctness({"answer": "It is 25.0%"}, {"expected": 25}).passed)
        self.assertFalse(scorers.correctness({"answer": "It is 25.01%"}, {"expected": 25}).passed)

    def test_range_from_text_and_field(self):
        self.assertTrue(scorers.value_range({"answer": "roughly 33.3 percent"}, {"lo": 33, "hi": 34}).passed)
        self.assertFalse(scorers.value_range({"answer": "roughly 35 percent"}, {"lo": 33, "hi": 34}).passed)
        ok = scorers.value_range({"answer": "n/a", "data": {"pct": 33.5}}, {"lo": 33, "hi": 34, "field": "pct"})
        self.assertTrue(ok.passed)
        self.assertFalse(scorers.value_range({"answer": "33.5", "data": {}}, {"lo": 33, "hi": 34, "field": "pct"}).passed)
        with self.assertRaises(ValueError):
            scorers.value_range({"answer": "1"}, {"lo": 2, "hi": 1})

    def test_keyword_partial_credit(self):
        chk = {"points": ["old", "new", "100"], "min_hit": 2}
        s = scorers.keyword({"answer": "(new - old) / old"}, chk)
        self.assertAlmostEqual(s.score, 2 / 3)
        self.assertTrue(s.passed)
        self.assertEqual(s.extra["missing"], ["100"])
        self.assertFalse(scorers.keyword({"answer": "(new - old) / old"}, {"points": ["old", "new", "100"]}).passed)
        with self.assertRaises(ValueError):
            scorers.keyword({"answer": "x"}, {"points": []})

    def test_tolerance_abs_and_rel(self):
        self.assertTrue(scorers.tolerance({"answer": "25.004"}, {"expected": 25, "abs": 0.01}).passed)
        self.assertFalse(scorers.tolerance({"answer": "25.02"}, {"expected": 25, "abs": 0.01}).passed)
        self.assertTrue(scorers.tolerance({"answer": "1050"}, {"expected": 1000, "rel": 0.05}).passed)
        self.assertFalse(scorers.tolerance({"answer": "1000.5"}, {"expected": 1000}).passed)  # default abs 0
        with self.assertRaises(ValueError):
            scorers.tolerance({"answer": "1"}, {"expected": 1, "abs": 1, "rel": 1})

    def test_requirement_skips_or_falls_back_without_judge(self):
        judge.configure("none")
        s = scorers.requirement({"answer": "x"}, {"text": "says hello"})
        self.assertTrue(s.skipped)
        s = scorers.requirement({"answer": "hello world"}, {"text": "says hello", "must_contain_any": ["hello"]})
        self.assertTrue(s.passed and s.extra["fallback"])
        self.assertFalse(scorers.requirement({"answer": "bye"}, {"text": "t", "must_contain_any": ["hello"]}).passed)

    def test_requirement_with_fake_judge_records_reason_and_version(self):
        judge.configure("fake")
        s = scorers.run_check({"answer": "The formula divides the difference by the old value."},
                              {"type": "requirement", "text": "Uses the formula (new - old) / old"}, prompt="q")
        self.assertTrue(s.passed)
        self.assertEqual(s.extra["judge"], _latest("requirement"))
        self.assertEqual(s.extra["backend"], "fake")
        self.assertIn("fake judge", s.extra["reason"])
        self.assertFalse(scorers.requirement({"answer": ""}, {"text": "Uses the formula"}).passed)

    def test_llm_judge_with_fake_judge(self):
        judge.configure("fake")
        s = scorers.llm_judge({"answer": "percentage change formula"}, {"rubric": "mentions the formula", "threshold": 0.7})
        self.assertEqual(s.extra["judge"], _latest("rubric"))
        self.assertTrue(s.passed)
        self.assertFalse(scorers.llm_judge({"answer": "zzz"}, {"rubric": "mentions the formula"}).passed)

    def test_every_scorer_is_registered(self):
        for name in ("correctness", "range", "keyword", "requirement", "tolerance"):
            self.assertIn(name, scorers.SCORERS)


class JudgeModuleTests(unittest.TestCase):
    def tearDown(self):
        judge.configure("none")

    def test_prompt_versions_and_loading(self):
        versions = judge.list_prompt_versions()
        self.assertIn("requirement", versions)
        self.assertIn("rubric", versions)
        name, tpl = judge.load_prompt("requirement")
        self.assertEqual(name, f"requirement.v{versions['requirement'][-1]}")
        self.assertIn("${answer}", tpl.template)
        with self.assertRaises(FileNotFoundError):
            judge.load_prompt("requirement", version=999)
        with self.assertRaises(FileNotFoundError):
            judge.load_prompt("does-not-exist")

    def test_latest_prompts_state_the_three_rules(self):
        self.assertEqual(len(judge.RULES), 3)
        for name, versions in judge.list_prompt_versions().items():
            version, tpl = judge.load_prompt(name)
            self.assertEqual(version, f"{name}.v{versions[-1]}")
            self.assertGreaterEqual(versions[-1], 2, name)
            self.assertEqual(judge.prompt_states_rules(tpl.template), [], name)
        self.assertIn("1. The case rubric", judge.rules_text())
        self.assertEqual(judge.prompt_states_rules("nothing here"), list(judge.RULES))

    def test_rules_placeholder_is_filled_and_version_recorded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "requirement.v1.md").write_text("${rules}\nQ: ${question}\nA: ${answer}\nR: ${requirement}\n")
            captured = {}

            class Spy(judge.FakeJudge):
                def _judge(self, kind, prompt, fields):
                    captured["prompt"] = prompt
                    return {"verdict": "yes"}

            res = Spy(tmp).evaluate("requirement", question="q", answer="a", requirement="r", calculation=None)
            self.assertEqual(res["judge"], "requirement.v1")
            self.assertIn("2. An unmet requirement scores 0", captured["prompt"])
            self.assertNotIn("${rules}", captured["prompt"])

    def test_unmet_requirement_scores_zero_not_partial(self):
        judge.configure("fake")
        s = scorers.requirement({"answer": "zzz"}, {"text": "Uses the formula (new - old) / old"})
        self.assertEqual((s.score, s.passed), (0.0, False))

    def test_make_judge_specs(self):
        self.assertIsNone(judge.make_judge("none"))
        self.assertIsInstance(judge.make_judge("fake"), judge.FakeJudge)
        with self.assertRaises(ValueError):
            judge.make_judge("bogus")
        self.assertRegex(judge.make_judge("fake").version(), r"^fake:.*requirement\.v\d+")

    def test_env_var_selects_backend(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {judge.ENV_VAR: "fake"}):
            self.assertIsInstance(judge.configure(), judge.FakeJudge)


if __name__ == "__main__":
    unittest.main()
