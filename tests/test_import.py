import json
import subprocess
import sys
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

    def test_dotted_paths_json_array_and_prompt_template(self):
        self.assertEqual(importer.get_path({"qa": {"answer": "94"}, "a.b": 1}, "qa.answer"), "94")
        self.assertEqual(importer.get_path({"a.b": 1}, "a.b"), 1)  # a literal dotted column wins
        self.assertIsNone(importer.get_path({"qa": "x"}, "qa.answer"))
        self.assertEqual(importer.render([["h1", "h2"], ["1", "2"]]), "h1 | h2\n1 | 2")
        self.assertEqual(importer.render(["a", "b"]), "a\nb")
        finqa = [{"id": "ETR/2016/page_23.pdf-1", "pre_text": ["net revenue utility"], "post_text": [],
                  "table": [["", "amount"], ["2014 net revenue", "$ 5735"], ["2015 net revenue", "$ 5829"]],
                  "qa": {"question": "what is the net change in net revenue during 2015?", "answer": "94", "exe_ans": 94.0}}]
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "test.json"
            src.write_text(json.dumps(finqa))
            rows = importer.read_rows(jsonl_path=str(src))
            self.assertEqual(rows[0]["qa"]["answer"], "94")
            src.write_text("[1, 2]")
            with self.assertRaises(ValueError):
                importer.read_rows(jsonl_path=str(src))
            doc = importer.build_document(finqa, importer.parse_map("expected=qa.answer,id=id"), "finqa", "MIT",
                                          prompt_template="{pre_text}\\n{table}\\n{post_text}\\n\\n{qa.question}")
            case = doc["cases"][0]
            self.assertEqual(case["id"], "ETR/2016/page_23.pdf-1")
            self.assertEqual(case["expected"], "94")
            self.assertEqual(case["prompt"], "net revenue utility\n | amount\n2014 net revenue | $ 5735\n2015 net revenue | $ 5829\n\n\n"
                                             "what is the net change in net revenue during 2015?")
            num = importer.build_document(finqa, importer.parse_map("expected=qa.exe_ans"), "f", "MIT", scorer="numeric",
                                          prompt_template="{qa.question}")["cases"][0]
            self.assertEqual(num["expected"], 94.0)
            src.write_text(json.dumps(finqa))
            out = Path(tmp) / "e.yaml"
            self.assertEqual(main(["import", "--jsonl", str(src), "--map", "expected=qa.answer", "--prompt-template",
                                   "{table}\\n{qa.question}", "--source", "finqa", "--license", "MIT", "--out", str(out)]), 0)
            self.assertTrue(load_cases(tmp, tiers=["external"])[0].prompt.startswith("| amount\n2014 net revenue"))

    def test_example_converters_flatten_nested_benchmarks(self):
        conv = [{"id": "c1", "pre_text": ["text"], "post_text": [], "table": [["a", "1"], ["b", "3"]],
                 "annotation": {"dialogue_break": ["what is a?", "and b minus a?"], "exe_ans_list": [1, 2]}}]
        tat = [{"table": {"uid": "t", "table": [["item", "2019"], ["revenue", "10"]]},
                "paragraphs": [{"uid": "p2", "order": 2, "text": "second"}, {"uid": "p1", "order": 1, "text": "first"}],
                "questions": [{"uid": "q1", "order": 1, "question": "revenue?", "answer": ["10"], "answer_type": "span",
                               "answer_from": "table", "scale": "million"},
                              {"uid": "q2", "order": 2, "question": "no gold yet"}]}]
        with tempfile.TemporaryDirectory() as tmp:
            for script, data, expect in (("convert_convfinqa.py", conv, 2), ("convert_tatqa.py", tat, 1)):
                src, dst = Path(tmp) / "in.json", Path(tmp) / (script + ".jsonl")
                src.write_text(json.dumps(data))
                subprocess.run([sys.executable, str(ROOT / "examples" / script), str(src), str(dst)], check=True)
                rows = importer.read_rows(jsonl_path=str(dst))
                self.assertEqual(len(rows), expect)
                doc = importer.build_document(rows, importer.parse_map("id=id,tool=tool,note=note"), script, "MIT")
                self.assertEqual(len(doc["cases"]), expect)
            conv_rows = importer.read_rows(jsonl_path=str(Path(tmp) / "convert_convfinqa.py.jsonl"))
            self.assertEqual((conv_rows[1]["id"], conv_rows[1]["expected"]), ("c1-t1", "2"))
            self.assertIn("Q: what is a?\nA: 1\nQ: and b minus a?", conv_rows[1]["prompt"])
            tat_rows = importer.read_rows(jsonl_path=str(Path(tmp) / "convert_tatqa.py.jsonl"))
            self.assertEqual((tat_rows[0]["id"], tat_rows[0]["expected"], tat_rows[0]["tool"]), ("q1", "10", "span"))
            self.assertTrue(tat_rows[0]["prompt"].startswith("first\nsecond\n\nitem | 2019\nrevenue | 10\n\nrevenue? (answer in million)"))

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
