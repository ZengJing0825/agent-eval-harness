# agent-eval-harness

A lightweight evaluation harness for LLM agents. Standard library + PyYAML, no network needed for the demo.

It packages four ideas that make agent evaluation useful in practice rather than a one-off notebook:

1. **Dynamic golden set** - cases live in versioned YAML files and grow over time.
2. **Tool-level objective tests** - every case is tagged with the tool it exercises and scored deterministically where possible.
3. **Peer comparison** - run two agent versions on the same cases and get a side-by-side with win/loss/tie.
4. **Bad-case feedback loop** - failing production samples are captured into a backlog and promoted into the golden set.

The bundled demo is a tiny finance assistant (ticker resolution, earnings-date lookup, percentage-change maths, a "no buy/sell advice" policy, citation presence) with two offline rule-based agents, `baseline` and `v2`. v2 is better on four tools and quietly worse on one - the kind of regression an eval exists to catch.

## Quickstart

```bash
pip install pyyaml                      # the only dependency
python -m harness run --agent baseline  # runs 20 cases, saves runs/baseline-<ts>.json
python -m harness run --agent v2
python -m harness compare baseline v2   # side-by-side + per-case win/loss/tie
```

The compare output ends with a summary such as:

```
tool               pass baseline  pass v2  avg baseline  avg v2  B win/loss/tie
policy             100.0%          75.0%   1.00          0.88    0/1/3
ticker_resolution   50.0%         100.0%   0.50          1.00    2/0/2
ALL                 68.4%          94.7%   0.71          0.97    6/1/12

Summary: v2 wins 6, loses 1, ties 12, skipped 1 -> B is better.
Regressions in v2: policy-001
```

That last line is the point: v2 looks like a clear upgrade on aggregate, but it leaks "strong buy" into a refusal and fails the policy check. Aggregate pass rate would hide that; a per-tool table does not.

Other commands:

```bash
python -m harness report runs/v2-<ts>.json -v          # re-print a saved run, every case
python -m harness run --agent v2 --tool policy         # only one tool
python -m harness badcase add --agent v2 --prompt "Should I buy NVDA right now?" \
    --expected "strong buy" --scorer policy --tool policy --note "leaks analyst rating"
python -m harness badcase list
python -m harness badcase promote bc-20260912-1dfe     # -> cases/golden/promoted.yaml
python -m unittest discover -s tests                   # 20 stdlib tests
```

## Why each idea matters

**Dynamic golden set.** A static benchmark decays: the agent overfits to it, the product moves on, and the numbers stop meaning anything. Keeping cases in small YAML files with a `version` field makes the set reviewable in pull requests, diffable, and growable. A run records which cases it saw, so two runs on different set versions are never silently compared as equals (unmatched cases are listed explicitly).

**Tool-level objective tests.** "Overall accuracy 87%" tells you nothing about what to fix. Tagging each case with the capability it exercises (`ticker_resolution`, `policy`, ...) turns a run into a per-tool table that maps straight onto engineering work. Deterministic scorers (exact, contains, regex, numeric tolerance, JSON path, forbidden phrases, citation presence) make a failure a fact, not an opinion, and are free to run on every commit. An LLM-judge scorer exists for genuinely open-ended answers, but it is opt-in and reports *skipped* rather than *failed* when no API key is present.

**Peer comparison.** Absolute scores drift with the case set; the question that actually gets asked is "is the new version better than the old one?". Comparing two runs on the same cases gives per-case win/loss/tie, per-tool deltas, and an explicit list of regressions. That list is what you paste into the release discussion.

**Bad-case feedback loop.** The most valuable eval cases are the ones users hit in production. `badcase add` captures one in a single command (prompt, expected, what the agent said, a note), `badcase promote` moves it into the golden set and bumps the set version. The loop closes: production failures become permanent regression tests.

## Plug in your own agent

An agent is a Python module with one function:

```python
def answer(prompt: str, context: dict) -> dict:
    ...
    return {"answer": "The ticker symbol is AAPL.", "citations": ["https://..."]}
```

- `prompt` is the case prompt; `context` is the case's optional `context:` mapping (anything you like - user profile, tool outputs, date).
- Return a dict with `answer` (string) and `citations` (list of strings). An optional `data` key may hold structured output for the `json_key` scorer.
- Exceptions are caught and recorded as failed cases with a traceback; the harness never crashes because an agent did.

Run it by name, module path, or file path:

```bash
python -m harness run --agent my_agent          # agents/my_agent.py
python -m harness run --agent mypkg.agents.prod # importable module
python -m harness run --agent ./scratch/agent.py
```

`agents/anthropic_agent.py` is an optional adapter that sends the same prompts to a Claude model (`claude-sonnet-5`). It needs `pip install anthropic` and `ANTHROPIC_API_KEY`; without them it (and the LLM judge) is skipped cleanly.

## Add cases

One YAML file per tool under `cases/golden/`:

```yaml
version: 3                 # bump when you change the set
tool: earnings_date
cases:
  - id: earn-005           # ids must be unique across all files
    prompt: "When does Amazon report?"
    scorer: contains       # short form: one check
    expected: "2026-10-29"
    tags: [company-name]
  - id: earn-006
    prompt: "Cite your source: when does AMZN report?"
    checks:                # long form: several checks, score = mean, pass = all
      - {type: contains, expected: "2026-10-29"}
      - {type: citation, min: 1}
```

Available scorers and their arguments:

| type | arguments | passes when |
|---|---|---|
| `exact` | `expected`, `case_sensitive` | whole answer equals expected (whitespace/case normalised) |
| `contains` | `expected` (str or list), `case_sensitive` | every expected substring is present |
| `regex` | `pattern`, `ignore_case` | `re.search` matches |
| `numeric` | `expected`, `tolerance`, `relative` | some number in the answer is within tolerance |
| `json_key` | `path`, `expected` | value at dotted path in `data` (or parsed JSON answer) equals expected |
| `policy` | `forbidden` (list) | none of the forbidden phrases appear |
| `citation` | `min` | at least `min` non-empty citations |
| `llm_judge` | `rubric`, `threshold` | judge score (0-10, scaled) >= threshold; skipped without key |

Add a scorer by writing a function `(answer: dict, check: dict) -> Score` in `harness/scorers.py` and registering it in `SCORERS`.

## CI hint

Deterministic scorers run in well under a second, so the harness fits in any CI job:

```yaml
# .github/workflows/eval.yml
- run: pip install pyyaml
- run: python -m unittest discover -s tests
- run: python -m harness run --agent v2
- run: python -m harness compare baseline v2 --out compare.json
# then fail the job if compare.json["tally"]["loss"] > 0, or gate on a per-tool pass rate
```

Keep a reference run for the shipped version under version control (e.g. `runs/baseline-release.json`) and compare every PR against it; the "Regressions in ..." line is your review checklist.

## Layout

```
harness/      cases.py (loader) scorers.py runner.py compare.py report.py badcase.py cli.py
agents/       baseline.py v2.py anthropic_agent.py fixtures/market.json
cases/golden/ one YAML file per tool     cases/backlog/  captured bad cases
runs/         saved runs (JSON)          tests/          stdlib unittest suite
```

## License

MIT - Jing Zeng.
