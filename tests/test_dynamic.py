import tempfile
import unittest
from pathlib import Path

from agents import resolvers
from harness import runner
from harness.cases import load_cases

GOLDEN = Path(__file__).resolve().parents[1] / "cases" / "golden"


class ResolverTests(unittest.TestCase):
    def test_earnings_date_moves_with_as_of(self):
        self.assertEqual(resolvers.earnings_date("2026-09-12", ticker="AAPL")["next_earnings"], "2026-10-29")
        r = resolvers.earnings_date("2026-11-01", ticker="AAPL")
        self.assertEqual((r["next_earnings"], r["previous_earnings"]), ("2027-01-28", "2026-10-29"))
        self.assertEqual(r["days_until"], 88)
        self.assertEqual(resolvers.earnings_date("2026-10-29", ticker="AAPL")["days_until"], 0)
        with self.assertRaises(ValueError):
            resolvers.earnings_date("2099-01-01", ticker="AAPL")
        with self.assertRaises(ValueError):
            resolvers.earnings_date("2026-01-01", ticker="ZZZZ")


class PlaceholderTests(unittest.TestCase):
    def test_substitute_only_known_names(self):
        vals = {"as_of": "2026-09-12", "x": 3}
        self.assertEqual(runner.substitute("on {as_of}: {x} {unknown} {2,5}", vals), "on 2026-09-12: 3 {unknown} {2,5}")
        self.assertEqual(runner.substitute({"a": ["{x}", 1], "b": {"c": "{as_of}"}}, vals), {"a": ["3", 1], "b": {"c": "2026-09-12"}})

    def test_materialise_dynamic_case(self):
        case = next(c for c in load_cases(GOLDEN) if c.id == "dyn-002")
        concrete, resolved = runner.materialise(case, "2026-09-12")
        self.assertEqual(concrete.prompt, "How many days from 2026-09-12 until NVDA's next earnings report?")
        self.assertEqual(concrete.checks[0]["expected"], "67")
        self.assertEqual(concrete.checks[1]["expected"], "2026-11-18")
        self.assertEqual(concrete.context["as_of"], "2026-09-12")
        self.assertEqual(resolved["days_until"], 67)
        self.assertEqual(case.checks[0]["expected"], "{days_until}")  # original untouched

    def test_materialise_static_case_is_unchanged(self):
        case = next(c for c in load_cases(GOLDEN) if c.id == "ticker-001")
        concrete, resolved = runner.materialise(case, "2026-09-12")
        self.assertEqual((concrete.prompt, concrete.context, resolved), (case.prompt, {}, {}))

    def test_load_resolver_errors(self):
        with self.assertRaises(ValueError):
            runner.load_resolver("agents.resolvers")
        with self.assertRaises(AttributeError):
            runner.load_resolver("agents.resolvers:nope")


class DynamicRunTests(unittest.TestCase):
    def test_v2_tracks_the_date_and_baseline_goes_stale(self):
        cases = load_cases(GOLDEN, tiers=["dynamic"])
        v2, base = runner.load_agent("v2"), runner.load_agent("baseline")
        early = runner.run_agent("v2", v2, cases, as_of="2026-09-12")
        self.assertEqual(early["as_of"], "2026-09-12")
        self.assertTrue(all(r["passed"] for r in early["cases"]), [r["checks"] for r in early["cases"]])
        self.assertEqual(early["cases"][0]["resolved"]["next_earnings"], "2026-10-29")
        late = runner.run_agent("v2", v2, cases, as_of="2027-01-05")
        self.assertTrue(all(r["passed"] for r in late["cases"]))
        self.assertIn("2027-01-28", late["cases"][0]["answer"])
        stale = runner.run_agent("baseline", base, cases, as_of="2027-01-05")
        self.assertFalse(stale["cases"][0]["passed"])
        self.assertEqual(stale["summary"]["per_tier"]["dynamic"]["pass_rate"], 0.0)

    def test_resolver_failure_is_a_failed_case_not_a_crash(self):
        cases = load_cases(GOLDEN, tiers=["dynamic"])
        run = runner.run_agent("v2", runner.load_agent("v2"), cases, as_of="2099-01-01")
        self.assertTrue(all(r["passed"] is False and "calendar" in (r["error"] or "") for r in run["cases"]))

    def test_run_records_as_of_and_defaults_to_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _ = runner.run("v2", GOLDEN, tmp, tiers=["dynamic"], as_of="2026-10-01")
            self.assertEqual(run["as_of"], "2026-10-01")
            run, _ = runner.run("v2", GOLDEN, tmp, tiers=["dynamic"])
            self.assertEqual(run["as_of"], runner.today_utc())


if __name__ == "__main__":
    unittest.main()
