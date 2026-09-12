import json
import tempfile
import unittest
from pathlib import Path

import subprocess

from harness import __version__, cases, matrix, report, runner
from harness.cases import set_files, set_label_summary, set_labels, set_version
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


def _run(agent, scores, set_version="s1", judge="none", labels=None, label=None):
    rows = [{"id": f"c{i}", "tool": "t", "tier": "unit", "prompt": "p", "answer": "", "score": s,
             "passed": None if s is None else s >= 1.0} for i, s in enumerate(scores)]
    return {"agent": agent, "agent_version": "x", "timestamp": "t", "set_version": set_version,
            "set_labels": labels if labels is not None else {"t.yaml": "2026-09-12"}, "label": label,
            "judge_version": judge, "n_cases": len(rows), "summary": runner.summarise(rows), "cases": rows}


class SetLabelTests(unittest.TestCase):
    def test_explicit_label_git_date_and_today_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            golden = Path(tmp) / "golden"
            golden.mkdir()
            (golden / "a.yaml").write_text("version: 1\nset_label: \"2026-08-01\"\ntool: t\ncases:\n"
                                           "  - {id: a, prompt: p, scorer: exact, expected: x}\n")
            (golden / "b.yaml").write_text(CASE.format(v=1, x="ok").replace("id: a", "id: b"))
            self.assertEqual(set_labels(golden), {"a.yaml": "2026-08-01", "b.yaml": cases._today()})  # no git: today
            self.assertIsNone(cases.file_git_date(golden / "b.yaml"))
            git = lambda *args: subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True,  # noqa: E731
                                               env={"GIT_AUTHOR_DATE": "2026-07-04T00:00:00Z", "GIT_COMMITTER_DATE": "2026-07-04T00:00:00Z",
                                                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                                                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                                                    "PATH": __import__("os").environ["PATH"], "HOME": tmp})
            git("init", "-q")
            git("add", ".")
            git("commit", "-q", "-m", "seed")
            cases.clear_git_date_cache()  # the file did not change, only its git state did
            self.assertEqual(cases.file_git_date(golden / "b.yaml"), "2026-07-04")
            self.assertEqual(set_labels(golden)["b.yaml"], "2026-07-04")  # committed: the commit date
            self.assertEqual(set_labels(golden)["a.yaml"], "2026-08-01")  # explicit label still wins
            (golden / "b.yaml").write_text(CASE.format(v=2, x="ok").replace("id: a", "id: b"))
            self.assertEqual(set_labels(golden)["b.yaml"], cases._today())  # modified since: today

    def test_label_summary(self):
        self.assertEqual(set_label_summary({"a": "2026-09-12", "b": "2026-09-12"}), "2026-09-12")
        self.assertEqual(set_label_summary({"a": "2026-09-01", "b": "2026-09-12", "c": "2026-09-05"}), "2026-09-01..2026-09-12")
        self.assertEqual(set_label_summary({}), "?")
        self.assertEqual(set_label_summary(None), "?")

    def test_run_stores_set_labels_and_free_text_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, path = runner.run("v2", GOLDEN, tmp, ["policy"], label="policy set 2026-09-12", config=None)
            self.assertEqual(set(run["set_labels"]), set(set_files(GOLDEN)))
            self.assertTrue(all(len(v) == 10 for v in run["set_labels"].values()))
            self.assertEqual(run["label"], "policy set 2026-09-12")
            saved = json.loads(path.read_text())
            self.assertEqual((saved["label"], saved["set_labels"]), (run["label"], run["set_labels"]))
            text = report.render_run(run)
            self.assertIn("label='policy set 2026-09-12'", text)
            self.assertIn(f"set={run['set_version']} ({set_label_summary(run['set_labels'])})", text)
            run, _ = runner.run("v2", GOLDEN, tmp, ["policy"], config=None)
            self.assertIsNone(run["label"])

    def test_compare_and_matrix_print_labels_next_to_the_hash(self):
        a = _run("a", [1.0], labels={"t.yaml": "2026-09-01"}, label="before")
        b = _run("b", [1.0], set_version="s2", labels={"t.yaml": "2026-09-12"}, label="after")
        cmp = compare_runs(a, b)
        self.assertEqual((cmp["set_label_a"], cmp["set_label_b"]), ("2026-09-01", "2026-09-12"))
        text = report.render_compare(cmp)
        self.assertIn("set s1 (2026-09-01) vs s2 (2026-09-12)", text)
        self.assertIn("labels 'before' vs 'after'", text)
        from harness import markdown
        self.assertIn("`s1` (`2026-09-01`) vs `s2` (`2026-09-12`)", markdown.render_compare_md(cmp))
        m = matrix.build_matrix([a, b])
        self.assertEqual(m["set_labels"], ["2026-09-01", "2026-09-12"])
        self.assertEqual([x["set_label"] for x in m["agents"]], ["2026-09-01", "2026-09-12"])
        self.assertIn("set=s1,s2 (2026-09-01,2026-09-12)", matrix.render_text(m))
        self.assertIn("label(s): `2026-09-01, 2026-09-12`", matrix.render_markdown(m))


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
