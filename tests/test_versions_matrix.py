import json
import tempfile
import unittest
from pathlib import Path

from harness import __version__, matrix, runner
from harness.cases import set_files, set_version
from harness.compare import compare_runs, regressions

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"
CASE = "version: {v}\ntool: t\ncases:\n  - {{id: a, prompt: p, scorer: exact, expected: {x}}}\n"


class VersionTests(unittest.TestCase):
    def test_set_version_changes_with_content_or_declared_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.yaml"
            f.write_text(CASE.format(v=1, x="ok"))
            v1 = set_version(tmp)
            self.assertEqual(len(v1), 12)
            self.assertEqual(v1, set_version(tmp))  # deterministic
            f.write_text(CASE.format(v=1, x="changed"))
            self.assertNotEqual(v1, set_version(tmp))
            f.write_text(CASE.format(v=2, x="ok"))
            self.assertNotEqual(v1, set_version(tmp))
            self.assertEqual(set_files(tmp), {"t.yaml": 2})

    def test_agent_version_attribute_or_unversioned(self):
        self.assertEqual(runner.agent_version("baseline"), "1.0")
        self.assertEqual(runner.agent_version("v2"), "2.0")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "anon.py"
            p.write_text("def answer(prompt, context):\n    return {'answer': prompt}\n")
            self.assertEqual(runner.agent_version(str(p)), "unversioned")

    def test_run_records_all_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, path = runner.run("v2", GOLDEN, tmp, ["policy"])
            self.assertEqual(run["agent_version"], "2.0")
            self.assertEqual(run["set_version"], set_version(GOLDEN))
            self.assertEqual(run["harness_version"], __version__)
            self.assertIn("judge_version", run)
            self.assertEqual(run["selection"]["tools"], ["policy"])
            self.assertEqual(json.loads(path.read_text())["set_version"], run["set_version"])


def _run(agent, scores, set_version="s1", judge="none"):
    rows = [{"id": f"c{i}", "tool": "t", "tier": "unit", "prompt": "p", "answer": "", "score": s,
             "passed": None if s is None else s >= 1.0} for i, s in enumerate(scores)]
    return {"agent": agent, "agent_version": "x", "timestamp": "t", "set_version": set_version,
            "judge_version": judge, "n_cases": len(rows), "summary": runner.summarise(rows), "cases": rows}


class CompareWarningTests(unittest.TestCase):
    def test_warns_when_set_version_differs(self):
        cmp = compare_runs(_run("a", [1.0]), _run("b", [1.0], set_version="s2"))
        self.assertTrue(any("golden-set versions differ" in w for w in cmp["warnings"]))
        self.assertEqual(compare_runs(_run("a", [1.0]), _run("b", [1.0]))["warnings"], [])

    def test_warns_on_judge_version_and_unmatched(self):
        cmp = compare_runs(_run("a", [1.0]), _run("b", [1.0], judge="fake:rubric.v1"))
        self.assertTrue(any("judge versions differ" in w for w in cmp["warnings"]))
        cmp = compare_runs(_run("a", [1.0, 1.0]), _run("b", [1.0]))
        self.assertEqual(cmp["unmatched"]["only_in_a"], ["c1"])
        self.assertTrue(any("different cases" in w for w in cmp["warnings"]))

    def test_regressions_helper(self):
        cmp = compare_runs(_run("a", [1.0, 0.0]), _run("b", [0.0, 1.0]))
        self.assertEqual(regressions(cmp), ["c0"])


class MatrixTests(unittest.TestCase):
    def test_build_matrix_regressions_vs_first_agent(self):
        m = matrix.build_matrix([_run("a", [1.0, 0.0, 1.0]), _run("b", [0.0, 1.0, 1.0]), _run("c", [1.0, 1.0, 1.0])])
        self.assertEqual(m["reference"], "a")
        self.assertEqual([a["regressions"] for a in m["agents"]], [[], ["c0"], []])
        self.assertEqual(m["agents"][2]["improvements"], ["c1"])
        self.assertEqual(m["tiers"], ["unit"])
        self.assertEqual(m["warnings"], [])
        m = matrix.build_matrix([_run("a", [1.0]), _run("b", [1.0], set_version="other")])
        self.assertTrue(m["warnings"])
        with self.assertRaises(ValueError):
            matrix.build_matrix([])

    def test_render_text_and_markdown(self):
        m = matrix.build_matrix([_run("a", [1.0, 0.0]), _run("b", [1.0, 1.0])])
        text = matrix.render_text(m, verbose=True)
        self.assertIn("b vs a: wins 1, losses 0", text)
        md = matrix.render_markdown(m, verbose=True)
        self.assertIn("| a | x | 50% / 0.50 | 50% / 0.50 |", md)
        self.assertIn("## Per tool", md)
        with tempfile.TemporaryDirectory() as tmp:
            p = matrix.write(m, str(Path(tmp) / "m.json"))
            self.assertEqual(json.loads(p.read_text())["reference"], "a")
            p = matrix.write(m, str(Path(tmp) / "m.md"))
            self.assertTrue(p.read_text().startswith("# Experiment matrix"))

    def test_collect_runs_reuses_saved_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = matrix.collect_runs(["baseline", "v2"], GOLDEN, tmp, ["policy"])
            self.assertEqual([r["agent"] for r in first], ["baseline", "v2"])
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 2)
            again = matrix.collect_runs(["baseline", "v2"], GOLDEN, tmp, ["policy"], reuse=True)
            self.assertEqual(len(list(Path(tmp).glob("*.json"))), 2)  # nothing re-run
            self.assertEqual([r["timestamp"] for r in again], [r["timestamp"] for r in first])
            m = matrix.build_matrix(again)
            self.assertEqual(m["agents"][1]["regressions"], ["policy-001"])


if __name__ == "__main__":
    unittest.main()
