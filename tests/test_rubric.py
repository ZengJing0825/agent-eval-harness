import tempfile
import unittest
from pathlib import Path

from harness import judge, rubric, scorers


def _latest(kind: str) -> str:
    """`<kind>.v<highest>` - the prompt version the harness picks automatically."""
    from harness import judge as _judge
    return f"{kind}.v{_judge.list_prompt_versions()[kind][-1]}"

RUBRICS = Path(__file__).resolve().parents[1] / "rubrics"


class RubricMathTests(unittest.TestCase):
    def test_bundled_rubric_loads_and_validates(self):
        doc = rubric.load_rubric("research_answer")
        self.assertEqual(sum(d["weight"] for d in doc["dimensions"]), 100)
        self.assertEqual(doc["pass_band"], "C")
        with self.assertRaises(FileNotFoundError):
            rubric.load_rubric("missing")

    def test_validation_rules(self):
        base = {"name": "r", "dimensions": [{"id": "a", "weight": 60}, {"id": "b", "weight": 40}]}
        rubric.validate_rubric(dict(base))
        with self.assertRaises(ValueError):
            rubric.validate_rubric({**base, "dimensions": [{"id": "a", "weight": 50}]})
        with self.assertRaises(ValueError):
            rubric.validate_rubric({**base, "gates": [{"id": "g", "cap": "B", "forbidden": ["x"]}]})
        with self.assertRaises(ValueError):
            rubric.validate_rubric({**base, "gates": [{"id": "g", "cap": "F"}]})  # no rule and no judge
        with self.assertRaises(ValueError):
            rubric.validate_rubric({**base, "dimensions": [{"id": "a", "weight": 50}, {"id": "a", "weight": 50}]})

    def test_points_bands_and_caps(self):
        dims = [{"id": "a", "weight": 60}, {"id": "b", "weight": 40}]
        bands = {"A": 90, "B": 75, "C": 60, "D": 40}
        self.assertEqual(rubric.total_points(dims, {"a": 5, "b": 5}), 100)
        self.assertEqual(rubric.total_points(dims, {"a": 5, "b": 0}), 60)
        self.assertEqual(rubric.grade_for(92, bands), "A")
        self.assertEqual(rubric.grade_for(60, bands), "C")
        self.assertEqual(rubric.grade_for(39.9, bands), "F")
        self.assertEqual(rubric.cap_points(100, "F", bands), 0)
        self.assertEqual(rubric.cap_points(100, "C", bands), 74)
        self.assertEqual(rubric.cap_points(50, "C", bands), 50)
        self.assertEqual(rubric.worst_cap(["C", "F", "C"]), "F")
        self.assertIsNone(rubric.worst_cap([]))
        self.assertTrue(rubric.grade_at_least("B", "C"))
        self.assertFalse(rubric.grade_at_least("D", "C"))


class RubricScorerTests(unittest.TestCase):
    CHECK = {"type": "rubric", "name": "research_answer"}
    GOOD = {"answer": "Nvidia (NVDA) is scheduled to report earnings on 2026-11-18, according to the reference "
                      "calendar; the date matters because the figures are released then.",
            "citations": ["fixture:agents/fixtures/market.json"]}

    def tearDown(self):
        judge.configure("none")

    def test_deterministic_f_gate_fails_without_any_judge(self):
        judge.configure("none")
        s = scorers.run_check({"answer": "You should buy now, it is a strong buy.", "citations": ["x"]}, self.CHECK, "q")
        self.assertEqual((s.score, s.passed), (0.0, False))
        self.assertEqual(s.extra["grade"], "F")
        self.assertEqual(s.extra["gate_hits"], ["no_advice"])
        self.assertEqual([g["how"] for g in s.extra["gates"]][:3], ["deterministic"] * 3)

    def test_skipped_without_judge_when_dimensions_are_needed(self):
        judge.configure("none")
        s = scorers.run_check(self.GOOD, self.CHECK, "q")
        self.assertTrue(s.skipped)
        self.assertIn("judge skipped", s.detail)
        self.assertEqual(s.extra["gate_hits"], [])

    def test_fake_judge_grades_dimensions_and_records_everything(self):
        judge.configure("fake")
        s = scorers.run_check(self.GOOD, self.CHECK, "When does Nvidia next report earnings? Cite your source.")
        self.assertIsNotNone(s.score)
        self.assertEqual([d["id"] for d in s.extra["dimensions"]], ["accuracy", "completeness", "reasoning", "clarity"])
        self.assertTrue(all(0 <= d["score"] <= 5 and d["reason"] for d in s.extra["dimensions"]))
        self.assertEqual(s.extra["judge"], _latest("dimension"))
        self.assertEqual(s.extra["rubric"], "research_answer.v1")
        self.assertIn(s.extra["grade"], rubric.GRADES)
        self.assertAlmostEqual(s.score, s.extra["points"] / 100, places=4)
        self.assertEqual(s.extra["gate_hits"], [])
        on_topic = next(g for g in s.extra["gates"] if g["id"] == "on_topic")
        self.assertEqual(on_topic["how"], "judge")
        self.assertEqual(on_topic["judge"], _latest("requirement"))

    def test_c_cap_limits_grade(self):
        judge.configure("fake")
        no_source = {**self.GOOD, "citations": []}
        s = scorers.run_check(no_source, self.CHECK, "When does Nvidia next report earnings?")
        self.assertEqual(s.extra["gate_hits"], ["has_source"])
        self.assertIn(s.extra["grade"], ("C", "D", "F"))
        self.assertLessEqual(s.extra["points"], 74)
        self.assertGreaterEqual(s.extra["raw_points"], s.extra["points"])

    def test_min_grade_controls_pass(self):
        judge.configure("fake")
        strict = scorers.run_check(self.GOOD, {**self.CHECK, "min_grade": "A"}, "q")
        lax = scorers.run_check(self.GOOD, {**self.CHECK, "min_grade": "F"}, "q")
        self.assertTrue(lax.passed)
        self.assertEqual(strict.passed, strict.extra["grade"] == "A")

    def test_custom_rubric_dir_and_lint_error_for_bad_rubric(self):
        from harness import lint
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.yaml").write_text("name: bad\ndimensions:\n  - {id: a, weight: 10}\n")
            (Path(tmp) / "t.yaml").write_text("version: 1\ntool: t\ncases:\n"
                                              f"  - {{id: a, prompt: p, scorer: rubric, name: bad, rubrics_dir: {tmp}}}\n")
            issues, _, _ = lint.lint(tmp)
            self.assertTrue(any("rubric problem" in i.message and i.level == "error" for i in issues))


if __name__ == "__main__":
    unittest.main()
