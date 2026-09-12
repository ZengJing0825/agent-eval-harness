import csv
import json
import tempfile
import unittest
from pathlib import Path

from harness import audit, judge, runner
from harness.cases import load_cases

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"


def _judged_run(n_pass=3, n_fail=2, version="requirement.v1", tier="unit", pass_scores=None):
    cases = []
    for i in range(n_pass + n_fail):
        ok = i < n_pass
        score = (pass_scores[i] if (ok and pass_scores) else 1.0) if ok else 0.0
        cases.append({"id": f"c{i}", "tool": "t", "tier": tier, "prompt": "q", "answer": "a",
                      "score": score, "passed": ok,
                      "checks": [{"type": "requirement", "score": score, "passed": ok, "detail": "",
                                  "extra": {"judge": version, "backend": "fake", "reason": f"r{i}"}}]})
    cases.append({"id": "det", "tool": "t", "tier": "unit", "prompt": "q", "answer": "a", "score": 0.0, "passed": False,
                  "checks": [{"type": "exact", "score": 0.0, "passed": False, "detail": ""}]})
    cases.append({"id": "skipped", "tool": "t", "tier": "unit", "prompt": "q", "answer": "", "score": None, "passed": None,
                  "checks": [{"type": "llm_judge", "score": None, "passed": None, "detail": "judge skipped"}]})
    return {"agent": "x", "timestamp": "t", "n_cases": len(cases), "summary": runner.summarise(cases), "cases": cases}


class AuditSheetTests(unittest.TestCase):
    def tearDown(self):
        judge.configure("none")

    def test_judged_checks_excludes_deterministic_and_skipped(self):
        rows = audit.judged_checks(_judged_run())
        self.assertEqual(sorted(r["case_id"] for r in rows), ["c0", "c1", "c2", "c3", "c4"])
        self.assertEqual(rows[0]["judge_reason"], "r0")

    def test_sheet_takes_all_failures_and_samples_passes(self):
        run = _judged_run(n_pass=10, n_fail=3)
        rows = audit.build_sheet(run, "r.json", "random:4", seed=1)
        self.assertEqual(sum(1 for r in rows if not r["judge_passed"]), 3)
        self.assertEqual(sum(1 for r in rows if r["judge_passed"]), 4)
        self.assertEqual(rows, audit.build_sheet(run, "r.json", "random:4", seed=1))  # deterministic
        self.assertEqual(len(audit.build_sheet(run, "r.json", "all")), 13)
        self.assertEqual(len(audit.build_sheet(run, "r.json", "random:0")), 3)
        for bad in ("random:x", "some", "random:-1", "pass:-2", "all-fails,bogus"):
            with self.assertRaises(ValueError):
                audit.parse_sample(bad)

    def test_parse_sample_rules(self):
        self.assertEqual(audit.DEFAULT_SAMPLE, "all-fails,low-first,pass:10")
        self.assertEqual(audit.parse_sample(None), {"fails": True, "low_first": True, "passes": 10})
        self.assertEqual(audit.parse_sample("all"), {"fails": True, "low_first": True, "passes": None})
        self.assertEqual(audit.parse_sample("random:4"), {"fails": True, "low_first": False, "passes": 4})
        self.assertEqual(audit.parse_sample("pass:all"), {"fails": False, "low_first": False, "passes": None})
        self.assertEqual(audit.parse_sample("all-fails, pass:3"), {"fails": True, "low_first": False, "passes": 3})

    def test_default_rule_fails_then_low_then_sampled_passes(self):
        # 3 failures (scores 0), 12 passes: two of them scored below 0.5 (rubric-style partial passes)
        run = _judged_run(n_pass=12, n_fail=3, pass_scores=[0.4, 1.0, 0.3] + [1.0] * 9)
        rows = audit.build_sheet(run, "r.json", seed=2)
        self.assertEqual([r["bucket"] for r in rows], ["fail"] * 3 + ["low"] * 2 + ["pass"] * 10)
        self.assertEqual([r["case_id"] for r in rows[3:5]], ["c2", "c0"])  # lowest score first
        self.assertEqual(len(rows), 15)
        self.assertNotIn("c2", [r["case_id"] for r in rows[5:]])  # low rows are not re-sampled as passes
        without_low = audit.build_sheet(run, "r.json", "all-fails,pass:10", seed=2)
        self.assertEqual({r["bucket"] for r in without_low}, {"fail", "pass"})
        self.assertEqual(len(without_low), 13)

    def test_write_and_read_csv_and_json(self):
        rows = audit.build_sheet(_judged_run(), "r.json", "all")
        with tempfile.TemporaryDirectory() as tmp:
            p = audit.write_sheet(rows, Path(tmp) / "sheet.csv")
            with open(p, newline="") as fh:
                back = list(csv.DictReader(fh))
            self.assertEqual(list(back[0]), audit.COLUMNS)
            for col in ("human_passed", "human_score", "reviewer", "human_note", "bucket"):
                self.assertIn(col, audit.COLUMNS)
            self.assertEqual((back[0]["human_passed"], back[0]["human_score"], back[0]["reviewer"]), ("", "", ""))
            self.assertEqual(len(audit.read_sheet(p)), 5)
            pj = audit.write_sheet(rows, Path(tmp) / "sheet.json")
            self.assertEqual(json.loads(pj.read_text())[0]["case_id"], rows[0]["case_id"])

    def test_agreement_overall_and_per_judge(self):
        rows = audit.build_sheet(_judged_run(n_pass=2, n_fail=2), "r.json", "all")
        rows += audit.build_sheet(_judged_run(n_pass=1, n_fail=1, version="requirement.v2"), "r2.json", "all")
        # human agrees with everything except: one judge pass that was wrong, one judge fail that was wrong (v1)
        labels = {("c0", "requirement.v1"): "no", ("c2", "requirement.v1"): "yes"}
        for r in rows:
            r["human_passed"] = labels.get((r["case_id"], r["judge"]), "yes" if r["judge_passed"] else "no")
        rows[-1]["human_passed"] = ""  # one unlabelled row is ignored
        res = audit.agreement(rows)
        self.assertEqual(res["labelled"], 5)
        self.assertEqual(res["unlabelled"], 1)
        self.assertEqual(res["overall"]["n"], 5)
        self.assertEqual(res["overall"]["agree"], 3)
        self.assertEqual((res["overall"]["overturned_to_fail"], res["overall"]["overturned_to_pass"]), (1, 1))
        self.assertAlmostEqual(res["per_judge"]["requirement.v1"]["agreement"], 0.5)
        self.assertAlmostEqual(res["per_judge"]["requirement.v2"]["agreement"], 1.0)
        self.assertEqual({d["case_id"] for d in res["disagreements"]}, {"c0", "c2"})
        self.assertIn("requirement.v2", audit.render_agreement(res))

    def test_apply_stores_audit_under_audits_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = audit.build_sheet(_judged_run(), "r.json", "all")
            for r in rows:
                r["human_passed"] = "yes"
            sheet = audit.write_sheet(rows, Path(tmp) / "labels.csv")
            result, path = audit.apply(sheet, Path(tmp) / "audits")
            self.assertTrue(path.name.startswith("audit-") and path.exists())
            self.assertEqual(result["overall"]["n"], 5)
            self.assertEqual(result["overall"]["overturned_to_pass"], 2)

    def test_agreement_per_tier_human_score_and_reviewer(self):
        rows = audit.build_sheet(_judged_run(n_pass=2, n_fail=2, tier="unit"), "r.json", "all")
        rows += audit.build_sheet(_judged_run(n_pass=2, n_fail=0, tier="complex"), "r2.json", "all")
        for r in rows:
            r["reviewer"] = "reviewer-a"
            r["human_score"] = "1" if r["judge_passed"] else "0"  # score only, no yes/no
        rows[0]["human_score"] = "1"  # a judged failure the human overturned to pass (unit)
        rows[-1]["human_score"] = "0.2"  # a judged pass the human overturned to fail (complex)
        rows[-2]["human_passed"] = "no"  # explicit yes/no wins over the score
        res = audit.agreement(rows)
        self.assertEqual(res["labelled"], 6)
        self.assertEqual(res["overall"]["overturned_to_pass"], 1)
        self.assertEqual(res["overall"]["overturned_to_fail"], 2)
        self.assertEqual(list(res["per_tier"]), ["unit", "complex"])
        self.assertAlmostEqual(res["per_tier"]["unit"]["agreement"], 0.75)
        self.assertAlmostEqual(res["per_tier"]["complex"]["agreement"], 0.0)
        self.assertEqual(res["reviewers"], ["reviewer-a"])
        self.assertTrue(all(d["reviewer"] == "reviewer-a" for d in res["disagreements"]))
        text = audit.render_agreement(res)
        for needle in ("Per judge version:", "Per tier:", "overturned to pass", "complex", "reviewer-a"):
            self.assertIn(needle, text)

    def test_end_to_end_with_fake_judge(self):
        judge.configure("fake")
        run = runner.run_agent("v2", runner.load_agent("v2"), load_cases(GOLDEN, tools=["explanation"]))
        rows = audit.build_sheet(run, "x", "all")
        self.assertTrue(rows)
        self.assertTrue(all(r["backend"] == "fake" and r["judge_reason"] for r in rows))
        self.assertEqual({r["judge"] for r in rows}, {"requirement.v2", "rubric.v2"})
        self.assertEqual(run["judge_version"], "fake:dimension.v2,requirement.v2,rubric.v2")

    def test_no_judge_means_empty_sheet_but_working_plumbing(self):
        judge.configure("none")
        run = runner.run_agent("v2", runner.load_agent("v2"), load_cases(GOLDEN, tools=["explanation"]))
        self.assertEqual(audit.build_sheet(run, "x", "all"), [])
        with tempfile.TemporaryDirectory() as tmp:
            p = audit.write_sheet([], Path(tmp) / "empty.csv")
            self.assertEqual(audit.read_sheet(p), [])
            self.assertEqual(audit.agreement([])["overall"]["agreement"], None)


if __name__ == "__main__":
    unittest.main()
