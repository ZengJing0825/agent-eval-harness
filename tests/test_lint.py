import tempfile
import unittest
from pathlib import Path

from harness import lint
from harness.cases import load_cases
from harness.runner import run_agent

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"

BAD = """
version: 1
tool: t
cases:
  - {id: dup, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: x, status: agreed}}
  - {id: dup, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: x, status: agreed}}
  - {id: unk, prompt: p, scorer: nope, expected: x, answer: {owner: x, peer: x, status: agreed}}
  - {id: disp, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: {reviewer: r, verdict: disagree, note: "UTC or local?"}, status: disputed}}
  - {id: differ, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: {reviewer: r, verdict: disagree}, status: agreed}}
  - {id: legacy_differ, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: y, status: agreed}}
  - {id: nopeer, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: null, status: draft}}
  - {id: noblock, prompt: p, scorer: exact, expected: x}
  - {id: tinytol, prompt: p, scorer: numeric, expected: 12345, tolerance: 0.01, answer: {owner: 12345, peer: "12,345", status: agreed}}
  - {id: oktol, prompt: p, scorer: tolerance, expected: 12345, abs: 50, answer: {owner: 12345, peer: 12345, status: agreed}}
  - {id: reltol, prompt: p, scorer: tolerance, expected: 12345, rel: 0.0001, answer: {owner: 12345, peer: 12345, status: agreed}}
  - {id: small, prompt: p, scorer: numeric, expected: 25, tolerance: 0.001, answer: {owner: "25%", peer: "25.0", status: agreed}}
"""


class LintTests(unittest.TestCase):
    def _lint(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "t.yaml").write_text(text)
            return lint.lint(tmp)

    def test_bundled_set_has_no_errors(self):
        issues, n_cases, n_files = lint.lint(GOLDEN)
        self.assertFalse(lint.has_errors(issues), [str(i) for i in issues])
        self.assertGreaterEqual(n_files, 7)
        self.assertIn("Lint: 0 error(s)", lint.render(issues, n_cases, n_files))

    def test_each_rule_fires_once(self):
        issues, n_cases, _ = self._lint(BAD)
        self.assertEqual(n_cases, 12)
        by_id = {}
        for i in issues:
            by_id.setdefault(i.case_id, []).append((i.level, i.message))
        self.assertEqual([lvl for lvl, _ in by_id["dup"]], ["error"])
        self.assertIn("unknown scorer", by_id["unk"][0][1])
        self.assertEqual(by_id["disp"][0][0], "error")
        self.assertIn("re-review", by_id["disp"][0][1])
        self.assertIn("UTC or local?", by_id["disp"][0][1])
        self.assertEqual(by_id["differ"], [("error", by_id["differ"][0][1])])
        self.assertIn("'disagree' but status=agreed", by_id["differ"][0][1])
        self.assertEqual(by_id["legacy_differ"][0][0], "error")
        self.assertIn("peer wrote a different answer", by_id["legacy_differ"][0][1])
        self.assertEqual(by_id["nopeer"][0][0], "warning")
        self.assertIn("missing peer", by_id["noblock"][0][1])
        self.assertEqual(by_id["tinytol"][0][0], "warning")
        self.assertIn("0.1%", by_id["tinytol"][0][1])
        for ok in ("oktol", "reltol", "small"):
            self.assertNotIn(ok, by_id)
        self.assertTrue(lint.has_errors(issues))

    def test_warnings_only_means_no_errors(self):
        issues, _, _ = self._lint("version: 1\ntool: t\ncases:\n  - {id: a, prompt: p, scorer: exact, expected: x}\n")
        self.assertEqual([i.level for i in issues], ["warning"])
        self.assertFalse(lint.has_errors(issues))

    def test_unloadable_file_is_an_error_not_a_crash(self):
        issues, _, n_files = self._lint("version: 1\ntool: t\ncases:\n  - {id: a, prompt: p, scorer: exact, expected: x, answer: {status: weird}}\n")
        self.assertEqual(n_files, 1)
        self.assertEqual(issues[0].level, "error")
        self.assertIn("does not load", issues[0].message)

    def test_normalise_answer_text(self):
        n = lint.normalise_answer_text
        self.assertEqual(n("25%"), n("25.0"))
        self.assertEqual(n("12,345"), n(12345))
        self.assertEqual(n("  AAPL. "), n("aapl"))
        self.assertNotEqual(n("x"), n("y"))


class StatusTests(unittest.TestCase):
    def test_loader_validates_answer_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "t.yaml").write_text("version: 1\ntool: t\ncases:\n"
                                              "  - {id: a, prompt: p, scorer: exact, expected: x, answer: {owner: x}}\n")
            case = load_cases(tmp)[0]
            self.assertEqual(case.status, "draft")
            self.assertEqual(case.review, {"reviewer": None, "verdict": None, "note": None})
            (Path(tmp) / "t.yaml").write_text("version: 1\ntool: t\ncases:\n"
                                              "  - {id: a, prompt: p, scorer: exact, expected: x, answer: {bogus: 1}}\n")
            with self.assertRaises(ValueError):
                load_cases(tmp)

    def test_run_skips_unagreed_unless_included(self):
        cases = load_cases(GOLDEN, tools=["multi_step"])
        draft = [c for c in cases if not c.agreed]
        self.assertEqual([c.id for c in draft], ["complex-004"])
        agent = lambda p, c: {"answer": "MSFT reports first, 2 days apart.", "citations": []}  # noqa: E731
        run = run_agent("x", agent, cases)
        row = next(r for r in run["cases"] if r["id"] == "complex-004")
        self.assertIsNone(row["score"])
        self.assertIn("status=draft", row["skip_reason"])
        self.assertIn("status=draft (not agreed; use --include-unagreed)", run["summary"]["skip_reasons"])
        run = run_agent("x", agent, cases, include_unagreed=True)
        row = next(r for r in run["cases"] if r["id"] == "complex-004")
        self.assertTrue(row["passed"])

    def test_unagreed_cases_do_not_count_towards_gates(self):
        cases = load_cases(GOLDEN, tools=["multi_step"])
        run = run_agent("x", lambda p, c: {"answer": ""}, cases, gates={"complex": 0.0})
        self.assertEqual(run["gates"][0]["n_scored"], 3)


if __name__ == "__main__":
    unittest.main()


class PeerReviewTests(unittest.TestCase):
    """``answer.peer`` is a review record; ``status`` is derived from its verdict when absent."""

    def _load(self, answer_yaml):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "t.yaml").write_text("version: 1\ntool: t\ncases:\n"
                                              f"  - {{id: a, prompt: p, scorer: exact, expected: x, answer: {answer_yaml}}}\n")
            return load_cases(tmp)[0]

    def test_status_is_derived_from_the_verdict(self):
        self.assertEqual(self._load("{owner: x, peer: {reviewer: r, verdict: agree}}").status, "agreed")
        self.assertEqual(self._load("{owner: x, peer: {reviewer: r, verdict: disagree, note: n}}").status, "disputed")
        self.assertEqual(self._load("{owner: x, peer: {reviewer: r}}").status, "draft")
        self.assertEqual(self._load("{owner: x}").status, "draft")
        # an explicit status wins over the derived one (lint then checks it is consistent)
        self.assertEqual(self._load("{owner: x, peer: {verdict: agree}, status: draft}").status, "draft")

    def test_review_record_is_normalised(self):
        case = self._load("{owner: x, peer: {reviewer: 7, verdict: AGREE, note: ok}}")
        self.assertEqual(case.review, {"reviewer": "7", "verdict": "agree", "note": "ok"})
        for bad in ("{owner: x, peer: {verdict: maybe}}", "{owner: x, peer: {who: r}}", "{owner: x, peer: [r]}"):
            with self.assertRaises(ValueError):
                self._load(bad)

    def test_legacy_string_peer_becomes_a_verdict(self):
        same = self._load('{owner: "25%", peer: "25.0"}')
        self.assertEqual((same.status, same.review["verdict"], same.review["note"]), ("agreed", "agree", None))
        other = self._load('{owner: "25%", peer: "26"}')
        self.assertEqual((other.status, other.review["verdict"]), ("disputed", "disagree"))
        self.assertIn("peer wrote a different answer", other.review["note"])
        self.assertIn("'26'", other.review["note"])

    def test_lint_rules_for_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "t.yaml").write_text("version: 1\ntool: t\ncases:\n"
                "  - {id: ok, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: {reviewer: r, verdict: agree}}}\n"
                "  - {id: disp, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: {reviewer: r, verdict: disagree}}}\n"
                "  - {id: unrev, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: {reviewer: r}}}\n"
                "  - {id: bad, prompt: p, scorer: exact, expected: x, answer: {owner: x, peer: {verdict: disagree}, status: agreed}}\n")
            issues, _, _ = lint.lint(tmp)
        levels = {}
        for i in issues:
            levels.setdefault(i.case_id, []).append(i.level)
        self.assertNotIn("ok", levels)
        self.assertEqual(levels["disp"], ["error"])
        self.assertEqual(levels["unrev"], ["warning"])
        self.assertEqual(levels["bad"], ["error"])

    def test_bundled_set_uses_review_records(self):
        cases = load_cases(GOLDEN)
        reviewed = [c for c in cases if c.review.get("verdict")]
        self.assertGreaterEqual(len(reviewed), 25)
        self.assertTrue(all(c.review["verdict"] == "agree" and c.status == "agreed" for c in reviewed))
        draft = next(c for c in cases if c.id == "complex-004")
        self.assertEqual((draft.status, draft.review["verdict"]), ("draft", None))
