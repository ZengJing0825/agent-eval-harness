import json
import tempfile
import unittest
from pathlib import Path

from harness import importer, lint, runner
from harness.cases import load_cases, set_provenance
from harness.cli import main

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "external_sample.csv"
GOLDEN = ROOT / "cases" / "golden"


class ImportTests(unittest.TestCase):
    def test_parse_map(self):
        self.assertEqual(importer.parse_map("prompt=question,expected=answer,tool=category"),
                         {"prompt": "question", "expected": "answer", "tool": "category"})
        self.assertEqual(importer.parse_map(""), {"prompt": "prompt", "expected": "expected"})
        for bad in ("prompt", "bogus=x"):
            with self.assertRaises(ValueError):
                importer.parse_map(bad)

    def test_read_rows_csv_and_jsonl(self):
        rows = importer.read_rows(csv_path=str(SAMPLE))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["answer"], "TSLA")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "b.jsonl"
            p.write_text('{"q": "a?", "a": "1"}\n\n{"q": "b?", "a": "2"}\n')
            self.assertEqual(len(importer.read_rows(jsonl_path=str(p))), 2)
            p.write_text('{"q": "a?"\n')
            with self.assertRaises(ValueError):
                importer.read_rows(jsonl_path=str(p))
        with self.assertRaises(ValueError):
            importer.read_rows()

    def test_build_document_shape_and_errors(self):
        rows = importer.read_rows(csv_path=str(SAMPLE))
        mapping = importer.parse_map("prompt=question,expected=answer,tool=category,note=notes")
        doc = importer.build_document(rows, mapping, "Sample Bench", "CC0")
        self.assertEqual((doc["tier"], doc["source"], doc["license"], doc["version"]), ("external", "Sample Bench", "CC0", 1))
        self.assertEqual(doc["cases"][0]["id"], "ext-sample-bench-001")
        self.assertEqual(doc["cases"][0]["tool"], "ticker_resolution")
        self.assertEqual(doc["cases"][0]["answer"], {"owner": "TSLA", "peer": None, "calculation": None,
                                                     "source": "Sample Bench", "status": "agreed"})
        num = importer.build_document(rows[2:3], mapping, "s", "l", scorer="numeric")["cases"][0]
        self.assertEqual((num["expected"], num["tolerance"]), (50.0, 0.01))
        with self.assertRaises(ValueError):
            importer.build_document(rows, importer.parse_map("prompt=missing_column"), "s", "l")
        with self.assertRaises(ValueError):
            importer.build_document(rows, mapping, "s", "l", tier="nope")
        with self.assertRaises(ValueError):
            importer.build_document(rows, mapping, "s", "l", scorer="llm_judge")
        with self.assertRaises(ValueError):
            importer.build_document([], mapping, "s", "l")

    def test_written_file_loads_with_provenance_and_lints_clean(self):
        rows = importer.read_rows(csv_path=str(SAMPLE))
        doc = importer.build_document(rows, importer.parse_map("prompt=question,expected=answer,tool=category"), "sb", "CC0")
        with tempfile.TemporaryDirectory() as tmp:
            importer.write_document(doc, Path(tmp) / "external_sb.yaml", origin="x.csv")
            cases = load_cases(tmp)
            self.assertEqual({c.tier for c in cases}, {"external"})
            self.assertEqual(cases[0].provenance, {"source": "sb", "license": "CC0"})
            self.assertEqual(set_provenance(tmp), {"external_sb.yaml": {"tier": "external", "source": "sb", "license": "CC0"}})
            issues, _, _ = lint.lint(tmp)
            self.assertEqual(issues, [])  # sourced external cases are exempt from the missing-peer warning

    def test_bundled_external_file_is_reproducible_and_reported_separately(self):
        self.assertIn("external_sample-bench.yaml", set_provenance(GOLDEN))
        with tempfile.TemporaryDirectory() as tmp:
            run, _ = runner.run("v2", GOLDEN, tmp, tiers=["external"])
            self.assertEqual(list(run["summary"]["per_tier"]), ["external"])
            self.assertEqual(run["summary"]["per_tier"]["external"]["n"], 4)
            self.assertEqual(run["set_provenance"]["external_sample-bench.yaml"]["source"], "sample-bench")
            self.assertEqual(main(["import", "--csv", str(SAMPLE), "--map", "prompt=question,expected=answer,tool=category",
                                   "--source", "cli-bench", "--license", "CC0", "--out", str(Path(tmp) / "e.yaml")]), 0)
            self.assertEqual(load_cases(tmp, tiers=["external"])[0].id, "ext-cli-bench-001")


if __name__ == "__main__":
    unittest.main()
