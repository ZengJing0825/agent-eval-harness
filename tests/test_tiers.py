import tempfile
import unittest
from pathlib import Path

from harness.cases import TIERS, load_cases
from harness import runner
from harness.runner import parse_gates, run_agent, summarise

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"

YAML = """
version: 1
tool: t
tier: complex
cases:
  - {id: a, prompt: "a", scorer: exact, expected: ok}
  - {id: b, prompt: "b", scorer: exact, expected: ok, tier: unit}
  - {id: c, prompt: "c", scorer: exact, expected: ok, tier: unit}
  - {id: d, prompt: "d", scorer: exact, expected: ok, tier: dynamic}
"""


def _write(tmp: str, text: str = YAML, name: str = "t.yaml") -> Path:
    p = Path(tmp) / name
    p.write_text(text)
    return Path(tmp)


class TierTests(unittest.TestCase):
    def test_file_default_and_per_case_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = {c.id: c.tier for c in load_cases(_write(tmp))}
        self.assertEqual(cases, {"a": "complex", "b": "unit", "c": "unit", "d": "dynamic"})

    def test_default_tier_is_unit_and_unknown_tier_rejected(self):
        self.assertTrue(all(c.tier == "unit" for c in load_cases(GOLDEN, tools=["policy"])))
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "version: 1\ntool: t\ntier: nope\ncases: []\n")
            with self.assertRaises(ValueError):
                load_cases(tmp)

    def test_tier_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = sorted(c.id for c in load_cases(_write(tmp), tiers=["unit", "dynamic"]))
        self.assertEqual(ids, ["b", "c", "d"])
        self.assertIn("complex", {c.tier for c in load_cases(GOLDEN)})

    def test_parse_gates(self):
        self.assertEqual(parse_gates(["unit:0.9", "complex:0.5,external:1"]), {"unit": 0.9, "complex": 0.5, "external": 1.0})
        self.assertEqual(parse_gates(None), {})
        for bad in ("unit", "nope:0.5", "unit:2", "unit:x"):
            with self.assertRaises(ValueError):
                parse_gates([bad])

    def test_strict_gate_stops_later_tiers_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = load_cases(_write(tmp))
        agent = lambda prompt, ctx: {"answer": "ok" if prompt == "b" else "no", "citations": []}  # noqa: E731
        run = run_agent("x", agent, cases, gates={"unit": 0.9}, gate_mode="strict")
        by_id = {r["id"]: r for r in run["cases"]}
        self.assertEqual([r["tier"] for r in run["cases"]], ["unit", "unit", "complex", "dynamic"])  # tier order
        self.assertEqual(run["gate_mode"], "strict")
        self.assertEqual(run["gates"], [{"tier": "unit", "threshold": 0.9, "pass_rate": 0.5, "passed": False,
                                         "met": False, "n_scored": 2, "mode": "strict"}])
        self.assertEqual(set(run["skipped_tiers"]), {"complex", "dynamic"})
        self.assertIsNone(by_id["a"]["score"])
        self.assertIn("gate unit:0.9 failed", by_id["a"]["skip_reason"])
        self.assertEqual(run["summary"]["per_tier"]["complex"]["skipped"], 1)
        self.assertEqual(sum(run["summary"]["skip_reasons"].values()), 2)

    def test_default_target_mode_records_result_and_runs_every_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = load_cases(_write(tmp))
        agent = lambda prompt, ctx: {"answer": "ok" if prompt == "b" else "no", "citations": []}  # noqa: E731
        run = run_agent("x", agent, cases, gates={"unit": 0.9, "complex": 0.5})
        self.assertEqual(run["gate_mode"], "target")
        self.assertEqual([(g["tier"], g["met"], g["mode"]) for g in run["gates"]],
                         [("unit", False, "target"), ("complex", False, "target")])
        self.assertEqual(run["skipped_tiers"], {})
        self.assertTrue(all(r["score"] is not None for r in run["cases"]))  # nothing skipped
        self.assertEqual(run["summary"]["skip_reasons"], {})
        with self.assertRaises(ValueError):
            run_agent("x", agent, cases, gates={"unit": 0.9}, gate_mode="loose")
        from harness import report
        text = report.render_run(run)
        self.assertIn("Targets:", text)
        self.assertIn("mode=target", text)
        self.assertRegex(text, r"unit\s+90%\s+50\.0%\s+NO")

    def test_harness_yaml_targets_merge_with_cli_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "golden").mkdir()
            golden = _write(str(Path(tmp) / "golden"))
            cfg = Path(tmp) / "harness.yaml"
            cfg.write_text("targets: {unit: 0.8, complex: 0.8}\ngate_mode: strict\n")
            self.assertEqual(runner.config_targets(runner.load_config(cfg)), {"unit": 0.8, "complex": 0.8})
            self.assertEqual(runner.load_config(Path(tmp) / "missing.yaml"), {})
            self.assertEqual(runner.load_config(None), {})
            agent_file = Path(tmp) / "ok_agent.py"
            agent_file.write_text("def answer(prompt, context):\n    return {'answer': 'ok' if prompt == 'b' else 'no'}\n")
            run, _ = runner.run(str(agent_file), golden, Path(tmp) / "runs", gates={"unit": 0.6}, config=cfg)
            self.assertEqual(run["selection"]["gates"], {"unit": 0.6, "complex": 0.8})  # CLI overrides per tier
            self.assertEqual(run["selection"]["targets_from_config"], {"unit": 0.8, "complex": 0.8})
            self.assertEqual(run["gate_mode"], "strict")  # from the file
            self.assertEqual([g["met"] for g in run["gates"]], [False])
            self.assertIn("complex", run["skipped_tiers"])
            run, _ = runner.run(str(agent_file), golden, Path(tmp) / "runs", config=cfg, gate_mode="target")
            self.assertEqual(run["gate_mode"], "target")
            self.assertEqual(run["skipped_tiers"], {})
            cfg.write_text("targets: [unit]\n")
            with self.assertRaises(ValueError):
                runner.run(str(agent_file), golden, Path(tmp) / "runs", config=cfg)

    def test_repo_harness_yaml_is_valid(self):
        cfg = runner.load_config(Path(__file__).resolve().parents[1] / "harness.yaml")
        self.assertEqual(runner.config_targets(cfg), {"unit": 0.8, "complex": 0.8})
        self.assertEqual(runner.validate_gate_mode(cfg.get("gate_mode")), "target")

    def test_cli_gate_mode_flag(self):
        from harness.cli import main
        from harness.compare import load_run
        with tempfile.TemporaryDirectory() as tmp:
            runs = str(Path(tmp) / "runs")
            self.assertEqual(main(["run", "--agent", "baseline", "--runs-dir", runs, "--judge", "none",
                                   "--gate", "unit:0.9", "--gate-mode", "strict", "--config", str(Path(tmp) / "none.yaml")]), 0)
            run = load_run(runner.latest_run("baseline", runs))
            self.assertEqual(run["gate_mode"], "strict")
            self.assertIn("complex", run["skipped_tiers"])

    def test_gate_passes_and_all_tiers_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = load_cases(_write(tmp))
        run = run_agent("x", lambda p, c: {"answer": "ok"}, cases, gates={"unit": 0.9, "complex": 0.5})
        self.assertEqual([g["passed"] for g in run["gates"]], [True, True])
        self.assertEqual(run["skipped_tiers"], {})
        self.assertEqual(run["summary"]["per_tier_tool"]["dynamic"]["t"]["pass_rate"], 1.0)

    def test_summarise_per_tier_ordering(self):
        rows = [{"id": i, "tool": "t", "tier": tier, "score": 1.0, "passed": True}
                for i, tier in enumerate(reversed(TIERS))]
        self.assertEqual(list(summarise(rows)["per_tier"]), list(TIERS))


if __name__ == "__main__":
    unittest.main()
