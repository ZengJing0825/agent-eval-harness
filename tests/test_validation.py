"""Validation-field rubrics: the format answer authors write -> harness checks."""
import json
import unittest

from harness import cases, judge, scorers
from harness.validation import RubricError, checks_from_rubric, parse_range_text


class RubricConversionTests(unittest.TestCase):
    def test_range_keyword_requirement_with_calculation_folded_in(self):
        rubric = json.dumps([
            {"validation field": "range", "criteria": {"value": 6.617, "tolerance": 0.05, "unit": "USD per share"}},
            {"validation field": "keyword", "criteria": ["free cash flow", "outstanding shares"]},
            {"validation field": "calculation", "criteria": "1,320 / 199.5 = 6.617"},
            {"validation field": "requirement", "requirement": "The answer must show the division."},
        ])
        checks = checks_from_rubric(rubric, "demo")
        self.assertEqual([c["type"] for c in checks], ["range", "keyword", "requirement"])
        self.assertEqual(checks[0]["value"], 6.617)
        self.assertEqual(checks[0]["unit"], "USD per share")
        self.assertEqual(checks[1]["points"], ["free cash flow", "outstanding shares"])
        self.assertEqual(checks[2]["calculation"], "1,320 / 199.5 = 6.617")  # folded in, not its own check

    def test_range_written_as_a_sentence_is_parsed(self):
        parsed = parse_range_text("utilization rate on 2025/08/01 = 41.87% with +/-2% tolerance")
        self.assertEqual(parsed, {"value": 41.87, "unit": "%", "tolerance": 2.0})
        checks = checks_from_rubric([{"validation field": "range",
                                      "criteria": "ratio = 41.87% with +/-2% tolerance"}])
        self.assertEqual(checks[0]["type"], "range")
        self.assertAlmostEqual(checks[0]["tolerance"], 2.0)

    def test_range_sentence_without_a_number_falls_back_to_a_judged_requirement(self):
        checks = checks_from_rubric([{"validation field": "range", "criteria": "roughly in line with peers"}])
        self.assertEqual(checks[0]["type"], "requirement")
        self.assertEqual(checks[0]["from_field"], "range")

    def test_correctness_value_vs_sentence(self):
        value = checks_from_rubric([{"validation field": "correctness", "criteria": "AAPL"}])
        self.assertEqual(value[0], {"type": "correctness", "expected": "AAPL"})
        sentence = checks_from_rubric([{"validation field": "correctness",
                                        "criteria": "The answer must state that ADBE has the largest share."}])
        self.assertEqual(sentence[0]["type"], "requirement")  # judged, as the original evaluator did
        self.assertEqual(sentence[0]["from_field"], "correctness")

    def test_boolean_statement_becomes_a_judged_requirement(self):
        checks = checks_from_rubric([{"validation field": "boolean", "criteria": {"statement": "OKB burned: Yes"}}])
        self.assertEqual(checks[0]["type"], "requirement")
        self.assertIn("OKB burned: Yes", checks[0]["text"])

    def test_one_rubric_one_requirement(self):
        with self.assertRaises(RubricError) as cm:
            checks_from_rubric([{"validation field": "requirement", "requirement": "a"},
                                {"validation field": "requirement", "requirement": "b"}])
        self.assertIn("a rubric holds one", str(cm.exception))

    def test_unknown_field_and_bad_json(self):
        with self.assertRaises(RubricError):
            checks_from_rubric([{"validation field": "vibes", "criteria": "nice"}])
        with self.assertRaises(RubricError):
            checks_from_rubric("{not json")


class RangeFormTests(unittest.TestCase):
    def test_value_tolerance_form_scores_like_bounds(self):
        check = {"type": "range", "value": 6.617, "tolerance": 0.05, "unit": "USD per share"}
        ok = scorers.run_check({"answer": "about 6.62 USD per share"}, check)
        self.assertTrue(ok.passed)
        self.assertEqual(ok.extra["unit"], "USD per share")
        bad = scorers.run_check({"answer": "about 7.1 USD per share"}, check)
        self.assertFalse(bad.passed)

    def test_relative_tolerance_and_bad_configuration(self):
        rel = scorers.run_check({"answer": "1,010"}, {"type": "range", "value": 1000, "tolerance": 0.02,
                                                     "relative": True})
        self.assertTrue(rel.passed)
        with self.assertRaises(ValueError):
            scorers.run_check({"answer": "1"}, {"type": "range", "lo": 1})
        with self.assertRaises(ValueError):
            scorers.run_check({"answer": "1"}, {"type": "range"})

    def test_field_form_reads_structured_data(self):
        check = {"type": "range", "field": "signals.0.instruction.weights.0.weight", "value": 1.0, "tolerance": 0}
        answer = {"answer": "{}", "data": {"signals": [{"instruction": {"weights": [{"weight": 1}]}}]}}
        self.assertTrue(scorers.run_check(answer, check).passed)


class CaseLoadingTests(unittest.TestCase):
    def test_case_may_carry_validation_fields(self):
        raw = {"id": "c1", "prompt": "q",
               "validation_fields": [{"validation field": "range", "criteria": {"value": 1, "tolerance": 0}}]}
        self.assertEqual(cases.normalise_checks(raw), [{"type": "range", "value": 1, "tolerance": 0}])

    def test_validation_fields_and_checks_are_exclusive(self):
        with self.assertRaises(ValueError):
            cases.normalise_checks({"id": "c1", "prompt": "q", "scorer": "exact", "expected": "x",
                                    "validation_fields": [{"validation field": "requirement",
                                                           "requirement": "r"}]})

    def test_the_shipped_set_has_a_validation_field_case(self):
        loaded = {c.id: c for c in cases.load_cases("cases/golden")}
        self.assertIn("complex-005", loaded)
        self.assertEqual([c["type"] for c in loaded["complex-005"].checks], ["range", "keyword", "requirement"])
        self.assertIn("1,320,000,000", loaded["complex-005"].checks[2]["calculation"])


class StrategyCaseTests(unittest.TestCase):
    def test_strategy_case_scores_signal_fields_and_unsupported_is_marked(self):
        loaded = {c.id: c for c in cases.load_cases("cases/golden")}
        self.assertEqual([c["type"] for c in loaded["strat-001"].checks],
                         ["json_key", "json_key", "json_key", "range"])
        self.assertTrue(loaded["strat-002"].unsupported)


class FailureClassTests(unittest.TestCase):
    def test_classes_map_to_a_bad_case_category(self):
        self.assertEqual(set(judge.FAILURE_CATEGORY), set(judge.FAILURE_CLASSES))
        self.assertEqual(judge.FAILURE_CATEGORY["E1"], "tool_choice")
        self.assertIn("E1:", judge.failure_classes_text())

    def test_normalisation(self):
        self.assertEqual(judge.normalise_failure_class("e2 - mismatch"), "E2")
        self.assertIsNone(judge.normalise_failure_class("none"))
        self.assertIsNone(judge.normalise_failure_class(None))

    def test_latest_prompts_ask_for_a_failure_class(self):
        for name in ("requirement", "rubric"):
            _, tpl = judge.load_prompt(name)
            self.assertIn("failure_class", tpl.template, name)
            self.assertIn("${failure_classes}", tpl.template, name)

    def test_prompts_take_the_class_list_from_the_placeholder(self):
        """Adding a class must not need a new prompt version: no hard-coded E1..E4 list."""
        for name in ("requirement", "rubric"):
            _, tpl = judge.load_prompt(name)
            self.assertNotIn('"E1" | "E2"', tpl.template, name)

    def test_fake_judge_reports_a_class_only_on_failure(self):
        j = judge.FakeJudge()
        empty = j.evaluate("requirement", question="q", answer="", requirement="states AAPL")
        self.assertEqual(empty["failure_class"], "E3")
        wrong = j.evaluate("requirement", question="q", answer="it is 19900.00%", requirement="zzz qqq")
        self.assertEqual(wrong["failure_class"], "E2")
        good = j.evaluate("requirement", question="ticker for Apple", answer="AAPL is the ticker",
                          requirement="ticker")
        self.assertNotIn("failure_class", good)

    def test_counting_over_a_run(self):
        run_cases = [{"checks": [{"extra": {"failure_class": "E2"}}, {"extra": {}}]},
                     {"checks": [{"extra": {"failure_class": "E2"}}, {"extra": {"failure_class": "E1"}}]}]
        self.assertEqual(judge.count_failure_classes(run_cases), {"E1": 1, "E2": 2})


if __name__ == "__main__":
    unittest.main()
