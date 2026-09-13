import tempfile
import unittest
from pathlib import Path

from harness import judge, markdown, runner
from harness.cases import load_cases
from harness.cli import main
from harness.compare import compare_runs

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"


class MarkdownTests(unittest.TestCase):
    def setUp(self):
        judge.configure("fake")
        cases = load_cases(GOLDEN)
        # strict mode reproduces the classic gate behaviour: baseline misses unit:0.9 and stops there
        self.base = runner.run_agent("baseline", runner.load_agent("baseline"), cases, gates={"unit": 0.9}, gate_mode="strict")
        self.v2 = runner.run_agent("v2", runner.load_agent("v2"), cases, gates={"unit": 0.9}, gate_mode="strict")

    def tearDown(self):
        judge.configure("none")

    def test_judge_coverage(self):
        cov = markdown.judge_coverage(self.v2)
        # explain-001/002 (rubric, requirement) + research-001 (rubric scorer) + complex-005 (requirement)
        self.assertEqual(cov["judged"], 4)
        self.assertEqual(cov["skipped"], 2)  # the draft case and the unsupported one
        # research-002 hits a deterministic F gate before any judge call, so it counts as deterministic
        r2 = next(c for c in self.v2["cases"] if c["id"] == "research-002")
        self.assertFalse(r2["passed"])
        self.assertNotIn("judge", r2["checks"][0]["extra"])
        self.assertEqual(sum(cov.values()), self.v2["n_cases"])
        judge.configure("none")
        cov = markdown.judge_coverage(runner.run_agent("v2", runner.load_agent("v2"), load_cases(GOLDEN)))
        self.assertEqual(cov["judged"], 0)

    def test_run_markdown_sections(self):
        md = markdown.render_run_md(self.base)
        for section in ("## Tier x tool", "## Targets", "## Judge coverage", "## Skipped", "## Failing cases"):
            self.assertIn(section, md)
        self.assertIn("| **unit** | *(all)* |", md)
        self.assertIn("| unit | 90% | 59.1% | **NO** |", md)  # baseline misses the unit target
        self.assertIn("mode `strict`", md)
        self.assertIn("tier `complex` skipped", md)
        self.assertIn("gate unit:0.9 failed", md)

    def test_target_mode_markdown_reports_met_and_keeps_running(self):
        run = runner.run_agent("v2", runner.load_agent("v2"), load_cases(GOLDEN), gates={"unit": 0.9, "complex": 0.9})
        md = markdown.render_run_md(run)
        self.assertIn("mode `target`", md)
        self.assertIn("| unit | 90% | 90.9% | yes |", md)
        self.assertIn("| complex | 90% | 85.7% | **NO** |", md)
        self.assertIn("## Failure classes (judged failures)", md)
        self.assertNotIn("skipped:", md.split("## Judge coverage")[0].split("## Targets")[1])

    def test_compare_markdown_lists_regressions(self):
        cmp = compare_runs(self.base, self.v2)
        md = markdown.render_compare_md(cmp, self.base, self.v2)
        self.assertIn("## Regressions in v2 (1)", md)
        self.assertIn("| policy-001 | unit | policy |", md)
        self.assertIn("## A: baseline", md)
        self.assertIn("judged by LLM judge: 2", md)
        cmp["warnings"] = ["set differs"]
        self.assertIn("**WARNING:** set differs", markdown.render_compare_md(cmp))

    def test_md_table_escapes_pipes(self):
        self.assertIn("a \\| b", markdown.md_table(["h"], [["a | b"]]))

    def test_cli_writes_markdown_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = str(Path(tmp) / "runs")
            self.assertEqual(main(["run", "--agent", "baseline", "--runs-dir", runs, "--tool", "policy",
                                   "--md", str(Path(tmp) / "run.md"), "--judge", "none"]), 0)
            self.assertEqual(main(["run", "--agent", "v2", "--runs-dir", runs, "--tool", "policy", "--judge", "none"]), 0)
            self.assertEqual(main(["compare", "baseline", "v2", "--runs-dir", runs, "--md", str(Path(tmp) / "cmp.md")]), 0)
            self.assertEqual(main(["report", str(runner.latest_run("v2", runs)), "--md", str(Path(tmp) / "rep.md")]), 0)
            self.assertEqual(main(["matrix", "--agents", "baseline,v2", "--runs-dir", runs, "--reuse",
                                   "--md", str(Path(tmp) / "matrix.md")]), 0)
            for name in ("run.md", "cmp.md", "rep.md", "matrix.md"):
                self.assertTrue((Path(tmp) / name).read_text().startswith("# "), name)
            self.assertIn("policy-001", (Path(tmp) / "cmp.md").read_text())


if __name__ == "__main__":
    unittest.main()
