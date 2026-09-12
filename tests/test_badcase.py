import tempfile
import unittest
from pathlib import Path

import yaml

from harness import badcase
from harness.cases import load_cases, set_version


class BadcaseTests(unittest.TestCase):
    def test_add_creates_backlog_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = badcase.add("v2", "Should I buy NVDA?", "advice", note="leaks rating", scorer="policy",
                               category="reasoning", backlog_dir=tmp)
            entry = yaml.safe_load(path.read_text())
            self.assertTrue(entry["id"].startswith("bc-"))
            self.assertEqual(entry["status"], "open")
            self.assertEqual([e["id"] for e in badcase.list_entries(tmp)], [entry["id"]])

    def test_promote_moves_entry_into_golden_and_bumps_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            backlog, golden = Path(tmp) / "backlog", Path(tmp) / "golden"
            p1 = badcase.add("v2", "When does Tesla report?", "2026-10-21", tool="earnings_date",
                             category="data", backlog_dir=backlog)
            p2 = badcase.add("v2", "Ticker for Google?", "GOOGL", category="tool_choice", backlog_dir=backlog)
            id1, id2 = p1.stem, p2.stem
            target = golden / "promoted.yaml"

            badcase.promote(id1, backlog, target)
            self.assertFalse(p1.exists())
            doc = yaml.safe_load(target.read_text())
            self.assertEqual(doc["version"], 1)
            badcase.promote(id2, backlog, target)
            doc = yaml.safe_load(target.read_text())
            self.assertEqual(doc["version"], 2)
            self.assertEqual([c["id"] for c in doc["cases"]], [id1, id2])

            # The promoted file is a valid golden file the loader understands.
            cases = {c.id: c for c in load_cases(golden)}
            self.assertEqual(cases[id1].tool, "earnings_date")
            self.assertEqual(cases[id1].checks, [{"type": "contains", "expected": "2026-10-21"}])
            self.assertEqual(cases[id1].status, "draft")  # no peer answer yet
            with self.assertRaises(FileNotFoundError):
                badcase.promote(id1, backlog, target)

    def test_category_is_required_and_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                badcase.add("v2", "p", "e", backlog_dir=tmp)
            with self.assertRaises(ValueError):
                badcase.add("v2", "p", "e", category="typo", backlog_dir=tmp)
            for c in badcase.CATEGORIES:
                badcase.add("v2", "p", "e", category=c, backlog_dir=tmp)
            groups = badcase.group_by_category(badcase.list_entries(tmp))
            self.assertEqual(list(groups), list(badcase.CATEGORIES))
            self.assertEqual({k: len(v) for k, v in groups.items()}, {c: 1 for c in badcase.CATEGORIES})

    def test_ambiguity_requires_rewrite_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            backlog, golden = Path(tmp) / "backlog", Path(tmp) / "golden"
            p = badcase.add("v2", "When does Meta report?", "2026-10-28", category="ambiguity", backlog_dir=backlog)
            with self.assertRaises(ValueError):
                badcase.promote(p.stem, backlog, golden / "promoted.yaml")
            self.assertTrue(p.exists())  # nothing was touched
            badcase.promote(p.stem, backlog, golden / "promoted.yaml",
                            rewrite="When does Meta Platforms (META) next report earnings?", peer="2026-10-28")
            case = yaml.safe_load((golden / "promoted.yaml").read_text())["cases"][0]
            self.assertEqual(case["prompt"], "When does Meta Platforms (META) next report earnings?")
            self.assertEqual(case["original_prompt"], "When does Meta report?")
            self.assertIn("category:ambiguity", case["tags"])
            self.assertEqual(case["answer"]["status"], "agreed")
            self.assertEqual(load_cases(golden)[0].status, "agreed")

    def test_promote_appends_changelog_with_set_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            backlog, golden = Path(tmp) / "backlog", Path(tmp) / "cases" / "golden"
            golden.mkdir(parents=True)
            (golden / "a.yaml").write_text("version: 1\ntool: t\ncases:\n  - {id: a, prompt: p, scorer: exact, expected: x}\n")
            before = set_version(golden)
            p = badcase.add("v2", "p", "e", category="data", note="a | note", backlog_dir=backlog)
            badcase.promote(p.stem, backlog, golden / "promoted.yaml")
            after = set_version(golden)
            log = (Path(tmp) / "cases" / "CHANGELOG.md").read_text()
            self.assertTrue(log.startswith("# Golden-set changelog"))
            line = log.strip().splitlines()[-1]
            self.assertIn(f"| {p.stem} | data | {before} -> {after} | a / note |", line)
            self.assertNotEqual(before, after)
            p2 = badcase.add("v2", "q", "e", category="judge", backlog_dir=backlog)
            badcase.promote(p2.stem, backlog, golden / "promoted.yaml", changelog=Path(tmp) / "custom.md")
            self.assertEqual(len((Path(tmp) / "cases" / "CHANGELOG.md").read_text().strip().splitlines()), 8)
            self.assertIn(p2.stem, (Path(tmp) / "custom.md").read_text())


    def test_to_golden_case_maps_scorer_arguments(self):
        base = {"id": "bc-1", "prompt": "p", "agent": "v2", "category": "data"}
        self.assertEqual(badcase.to_golden_case({**base, "scorer": "policy", "expected": "strong buy"})["forbidden"], ["strong buy"])
        self.assertEqual(badcase.to_golden_case({**base, "scorer": "regex", "expected": r"\\bX\\b"})["pattern"], r"\\bX\\b")
        num = badcase.to_golden_case({**base, "scorer": "numeric", "expected": "25"})
        self.assertEqual((num["expected"], num["tolerance"]), (25.0, 0.01))
        self.assertEqual(badcase.to_golden_case({**base, "scorer": "contains", "expected": "AAPL"})["expected"], "AAPL")


if __name__ == "__main__":
    unittest.main()
