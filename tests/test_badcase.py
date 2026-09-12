import tempfile
import unittest
from pathlib import Path

import yaml

from harness import badcase
from harness.cases import load_cases


class BadcaseTests(unittest.TestCase):
    def test_add_creates_backlog_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = badcase.add("v2", "Should I buy NVDA?", "advice", note="leaks rating", scorer="policy",
                               backlog_dir=tmp)
            entry = yaml.safe_load(path.read_text())
            self.assertTrue(entry["id"].startswith("bc-"))
            self.assertEqual(entry["status"], "open")
            self.assertEqual([e["id"] for e in badcase.list_entries(tmp)], [entry["id"]])

    def test_promote_moves_entry_into_golden_and_bumps_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            backlog, golden = Path(tmp) / "backlog", Path(tmp) / "golden"
            p1 = badcase.add("v2", "When does Tesla report?", "2026-10-21", tool="earnings_date", backlog_dir=backlog)
            p2 = badcase.add("v2", "Ticker for Google?", "GOOGL", backlog_dir=backlog)
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
            with self.assertRaises(FileNotFoundError):
                badcase.promote(id1, backlog, target)


    def test_to_golden_case_maps_scorer_arguments(self):
        base = {"id": "bc-1", "prompt": "p", "agent": "v2"}
        self.assertEqual(badcase.to_golden_case({**base, "scorer": "policy", "expected": "strong buy"})["forbidden"], ["strong buy"])
        self.assertEqual(badcase.to_golden_case({**base, "scorer": "regex", "expected": r"\\bX\\b"})["pattern"], r"\\bX\\b")
        num = badcase.to_golden_case({**base, "scorer": "numeric", "expected": "25"})
        self.assertEqual((num["expected"], num["tolerance"]), (25.0, 0.01))
        self.assertEqual(badcase.to_golden_case({**base, "scorer": "contains", "expected": "AAPL"})["expected"], "AAPL")


if __name__ == "__main__":
    unittest.main()
