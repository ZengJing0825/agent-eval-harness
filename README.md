# agent-eval-harness

[中文](README.zh.md)

A test harness for **finance Q&A and investment-research AI agents**.

**When it applies.** Your agent answers questions like "when is Nvidia's next earnings call", "how much has Bitcoin dropped this month", "is A or B the better buy". Every time you change a prompt, swap the model or touch a data API you need to know: is the new version better or worse? On which tool and which kind of question? Was the failure caused by the question, the data, the scorer, or the agent itself?

**Who it is for.** Product managers and engineers building their own agent who want, before each release, one table of per-tool pass rates plus a regression list.

**What you get.** A tiered case set, a one-writes-one-reviews workflow, versioned and calibratable scorers, a set × agent × scorer comparison matrix, and failed cases categorised by cause and fed back into the set. Standard library + PyYAML only; the bundled offline demo runs in about a second; another domain only needs its own cases and scoring rules. All demo data is synthetic.

## What you get (preview)

Run the offline demo and these two screens are what you read before a release. Every number below comes from the bundled synthetic set: 34 cases, two demo agents, an offline stand-in judge.

**One table per release.** Pass rate and average score per tier and per tool, whether each tier met its target, then every failing case with what the agent answered and why it failed.

![harness run: tier x tool table, targets, failing cases](docs/preview/run.png)

**Two versions side by side, regressions named.** `v2` beats `baseline` 13 to 2 overall. The aggregate would hide the two losses; the per-tool table and the regression line do not: `policy-001` is `v2` leaking "strong buy" inside a refusal.

![harness matrix: agents x tiers with the regression list](docs/preview/matrix.png)

Every run is also saved as JSON and can be re-printed as Markdown (`--md`) for a PR comment; `audit` turns the judged cases into a CSV sheet for human labelling. The Quickstart below reproduces both screens in about a second.

## Five ways it differs from a generic eval framework

promptfoo, DeepEval and their peers evaluate "prompt + model". A finance agent mostly fails elsewhere: in the data feed, in tool routing, and in how the question itself is worded. This harness freezes a workflow run for a year into five rules, one command each:

1. **Four tiers, pass rate per tool, never just a total.** `unit` (one fact) → `complex` (multi-step calculation) → `external` (public benchmarks) → `dynamic` (the answer moves with the date asked). Each tier has a target; the report lists target / actual / met? per tool. Command: `run --gate unit:0.9`
2. **One person writes the answer, another reviews it; a disagreement rewrites the question.** Answers carry the calculation and the source; the reviewer records a verdict, not a second answer; nobody votes, the two agree on the definition and pin it into the wording. Command: `lint`
3. **Attribute a failure before deciding who fixes it.** Six categories: data, routing, ambiguous question, reasoning, unsupported tool, wrong judge. Attributed cases flow into the next set version with a changelog line; judge errors go to a dispute log, never into the set. Command: `badcase add / promote`
4. **Judges are versioned and calibrated on a schedule.** Three judge rules are stated in every prompt; every verdict keeps a reason; sampling is "every 0, lowest first, ten random 1s"; after human labelling, agreement is reported per judge version. Command: `audit`
5. **Every run = set version × agent version × judge version.** Mismatched versions get a loud warning; several agents side by side on one set, regressions listed outright. Command: `compare / matrix`

## How it works

```
cases/golden/*.yaml   four-tier case set; every case has one owner and one reviewer
      │
      │  lint          sanity: reviews, statuses, tolerances, ids
      ▼
run --agent X         deterministic scorers for factual cases, a versioned judge for open ones  ──▶  runs/X-<ts>.json
      │
      ├── compare A B / matrix    pass rate per tier and per tool, per-case win / loss, regression list
      └── audit                   sample for human labelling  ──▶  agreement per judge version

a failure in production or review  ──▶  badcase add --category  ──▶  badcase promote  ──▶  back into the set + CHANGELOG
```

Every step produces files (YAML, JSON, Markdown, CSV) that go under version control and can be compared in CI. Two concepts run through every command.

### What the four tiers are

| Tier | What it is | Example (fictional) | What it tests |
|---|---|---|---|
| `unit` | A single-fact fill-in with one fixed answer and no real calculation | A company's net profit for one fiscal quarter | Whether the tool and the data source return the right thing |
| `complex` | Multi-step retrieval and calculation, still with a fixed answer | Compare two companies over the last four quarters from a free-cash-flow angle and give the gap and a conclusion | The whole retrieve-and-compute chain |
| `external` | Public finance QA benchmarks imported as-is | Questions from a public benchmark | Other people's questions, so you do not overfit your own set |
| `dynamic` | Questions whose answer depends on when they are asked | "Apple's revenue last quarter" asked on 2025-01-01 vs 2026-01-01 | Whether the agent reads the time intent and picks the right period |

In code the `dynamic` tier uses `{as_of}` placeholders in the prompt and the expected value; a small resolver computes the expectation for the given date, and `run --as-of DATE` sets "today".

### What the bad-case loop is

A *bad case* is a failure found in production or in review. *Feeding it back* means turning it into a regular case in the golden set so every future run tests it (the command is `badcase promote`). Categorise before you promote, because the category says who fixes it: `data` fixes the data source; `tool_choice` fixes routing; `reasoning` fixes the prompt; `ambiguity` means the question itself was unclear, so the prompt must be rewritten before it enters the set; `unsupported` means the tool does not exist yet, so the case enters the set marked not-to-run; `judge` means the judge scored it wrong, so it goes to the judge-dispute log, not the golden set.

## Quickstart (offline, under a minute)

In the demo `v2` beats `baseline` almost everywhere: 13 wins, 2 losses. One of the two losses is `policy-001`: `v2` leaks "strong buy" while refusing to give advice. The total pass rate hides it; the per-tool table and the regression list show it. The commands below reproduce exactly that.

```bash
pip install pyyaml                                   # the only dependency
python -m harness lint                               # golden set sanity: reviews, statuses, tolerances, ids
python -m harness run --agent baseline --gate unit:0.9 --judge fake
python -m harness run --agent v2       --gate unit:0.9 --judge fake --label "demo set 2026-09-12"
python -m harness compare baseline v2 --md compare.md
python -m harness matrix --agents baseline,v2 --reuse -v
python -m harness audit runs/v2-<ts>.json            # -> audit sheet for human labels
python -m harness run --agent baseline --tier dynamic --as-of 2027-01-05
python -m unittest discover -s tests                 # 120 stdlib tests
```

`--judge fake` is a deterministic offline stand-in that exercises the judge plumbing (prompt files, reasons, audit sheets). It grades by word overlap and must not be mistaken for an evaluation; drop the flag (or use `--judge anthropic` with `pip install anthropic` and `ANTHROPIC_API_KEY`) for real judged scores. Without any judge, judged checks are *skipped*, never failed.

What the commands show (the bundled `harness.yaml` sets targets `unit: 0.8, complex: 0.8`; `--gate unit:0.9` overrides the unit one for this run):

```
$ python -m harness run --agent baseline --gate unit:0.9 --judge fake
Run: agent=baseline  timestamp=2026-09-12T13:54:59Z  cases=34
Versions: agent=1.0  set=8a52804b918d (2026-09-12)  judge=fake:dimension.v2,requirement.v2,rubric.v2  harness=0.3.0
tier      tool               n   pass    avg   skip
unit      policy             4   100.0%  1.00  0
unit      ticker_resolution  4    50.0%  0.50  0
unit      (all)              22   59.1%  0.62  0
complex   (all)              6    40.0%  0.35  1
external  (all)              4    75.0%  0.75  0
dynamic   (all)              2     0.0%  0.25  0
...
Targets:
  mode=target  (targets are recorded; every tier still runs)
  tier     target  actual  met?
  unit     90%     59.1%   NO
  complex  80%     40.0%   NO
Judge coverage: 3 judged, 30 deterministic, 1 skipped
```

With `--gate-mode strict` the classic hard gate is back: unit misses its target, so the three later tiers are skipped with a reason:

```
Targets:
  mode=strict  (unmet target skips later tiers)
  unit  90%     59.1%   NO
  tier complex skipped: gate unit:0.9 failed (pass rate 59.1%)
  tier external skipped: gate unit:0.9 failed (pass rate 59.1%)
  tier dynamic skipped: gate unit:0.9 failed (pass rate 59.1%)
```

```
$ python -m harness matrix --agents baseline,v2 --reuse
Matrix: reference=baseline  set=8a52804b918d (2026-09-12)  judge=fake:dimension.v2,requirement.v2,rubric.v2
agent     version  unit        complex     external    dynamic      ALL
baseline  1.0      59% / 0.62  40% / 0.35  75% / 0.75  0% / 0.25    55% / 0.57
v2        2.0      91% / 0.94  80% / 0.72  75% / 0.75  100% / 1.00  88% / 0.89

v2 vs baseline: wins 13, losses 2, ties 18
  regressions: policy-001, research-002
  target missed: baseline unit 90% -> 59.1%
  target missed: baseline complex 80% -> 40.0%
```

`policy-001` is the point: v2 leaks "strong buy" into a refusal. An aggregate pass rate hides it; the per-tool table and the regressions line do not.

```
$ python -m harness audit runs/v2-20260912T135500Z.json
Audit sheet: 3 rows (1 judged failures, 0 low-scoring passes, 2 sampled passes; rule all-fails,low-first,pass:10) out of 3 judged checks in 34 cases
Fill in human_passed (yes/no) or human_score (0-1), reviewer and human_note, then: harness audit --apply runs/v2-20260912T135500Z-audit.csv
```

```
$ python -m harness run --agent baseline --tier dynamic --as-of 2027-01-05
dynamic  earnings_date  2    0.0%  0.00  0     # baseline answers from a static table; v2 tracks the calendar
```

## Design decisions and lessons

The workflow comes out of close to a year of evaluating a finance Q&A agent. The whole of it is one sentence: get the questions and answers right first, then evaluate the agent. Each item below is "how it is done" plus "why it was decided that way".

**Where the questions come from.** Three sources: written by hand against business scenarios; imported from public benchmarks; generated in bulk by AI from a playbook of current topics, then filtered by hand. Every question is tagged with the tool or data source it exercises and whether that is supported today; questions whose answer changes every day stay out of the static set. The set is split into files per tool and versioned by date. Lesson: data-layer errors impersonate model errors. A wrong number from an API, a mis-routed tool, thin historical coverage all look like "the model got it wrong". So tool-level factual cases run as their own tier, and a question whose tool is not wired up yet is marked `unsupported` so it neither drags the score down nor pretends to pass.

**How answers are decided.** One owner writes the answer, the calculation and the source; one peer reviews. A disagreement is not settled by voting: the two agree on the definition (close or intraday, UTC or local, inclusive bounds or not) and rewrite the question so it is pinned down. Review is always the bottleneck: in a set of 100 the owner had written 97 by the time the peer had reviewed 42, so lint treats a missing review as a warning, not an error. Lesson: a large share of the "agent errors" found in review were wrong expected answers or under-specified questions; a failure spike after a version change means check the wording first, then look for a regression.

**How rubrics are written.** Only four validation fields: correctness (zero tolerance), range (tolerance set by a human), keyword (scoring points), requirement (one requirement with the calculation rule folded in). One rubric holds one requirement. Lesson: tolerances are set by humans. A generated rubric will give a seven-figure number a tolerance of 1; borrow its structure only. The magnitude warning in `lint` exists for that.

**How the judge judges.** Reference-answer cases are scored deterministically; open cases go to a judge with a version number. Three rules: the rubric takes precedence over the general instructions; an unmet requirement scores 0 outright; an answer that contains the reference and is richer is not penalised. Every verdict keeps its reason. Calibration: read every 0, lowest scores first, sample the 1s; a misjudgement can be overturned, and the fix goes into the judge, not the agent. Lesson: spend judge budget only on agents that passed the factual tier, which is where `--gate-mode strict` and the default sampling rule come from. What we actually did was re-run the same set after every judge-prompt change and compare; the repo turns that into agreement tracked per judge version.

**How experiments are run.** Every run = set version × agent or backend version × judge version. Run one set against different backend versions to see regressions (one backend change dropped every score by more than 0.1) and against different model versions to see stability. Every case has a timeout.

**How results are used.** Attribute before fixing: a wrong answer or definition means fix the question; an unsupported tool means mark it not tested; a data or API error goes to the backend; a misjudgement means fix the judge; model behaviour (empty reasoning, signal not found) means fix the prompt. Attributed cases flow into the next set version and the changelog. The set grew from 30 to 100 questions, then split by asset class into four sets; the first build-type evaluation passed 5 of 27, and three months later the stock and crypto sets were stable above 0.9 and the screener and new-tool sets above 0.5. Lesson: "no data" and "made it up" are different failures. The first is fixed at the data source and the refusal policy, the second in the prompt and the citation requirement; the `citation` and `policy` scorers and the `data` / `reasoning` categories keep them apart.

**A separate track for open questions.** Analytical output with no reference answer uses a gate-first weighted rubric: hard gates first (safety, key facts, fatal bias), then general dimensions, then skill dimensions; one flaw costs points in exactly one dimension; the result is banded A/B/C/F.

## Commands

| command | what it does |
|---|---|
| `run --agent A [--tier unit,complex] [--tool T] [--gate unit:0.9] [--gate-mode target\|strict] [--config harness.yaml] [--judge auto\|anthropic\|fake\|none] [--as-of DATE] [--include-unagreed] [--label "..."] [--timeout 900] [--md out.md]` | run one agent tier by tier, save `runs/A-<ts>.json` |
| `compare A B [--out cmp.json] [--md cmp.md]` | per-tier/per-tool side by side, per-case win/loss/tie, regressions, versions with labels, version warnings |
| `report run.json [-v] [--md out.md]` | re-print a saved run |
| `matrix --agents A,B[,C] [--reuse] [--gate-mode ...] [-v] [--out m.json\|m.md]` | agents x tiers (x tools), regressions vs the first agent, targets missed |
| `lint [--cases dir]` | static checks on the golden set; exit 1 on errors only |
| `audit run.json [--sample all-fails,low-first,pass:10] [--out sheet.csv]` / `audit --apply sheet.csv` | judge audit sheet / judge-vs-human agreement and overturn counts per judge version and per tier, under `runs/audits/` |
| `badcase add --category C ...` / `badcase list` / `badcase promote ID [--rewrite "..."] [--reviewer NAME --agree]` | capture, group by category (with the fix owner), promote into the golden set + changelog; a `judge` case goes to `runs/audits/judge_disputes.jsonl` |
| `import --csv f.csv\|--jsonl f.jsonl --map "prompt=question,expected=answer,tool=category" [--prompt-template "{table}\n{qa.question}"] --source S --license L [--out ...]` | external benchmark -> `external`-tier case file with provenance; dotted paths and JSON arrays accepted |

`--sample` tokens: `all-fails` (every judged failure), `low-first` (every judged pass scored below 0.5, listed first), `pass:<N>` (a random N of the remaining passes; `pass:all` keeps them all); `all` and the legacy `random:<N>` still work.

## How each rule is implemented

**Objective before open-ended (tiers + targets).** Judge-scored cases are expensive, noisy and easy to argue with. Deterministic unit cases are free and unambiguous. The tiers run in order with a target per tier - `--gate unit:0.9` on the command line, or `targets: {unit: 0.8, complex: 0.8}` in an optional `harness.yaml` at the repo root (the command line overrides per tier). The default `target` mode records target / actual / met? for every tier and keeps running, so the report shows at a glance which tier fell short; `--gate-mode strict` restores the hard gate: an unmet target skips the later tiers, and the skipped tiers are recorded with a reason (not silently omitted), so the judge budget is spent only on agents that deserve it.

**One owner writes, one peer reviews (owner/peer + lint).** Most "agent errors" found in review turn out to be wrong expected values or under-specified questions. The owner writes the answer, the calculation and the source; the peer does not write a second answer but records a review: `peer: {reviewer, verdict: agree|disagree, note}`. A disagreement is not settled by voting - the two agree on the definition, rewrite the question so it is pinned down, and review again. `status` is derived from the verdict when absent: agree -> `agreed`, disagree -> `disputed`, no review yet -> `draft`. `lint` turns the workflow into errors (`disputed` - fix the question wording or the owner answer, then re-review; `agreed` with a disagree verdict; duplicate ids; unknown scorers) and warnings (missing review, tolerances implausibly small for the magnitude). Review is always the bottleneck, which is why a missing review is only a warning and never blocks a run. `run` skips `draft`/`disputed` cases unless `--include-unagreed`. The old form `peer: "<second answer>"` still loads: equal to the owner it becomes an `agree` verdict, different it becomes `disagree` with the note "peer wrote a different answer".

**Judges must be calibrated (three rules + versioned prompts + audit).** A judge is a model with a prompt; change the prompt and the numbers move. Three rules are stated in every judge prompt (`judges/*.v2.md`; `harness.judge.RULES` is the single source, `judges/README.md` explains them): the case rubric or requirement takes precedence over the general instructions; an unmet requirement scores 0 for that check, no partial credit; an answer that contains the reference and adds correct extra detail is not penalised. Prompts live in `judges/<name>.v<N>.md`, a published version is never edited, and the version is stamped into every run and every check result together with the judge's stated reason. `audit` samples with the rule `all-fails,low-first,pass:10`: every judged failure, then every judged pass scored below 0.5 (low first), then a random 10 of the rest; the sheet has `human_passed` / `human_score` / `reviewer` / `human_note` columns. `audit --apply` reports agreement per judge version and per tier, plus how many judgements the human overturned to pass (judge 0, human 1) and overturned to fail. Agreement is tracked per judge version; when it drops, the judge is revised, not the agent. `judge`-category bad cases land in `runs/audits/judge_disputes.jsonl` as the input for the next prompt version.

**Versions form a matrix (set x agent x judge).** "v2 is 88%" is meaningless without the set it ran on and the judge that scored it. Runs carry `set_version` (a content hash of every case file), `set_labels` (a human label per file: the file's `set_label:`, else the date of its last git commit, else today), `agent_version` (the module's `VERSION`), `judge_version` and `harness_version`; `run --label "set + date"` names the experiment and is stored on the run. `compare` and `matrix` print the label next to the hash, warn when set or judge versions differ and list unmatched cases; `matrix` puts several agents side by side on one set and lists each agent's regressions against the first. Every case has a timeout: `run --timeout 900` (the default); an agent call over the limit is a failed case with reason "timeout", implemented with a thread join - nothing is killed.

**Bad cases must be categorised (the category says who owns the fix).** A production failure filed as "wrong answer" is not actionable. Six categories: `data` (feed), `tool_choice` (routing), `ambiguity` (the question), `reasoning` (model or prompt), `unsupported` (the tool or data does not exist yet), `judge` (the scorer). Ambiguity is the common one and the wrong fix is tuning the agent until it guesses what the question meant; `promote --rewrite` puts the clarified prompt into the golden set and keeps the original. An `unsupported` case is promoted with `status: skipped_unsupported`: `run` skips it (reason `unsupported`), `report` counts it separately, and the status is changed once the tool exists. A `judge` case never enters the golden set: `promote` appends it to `runs/audits/judge_disputes.jsonl`, because the fix is the judge. Every promotion is a line in `cases/CHANGELOG.md` with the set version before and after.

## Case schema

One YAML file per tool under `cases/golden/`. Short form (`scorer` + arguments) or long form (`checks`, score = mean, pass = all).

```yaml
version: 3                       # bump when you change the set; also part of set_version
set_label: "2026-09-12"          # optional human label; default: the file's last git commit date, else today
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
    answer:                      # one owner writes, one peer reviews
      owner: "2026-11-18"        # the answer, written by the case owner
      peer:                      # the review record - not a second answer
        reviewer: peer-a         # who reviewed (fictional placeholder)
        verdict: agree           # agree | disagree | null (not reviewed yet)
        note: null               # on disagree: which definition is unclear
      calculation: null          # how it was derived (e.g. "(125-100)/100*100 = 25")
      source: "fixture:agents/fixtures/market.json"
      status: agreed             # derived when absent: agree -> agreed, disagree -> disputed, none -> draft
                                 # also skipped_unsupported: tool not supported yet; run skips it, report counts it
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

Cases without an `answer` block count as `agreed` (lint warns about the missing review). `{today}` and `{as_of}` work in prompt, context and check values; `run --as-of` defaults to today, and dynamic-tier cases get `as_of` injected into the agent context.

## Scorers

Deterministic scorers make a failure a fact. The *validation fields* an answer author fills in map onto the scorers marked with their field name; one requirement per check.

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
| `requirement` | requirement | `text`, `calculation?`, `must_contain_any?` | the judge says *yes* to the one requirement; *no* is 0, never partial credit (judge rule 2); without a judge, `must_contain_any` decides, else skipped |
| `tolerance` | tolerance | `expected`, `abs` \| `rel` | explicit numeric tolerance (default `abs: 0`) |
| `rubric` | | `name`, `min_grade?` | gate-first weighted rubric from `rubrics/<name>.yaml` (below) |
| `llm_judge` | | `rubric` (text), `threshold` | free-text rubric, judge score 0-10 scaled >= threshold; skipped without a judge |

Add a scorer by writing `(answer: dict, check: dict) -> Score` in `harness/scorers.py` and registering it in `SCORERS`; a `Score` carries `score`, `passed`, `detail` and an `extra` dict that is stored with the run (judge reason and version, keyword hits, rubric dimensions ...). A case whose agent call timed out has no check results, only a single failed `type: timeout` entry.

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

Prompts are `string.Template` markdown files: `judges/requirement.v2.md` (yes/no), `judges/rubric.v2.md` (0-10), `judges/dimension.v2.md` (0-5). The three judge rules appear verbatim in every prompt (`harness.judge.RULES` is the single source; a custom prompt can reference them with the `${rules}` placeholder):

1. The case rubric or requirement takes precedence over the general instructions in the prompt.
2. An unmet requirement scores 0 for that check; there is no partial credit.
3. An answer that contains the reference answer and adds correct extra detail is not penalised - richer than the reference is fine.

The versioning convention is in `judges/README.md`: never edit a published version; add `requirement.v3.md` and the highest version is used automatically, and runs record which. v1 files stay for reproducing old runs. Backends: `--judge auto` (Anthropic adapter when `ANTHROPIC_API_KEY` is set, else none), `anthropic`, `fake`, `none`; or `HARNESS_JUDGE=fake`.

### Judge audit

```bash
python -m harness audit runs/v2-<ts>.json            # default --sample all-fails,low-first,pass:10
# fill in human_passed (yes/no) or human_score (0-1), reviewer and human_note, then:
python -m harness audit --apply runs/v2-<ts>-audit.csv
```

```
Judge audit: 3 labelled, 0 unlabelled rows  reviewers: peer-a

Per judge version:
judge           n  agreement  overturned to pass  overturned to fail
rubric.v2       1  0.0%       1                   0
requirement.v2  1  100.0%     0                   0
ALL             3  66.7%      1                   0

Per tier:
tier     n  agreement  overturned to pass  overturned to fail
unit     2  50.0%      1                   0
complex  1  100.0%     0                   0

Disagreements:
  - explain-001 [rubric.v2] judge=fail human=pass (peer-a): fake judge: 0/1 rubric words found in answer | note: formula present and correct; fake judge missed it
```

Many "overturned to pass" means the judge is too strict (the demo's fake judge is); many "overturned to fail" means it is too lenient. Either way the fix is a new prompt version, then the same sheet again.

## Bad-case loop

```bash
python -m harness badcase add --agent v2 --category ambiguity \
    --prompt "When does Meta report?" --expected "2026-10-28"
python -m harness badcase add --agent v2 --category unsupported --tool reverse_lookup \
    --prompt "Which company trades under the ticker META?" --expected "Meta Platforms" --note "reverse lookup not built yet"
python -m harness badcase add --agent v2 --category judge \
    --prompt "Explain in two sentences what a percentage change is." --expected "(new - old) / old" \
    --note "judge penalised correct extra detail"
python -m harness badcase list                       # grouped by category, with the fix owner
python -m harness badcase promote bc-20260912-819d \
    --rewrite "When does Meta Platforms (META) next report earnings?" --reviewer peer-b --agree
python -m harness badcase promote bc-20260912-1bba --reviewer peer-b --agree     # -> status: skipped_unsupported
python -m harness badcase promote bc-20260912-c102                               # -> runs/audits/judge_disputes.jsonl
tail -3 cases/CHANGELOG.md
```

```
$ python -m harness badcase list
[ambiguity] 1  - fix the question: promote --rewrite
  bc-20260912-819d  [backlog] agent=v2  'When does Meta report?'
[unsupported] 1  - not supported yet: promote -> status skipped_unsupported (run skips, report counts)
  bc-20260912-1bba  [reverse_lookup] agent=v2  'Which company trades under the ticker META?'
[judge] 1  - fix the judge, not the agent: promote -> runs/audits/judge_disputes.jsonl, never the golden set
  bc-20260912-c102  [backlog] agent=v2  'Explain in two sentences what a percentage change is.'

$ tail -3 cases/CHANGELOG.md
| 2026-09-12 | bc-20260912-1bba | unsupported | 8a52804b918d -> 124bec0348e0 | reverse lookup not built yet |
| 2026-09-12 | bc-20260912-819d | ambiguity | 124bec0348e0 -> 8e909f3b436a | prompt rewritten (was: 'When does Meta report?'); |
| 2026-09-12 | bc-20260912-c102 | judge | 8e909f3b436a -> 8e909f3b436a | judge dispute recorded in runs/audits/judge_disputes.jsonl (fix the judge, not the agent); judge penalised correct extra detail |
```

Promoted cases carry an `answer` block whose `peer` records `--reviewer` and the verdict; without `--agree` they are `draft` and `run` skips them until the review is added. `--peer "<answer>"` is a deprecated alias that records an `agree` review. A `judge` promotion leaves the set version unchanged (`before -> before` in the changelog).

## External benchmarks

```bash
python -m harness import --csv examples/external_sample.csv \
    --map "prompt=question,expected=answer,tool=category" \
    --source "sample-bench" --license "CC0-1.0" --out cases/golden/external_sample-bench.yaml
```

The file lands in the `external` tier with `source`/`license` at file level; runs record them under `set_provenance` and reports list external sets separately. Imported answers are the benchmark's own, so lint does not ask for a review.

### Public benchmarks you can import today

Checked on 2026-09-12 against each project's own page. Only links, commands and two tiny converters live here; the data stays where its licence puts it. Three importer conveniences make the rows below one-liners: `--jsonl` also accepts a file holding one JSON array, `--map` values may be dotted paths into nested JSON (`expected=qa.answer`), and `--prompt-template` builds the prompt from several fields (lists render one item per line, tables as `a \| b` rows). Sets published only as Parquet on Hugging Face are exported first (`pip install datasets`): `python -c "import datasets; datasets.load_dataset('kensho/DocFinQA', split='test').to_json('docfinqa_test.jsonl')"`.

| Name | Link | Questions and size | Fields | Licence | Import |
|---|---|---|---|---|---|
| FinanceBench | [GitHub](https://github.com/patronus-ai/financebench) / [HF](https://huggingface.co/datasets/PatronusAI/financebench) | single-fact and light-reasoning questions over 10-K/10-Q filings; company and period named in the question; 150 open-source rows (`financebench_open_source.jsonl`) | `financebench_id`, `question`, `answer`, `question_type`, `company`, `doc_name`, `evidence` | CC BY-NC 4.0 (non-commercial) | `python -m harness import --jsonl financebench_open_source.jsonl --map "prompt=question,expected=answer,tool=question_type,id=financebench_id" --source financebench --license "CC BY-NC 4.0"` (answers are formatted strings such as `$1577.00`; review `expected` or score with a judge) |
| FinSearchComp | [GitHub](https://github.com/randomtutu/FinSearchComp) / [HF](https://huggingface.co/datasets/ByteSeedXpert/FinSearchComp) | open-domain search questions in three tasks: T1 time-sensitive fetching, T2 simple historical lookup, T3 complex investigation; global and Greater China (Chinese) subsets; 635 rows (`data/finsearchcomp_data.json`, a JSON array) | `prompt_id`, `prompt`, `response_reference`, `label` (task and region), plus judge prompts | CC BY 4.0 | `python -m harness import --jsonl data/finsearchcomp_data.json --map "prompt=prompt,expected=response_reference,tool=label,id=prompt_id" --source finsearchcomp --license "CC BY 4.0"` (`response_reference` carries a tolerance note, so judge it or trim it; T1 answers change daily and belong in `dynamic`, not `external`) |
| FinQA | [GitHub](https://github.com/czyssrs/FinQA) | multi-step numeric reasoning over one filing page (text plus table); 8,281 questions (6,251 / 883 / 1,147) as one JSON array per split | `pre_text`, `post_text`, `table`, `id`; nested `qa.question`, `qa.answer`, `qa.exe_ans`, `qa.program` | MIT | `python -m harness import --jsonl dataset/test.json --prompt-template "{pre_text}\n{table}\n{post_text}\n\n{qa.question}" --map "expected=qa.answer,id=id" --source finqa --license MIT` (or `--map "expected=qa.exe_ans,id=id" --scorer numeric`) |
| ConvFinQA | [GitHub](https://github.com/czyssrs/ConvFinQA) | multi-turn conversational numeric reasoning over a FinQA page; 3,892 conversations / 14,115 turns (`data.zip`) | `annotation.dialogue_break` (turn questions), `annotation.exe_ans_list` (turn answers), `pre_text`, `table`, `post_text`, `id` | MIT | `python examples/convert_convfinqa.py data/test.json convfinqa_test.jsonl` then `python -m harness import --jsonl convfinqa_test.jsonl --map "id=id,note=note" --source convfinqa --license MIT` (one case per turn; earlier turns and their gold answers are in the prompt) |
| TAT-QA | [GitHub](https://github.com/NExTplusplus/TAT-QA) | span / multi-span / arithmetic / count questions over a table plus paragraphs from annual reports; 16,552 questions on 2,757 contexts (`dataset_raw/`) | per context `table.table`, `paragraphs[].text`, `questions[]` with `uid`, `question`, `answer`, `answer_type`, `scale`, `answer_from` | CC BY 4.0 for the data (README); repo code MIT | `python examples/convert_tatqa.py dataset_raw/tatqa_dataset_dev.json tatqa_dev.jsonl` then `python -m harness import --jsonl tatqa_dev.jsonl --map "id=id,tool=tool,note=note" --source tat-qa --license "CC BY 4.0"` (`tool` becomes the answer type, so pass rates split by it) |
| DocFinQA | [HF](https://huggingface.co/datasets/kensho/DocFinQA) | FinQA questions re-attached to the full SEC filing (about 123k words of context each); 7,437 rows (5,740 / 780 / 922), Parquet | `Context`, `Question`, `Program`, `Answer` | MIT | export the split, then `python -m harness import --jsonl docfinqa_test.jsonl --prompt-template "{Context}\n\n{Question}" --map "expected=Answer" --source docfinqa --license MIT` (prompts exceed 100k words; check the agent's context budget and `run --timeout`) |
| BizBench | [HF](https://huggingface.co/datasets/kensho/bizbench) | eight quantitative tasks over SEC filings: SEC-Num (number extraction), FinKnow (multiple choice), ConvFinQA (E) and TAT-QA (E) extraction, plus FinCode / CodeFinQA / CodeTATQA / FormulaEval which expect Python; 14,377 train / 4,673 test, Parquet | `question`, `answer`, `task`, `context`, `context_type`, `options`, `program` | Apache 2.0 | export, then `python -m harness import --jsonl bizbench_test.jsonl --prompt-template "{context}\n\n{question}\n{options}" --map "expected=answer,tool=task" --source bizbench --license "Apache-2.0"` (drop the code tasks by `task` during export unless the agent answers in Python) |
| FinanceMath | [GitHub](https://github.com/yale-nlp/FinanceMath) / [HF](https://huggingface.co/datasets/yale-nlp/FinanceMath) | knowledge-intensive finance maths word problems, some with markdown tables; 200 validation + 1,000 test (test answers released July 2026), Parquet | `question_id`, `question`, `tables`, `python_solution`, `ground_truth`, `topic` | MIT (dataset card) | export (log in to Hugging Face if the page asks), then `python -m harness import --jsonl financemath_test.jsonl --prompt-template "{tables}\n\n{question}" --map "expected=ground_truth,tool=topic,id=question_id" --scorer numeric --source financemath --license MIT` |
| EconLogicQA | [HF](https://huggingface.co/datasets/yinzhu-quan/econ_logic_qa) | order four economic or business events; the answer is a letter sequence such as `D, A, C, B`; 650 rows (390 / 130 / 130), Parquet | `Question`, `A`, `B`, `C`, `D`, `Answer` | CC BY-NC-SA 4.0 (non-commercial, share-alike) | export, then `python -m harness import --jsonl econlogicqa_test.jsonl --prompt-template "{Question}\nA. {A}\nB. {B}\nC. {C}\nD. {D}\nAnswer with the letters in order, comma-separated." --map "expected=Answer" --source econlogicqa --license "CC BY-NC-SA 4.0"` |
| FinBen / PIXIU `flare-finqa` | [GitHub](https://github.com/The-FinAI/PIXIU) / [HF](https://huggingface.co/datasets/TheFinAI/flare-finqa) | FinQA re-packaged with the context already inside `query`; 6,251 / 883 / 1,147; gated: request access on Hugging Face and accept a non-commercial-use agreement | `id`, `query`, `answer`, `text` | not stated on the card; the access form binds you to non-commercial use; repo code MIT | export after access, then `python -m harness import --jsonl flare_finqa_test.jsonl --map "prompt=query,expected=answer,id=id" --source flare-finqa --license "non-commercial (HF gated agreement)"` |

Checked but not listed: FiQA-2018 (files on Google Drive only, non-commercial, and an answer-ranking task rather than QA), FinGPT `fingpt-fiqa_qa` (free-text opinion answers, no licence on the card), SEC-QA (paper only, no public data release found), Fin-Fact (claim verification, not QA).

Every import stamps `source` and `license` at file level and `harness run` copies them into the run JSON under `set_provenance` (also `harness.cases.set_provenance("cases/golden")`), so a report cites the benchmark and its terms next to the numbers; keep `--license` as the benchmark's own page words it.

## Plug in your own agent

```python
VERSION = "1.4.0"                       # recorded as agent_version

def answer(prompt: str, context: dict) -> dict:
    return {"answer": "The ticker symbol is AAPL.", "citations": ["https://..."], "data": {"ticker": "AAPL"}}
```

`context` is the case's `context:` mapping (plus `as_of` in the dynamic tier). Exceptions become failed cases with a traceback; a call that exceeds `--timeout` (default 900 s) becomes a failed case with reason "timeout" (a thread join - nothing is killed, a stuck call keeps running in the background until the process exits). Run by name (`agents/my_agent.py`), module path or file path. `agents/anthropic_agent.py` is an optional adapter for a Claude model.

## CI hint

```yaml
- run: pip install pyyaml
- run: python -m harness lint                                   # fails on disputed / agreed-but-disagree
- run: python -m unittest discover -s tests
- run: python -m harness run --agent v2 --gate unit:0.9 --gate-mode strict --md run.md   # unmet target: later tiers do not run
- run: python -m harness compare baseline v2 --out compare.json  # fail if tally.loss > 0
```

Keep a reference run for the shipped version under version control and compare every PR against it; the "Regressions in ..." line is the review checklist. Day to day, use the default target mode and read the target / actual / met? table per tier.

## Out of scope

- It scores the final answer to a single question; it does not score multi-turn trajectories or the sequence of intermediate tool calls.
- No red-teaming or adversarial testing; the `policy` scorer only checks forbidden phrases.
- No UI; output is terminal tables, JSON, Markdown and CSV.
- One judge adapter (Anthropic); another model means subclassing `Judge` in `harness/judge.py`. `--judge fake` only exercises the pipeline and is not a real evaluation.
- Case-set governance is a human job: who writes, who reviews, how a definition is pinned. The harness checks and records; it does not decide for you.

## Layout

```
harness/   cases.py scorers.py judge.py rubric.py runner.py compare.py matrix.py
           report.py markdown.py lint.py audit.py badcase.py importer.py cli.py
harness.yaml   optional: targets (per-tier minimum pass rate), gate_mode
agents/    baseline.py v2.py resolvers.py anthropic_agent.py fixtures/market.json
cases/     golden/*.yaml (one file per tool/tier)  backlog/  CHANGELOG.md
judges/    README.md  requirement.v2.md rubric.v2.md dimension.v2.md (v1 kept)   rubrics/  research_answer.yaml
examples/  external_sample.csv convert_convfinqa.py convert_tatqa.py        runs/  saved runs, runs/audits/ (agreement, judge_disputes.jsonl)    tests/  unittest suite
```

## License

MIT - Jing Zeng.
