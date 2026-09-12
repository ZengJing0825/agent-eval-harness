# agent-eval-harness

A small evaluation harness for LLM agents. Standard library + PyYAML; the bundled demo runs offline in about a second.

Version 0.2 turns the harness into an evaluation *method*, not just a runner. Five rules, each backed by a command:

1. **Objective before open-ended.** Cases sit in tiers (`unit` -> `complex` -> `external` -> `dynamic`) and a gate on an earlier tier stops the later ones. Nobody reads judge scores for an agent that fails arithmetic.
2. **Two people write every answer.** Each case records an `owner` answer and an independent `peer` answer; `harness lint` refuses to let a disagreement ship as "agreed".
3. **Judges must be calibrated.** Judge prompts are versioned files; every judgement stores a reason; `harness audit` samples judged cases for human labels and reports judge-vs-human agreement per judge version.
4. **Versions form a matrix.** Every run records set x agent x judge versions; `compare` warns loudly when they differ; `harness matrix` tabulates agents x tiers x tools.
5. **Bad cases get a category.** `data`, `tool_choice`, `ambiguity`, `reasoning` or `judge` - and an `ambiguity` case can only be promoted with a rewritten prompt, because the fix is the question, not the agent.

The demo is a fictional finance assistant (ticker resolution, earnings dates, percentage maths, a "no buy/sell advice" policy, citations) with two offline rule-based agents. `v2` is better than `baseline` almost everywhere and quietly worse on policy: the regression an eval exists to catch. All data is synthetic.

## Quickstart (offline, under a minute)

```bash
pip install pyyaml                                   # the only dependency
python -m harness lint                               # golden set sanity: peer answers, statuses, tolerances, ids
python -m harness run --agent baseline --gate unit:0.9 --judge fake
python -m harness run --agent v2       --gate unit:0.9 --judge fake
python -m harness compare baseline v2 --md compare.md
python -m harness matrix --agents baseline,v2 --reuse -v
python -m harness audit runs/v2-<ts>.json            # -> audit sheet for human labels
python -m harness run --agent baseline --tier dynamic --as-of 2027-01-05
python -m unittest discover -s tests                 # 92 stdlib tests
```

`--judge fake` is a deterministic offline stand-in that exercises the judge plumbing (prompt files, reasons, audit sheets). It grades by word overlap and must not be mistaken for an evaluation; drop the flag (or use `--judge anthropic` with `pip install anthropic` and `ANTHROPIC_API_KEY`) for real judged scores. Without any judge, judged checks are *skipped*, never failed.

What the commands show:

```
$ python -m harness run --agent baseline --gate unit:0.9 --judge fake
tier      tool               n   pass    avg   skip
unit      policy             4   100.0%  1.00  0
unit      ticker_resolution  4    50.0%  0.50  0
unit      (all)              22   59.1%  0.62  0
complex   (all)              6     n/a    n/a  6
...
Gates:
  gate unit:0.9 -> 59.1%  FAIL
  tier complex skipped: gate unit:0.9 failed (pass rate 59.1%)
  tier external skipped: ...
  tier dynamic skipped: ...
Judge coverage: 2 judged, 20 deterministic, 12 skipped
```

```
$ python -m harness matrix --agents baseline,v2 --reuse
agent     version  unit        complex     external    dynamic      ALL
baseline  1.0      59% / 0.62  n/a         n/a         n/a          59% / 0.62
v2        2.0      91% / 0.94  80% / 0.72  75% / 0.75  100% / 1.00  88% / 0.89

v2 vs baseline: wins 8, losses 1, ties 13
  regressions: policy-001
  gate hit: baseline unit:0.9 (59.1%)
```

`policy-001` is the point: v2 leaks "strong buy" into a refusal. An aggregate pass rate hides it; the per-tool table and the regressions line do not.

```
$ python -m harness run --agent baseline --tier dynamic --as-of 2027-01-05
dynamic  earnings_date  2    0.0%  0.00  0     # baseline answers from a static table; v2 tracks the calendar
```

## Why each rule

**Objective before open-ended (tiers + gates).** Judge-scored cases are expensive, noisy and easy to argue with. Deterministic unit cases are free and unambiguous. Running the tiers in order and gating (`--gate unit:0.9`) means a broken agent fails fast on facts, the skipped tiers are recorded as skipped with a reason (not silently omitted), and the judge budget is spent only on agents that deserve it.

**Two people write every answer (owner/peer + lint).** Most "agent errors" found in review turn out to be wrong expected values. Writing the answer twice, independently, and recording the calculation and source makes the golden set itself reviewable. `lint` turns the workflow into errors (disputed, owner != peer while agreed, duplicate ids, unknown scorers) and warnings (missing peer, tolerances implausibly small for the magnitude). `run` skips `draft`/`disputed` cases unless `--include-unagreed`.

**Judges must be calibrated (versioned prompts + audit).** A judge is a model with a prompt; change the prompt and the numbers move. Prompts live in `judges/<name>.v<N>.md`, the version is stamped into every run and every check result together with the judge's stated reason. `audit` samples *all* judged failures plus a random sample of judged passes into a CSV; a human labels them; `audit --apply` reports agreement per judge version, with false-pass / false-fail counts and the disagreements. Below ~90% agreement the judge, not the agent, is the thing to fix.

**Versions form a matrix (set x agent x judge).** "v2 is 88%" is meaningless without the set it ran on and the judge that scored it. Runs carry `set_version` (a content hash of every case file), `agent_version` (the module's `VERSION`), `judge_version` and `harness_version`. `compare` warns when set or judge versions differ and lists unmatched cases; `matrix` puts several agents side by side on one set and lists each agent's regressions against the first.

**Bad cases must be categorised (fix the question when it is ambiguity).** A production failure filed as "wrong answer" is not actionable. The category says who owns the fix: `data` (feed), `tool_choice` (routing), `reasoning` (model or prompt), `judge` (the scorer), `ambiguity` (the question). Ambiguity is the common one and the wrong fix is tuning the agent until it guesses what the question meant; `promote --rewrite` puts the clarified prompt into the golden set and keeps the original. Every promotion is a line in `cases/CHANGELOG.md` with the set version before and after.

## Field notes

Six things learned from running this method on a real finance Q&A agent for a year. They explain why each rule above looks the way it does.

1. **Most "agent errors" found in review were wrong expected answers or under-specified questions.** Fix the case set before you fix the agent. That is why owner/peer answers and `lint` exist.
2. **Data-layer errors impersonate model errors.** A wrong number from an API, a mis-routed tool, thin historical coverage: all of them look like "the model got it wrong". Tool-level objective cases must run as their own tier, or you will blame the model forever.
3. **A failure spike after a version change means "check the wording first".** Close vs intraday price, TTM vs one annualised quarter: if the prompt does not pin it down, a different reading gets scored as a regression. This is where the `ambiguity` category and `promote --rewrite` come from.
4. **Tolerances are set by humans.** Generated rubrics are useful for structure only; they will happily give a seven-figure number a tolerance of 1. The magnitude warning in `lint` is there for that.
5. **Spend judge budget only on agents that passed the factual tier; read every judged failure, sample the passes.** That is the gate rule and the `audit` sampling rule.
6. **"No data" and "made it up" are different failures and the eval must tell them apart.** The first is fixed at the data source and the refusal policy, the second in the prompt and citation requirements. The `citation` and `policy` scorers and the `data` / `reasoning` categories keep them separate.

## Commands

| command | what it does |
|---|---|
| `run --agent A [--tier unit,complex] [--tool T] [--gate unit:0.9] [--judge auto\|anthropic\|fake\|none] [--as-of DATE] [--include-unagreed] [--md out.md]` | run one agent tier by tier, save `runs/A-<ts>.json` |
| `compare A B [--out cmp.json] [--md cmp.md]` | per-tier/per-tool side by side, per-case win/loss/tie, regressions, version warnings |
| `report run.json [-v] [--md out.md]` | re-print a saved run |
| `matrix --agents A,B[,C] [--reuse] [-v] [--out m.json\|m.md]` | agents x tiers (x tools), regressions vs the first agent |
| `lint [--cases dir]` | static checks on the golden set; exit 1 on errors only |
| `audit run.json [--sample all\|random:N] [--out sheet.csv]` / `audit --apply sheet.csv` | judge audit sheet / judge-vs-human agreement under `runs/audits/` |
| `badcase add --category C ...` / `badcase list` / `badcase promote ID [--rewrite "..."] [--peer "..."]` | capture, group by category, promote into the golden set + changelog |
| `import --csv f.csv\|--jsonl f.jsonl --map "prompt=question,expected=answer,tool=category" --source S --license L [--out ...]` | external benchmark -> `external`-tier case file with provenance |

## Case schema

One YAML file per tool under `cases/golden/`. Short form (`scorer` + arguments) or long form (`checks`, score = mean, pass = all).

```yaml
version: 3                       # bump when you change the set; also part of set_version
tool: earnings_date
tier: unit                       # unit | complex | external | dynamic (file default, per-case override)
source: "sample-bench"           # external sets only: recorded on every run
license: "CC0-1.0"
cases:
  - id: earn-003                 # unique across all files
    prompt: "When is Nvidia's next earnings report?"
    context: {}                  # optional, passed straight to the agent
    tags: [company-name]
    scorer: contains             # short form ...
    expected: "2026-11-18"
    answer:                      # two people, one expected value
      owner: "2026-11-18"        # written by the case owner
      peer: "2026-11-18"         # written independently by a peer
      calculation: null          # how it was derived (e.g. "(125-100)/100*100 = 25")
      source: "fixture:agents/fixtures/market.json"
      status: agreed             # draft | agreed | disputed - run skips draft/disputed
  - id: earn-006
    prompt: "Cite your source: when does AMZN report?"
    checks:                      # ... or long form
      - {type: contains, expected: "2026-10-29"}
      - {type: citation, min: 1}
  - id: dyn-001                  # dynamic tier: the expectation moves with the date
    tier: dynamic
    prompt: "As of {as_of}, when is Apple's next earnings report?"
    resolver: agents.resolvers:earnings_date     # fn(as_of, **resolver_args) -> dict of placeholders
    resolver_args: {ticker: AAPL}
    scorer: contains
    expected: "{next_earnings}"
```

Cases without an `answer` block count as `agreed` (lint warns about the missing peer). `{today}` and `{as_of}` work in prompt, context and check values; `run --as-of` defaults to today, and dynamic-tier cases get `as_of` injected into the agent context.

## Scorers

Deterministic scorers make a failure a fact. The five *validation fields* an answer author fills in map onto the scorers marked with their field name; one requirement per check.

| type | field | arguments | passes when |
|---|---|---|---|
| `exact` | | `expected`, `case_sensitive` | whole answer equals expected (whitespace/case normalised) |
| `contains` | | `expected` (str or list), `case_sensitive` | every expected substring present |
| `regex` | | `pattern`, `ignore_case` | `re.search` matches |
| `numeric` | | `expected`, `tolerance`, `relative` | some number in the answer within tolerance |
| `json_key` | | `path`, `expected` | value at dotted path in `data` (or parsed JSON answer) equals expected |
| `policy` | | `forbidden` (list) | none of the forbidden phrases appear |
| `citation` | | `min` | at least `min` non-empty citations |
| `correctness` | correctness | `expected` | `exact` for strings, zero-tolerance `numeric` for numbers |
| `range` | range | `lo`, `hi`, `field?` | some number in the answer (or `data[field]`) within `[lo, hi]` |
| `keyword` | keyword | `points` (list), `min_hit?` | score = hits / len(points); pass when hits >= `min_hit` (default all) |
| `requirement` | requirement | `text`, `calculation?`, `must_contain_any?` | the judge says *yes* to the one requirement; without a judge, `must_contain_any` decides, else skipped |
| `tolerance` | tolerance | `expected`, `abs` \| `rel` | explicit numeric tolerance (default `abs: 0`) |
| `rubric` | | `name`, `min_grade?` | gate-first weighted rubric from `rubrics/<name>.yaml` (below) |
| `llm_judge` | | `rubric` (text), `threshold` | free-text rubric, judge score 0-10 scaled >= threshold; skipped without a judge |

Add a scorer by writing `(answer: dict, check: dict) -> Score` in `harness/scorers.py` and registering it in `SCORERS`; a `Score` carries `score`, `passed`, `detail` and an `extra` dict that is stored with the run (judge reason and version, keyword hits, rubric dimensions ...).

### Gate-first weighted rubric

`rubrics/research_answer.yaml` is a generic example:

```yaml
gates:                                   # checked first; a hit caps the grade
  - {id: no_advice,  cap: F, forbidden: ["you should buy", "strong buy"]}   # deterministic
  - {id: has_source, cap: C, min_citations: 1}                              # deterministic
  - {id: on_topic,   cap: F, judge: true, description: "Addresses the question asked."}
dimensions:                              # weights sum to 100, each judged 0-5, scaled
  - {id: accuracy,     weight: 40, description: "..."}
  - {id: completeness, weight: 30, description: "..."}
  - {id: reasoning,    weight: 20, description: "..."}
  - {id: clarity,      weight: 10, description: "..."}
bands: {A: 90, B: 75, C: 60, D: 40}      # lower bounds; below D is F
pass_band: C
```

Deterministic gates (`forbidden`, `required_any`, `required_all`, `min_citations`) run with no judge at all, so a forbidden phrase fails the case even offline. Dimensions are judged one at a time; each call lists the flaws already penalised by earlier dimensions (**single-attribution rule**: a flaw costs points in exactly one dimension). The result stores per-dimension scores, gate hits, points and grade.

### Judges

Prompts are `string.Template` markdown files: `judges/requirement.v1.md` (yes/no), `judges/rubric.v1.md` (0-10), `judges/dimension.v1.md` (0-5). Add `requirement.v2.md` and the highest version is used; runs record which. Backends: `--judge auto` (Anthropic adapter when `ANTHROPIC_API_KEY` is set, else none), `anthropic`, `fake`, `none`; or `HARNESS_JUDGE=fake`.

## Bad-case loop

```bash
python -m harness badcase add --agent v2 --category ambiguity \
    --prompt "When does Meta report?" --expected "2026-10-28"
python -m harness badcase list                       # grouped by category
python -m harness badcase promote bc-20260912-d0bb \
    --rewrite "When does Meta Platforms (META) next report earnings?" --peer "2026-10-28"
tail -1 cases/CHANGELOG.md
# | 2026-09-12 | bc-20260912-d0bb | ambiguity | 70d4174dfec1 -> 5951390d57b3 | prompt rewritten (was: ...) |
```

Promoted cases carry an `answer` block; without `--peer` they are `draft` and `run` skips them until a peer answer is added.

## External benchmarks

```bash
python -m harness import --csv examples/external_sample.csv \
    --map "prompt=question,expected=answer,tool=category" \
    --source "sample-bench" --license "CC0-1.0" --out cases/golden/external_sample-bench.yaml
```

The file lands in the `external` tier with `source`/`license` at file level; runs record them under `set_provenance` and reports list external sets separately. Imported answers are the benchmark's own, so lint does not ask for a peer.

## Plug in your own agent

```python
VERSION = "1.4.0"                       # recorded as agent_version

def answer(prompt: str, context: dict) -> dict:
    return {"answer": "The ticker symbol is AAPL.", "citations": ["https://..."], "data": {"ticker": "AAPL"}}
```

`context` is the case's `context:` mapping (plus `as_of` in the dynamic tier). Exceptions become failed cases with a traceback. Run by name (`agents/my_agent.py`), module path or file path. `agents/anthropic_agent.py` is an optional adapter for a Claude model.

## CI hint

```yaml
- run: pip install pyyaml
- run: python -m harness lint                                   # fails on disputed / mismatched answers
- run: python -m unittest discover -s tests
- run: python -m harness run --agent v2 --gate unit:0.9 --md run.md
- run: python -m harness compare baseline v2 --out compare.json  # fail if tally.loss > 0
```

Keep a reference run for the shipped version under version control and compare every PR against it; the "Regressions in ..." line is the review checklist.

## Layout

```
harness/   cases.py scorers.py judge.py rubric.py runner.py compare.py matrix.py
           report.py markdown.py lint.py audit.py badcase.py importer.py cli.py
agents/    baseline.py v2.py resolvers.py anthropic_agent.py fixtures/market.json
cases/     golden/*.yaml (one file per tool/tier)  backlog/  CHANGELOG.md
judges/    requirement.v1.md rubric.v1.md dimension.v1.md      rubrics/  research_answer.yaml
examples/  external_sample.csv        runs/  saved runs, runs/audits/    tests/  unittest suite
```

## License

MIT - Jing Zeng.
