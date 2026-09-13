# Judge prompts

Every judged check (`requirement`, `llm_judge`, `rubric` dimensions and judged
gates) goes through one of the prompt files in this directory. All of them
state the same three rules, and `harness.judge.RULES` is the single source
of truth (a test checks that the latest version of every prompt contains them).

## The three rules

1. **The case rubric or requirement takes precedence over the general
   instructions in the prompt.** If a case says "accept either date
   format", the judge accepts either, whatever the prompt says about
   formats.
2. **An unmet `requirement` scores 0 for that check.** No partial credit:
   the verdict is yes or no, and a "no" is 0.0.
3. **Richer than the reference is fine.** An answer that contains the
   reference answer and adds correct extra detail is not penalised. Only
   wrong or contradicting extra material costs points.

## Versioning convention

* Files are named `<kind>.v<N>.md`: `requirement` (yes/no), `rubric`
  (0-10), `dimension` (0-5). v3 of `requirement` and `rubric` also asks for a
  failure class (`E1`..`E4`, see `harness.judge.FAILURE_CLASSES`) and states
  that the verdict has no middle band; `${failure_classes}` expands to the list. They are `string.Template` files; `${rules}`
  expands to the numbered rules above, the other placeholders are the
  fields the scorer passes (`${question}`, `${answer}`, ...).
* **Never edit a published version.** Copy `x.vN.md` to `x.v(N+1).md` and
  change the copy; the highest `N` is used automatically and every run
  records which (`judge_version`, and `judge` on each judged check).
* Judge-vs-human agreement (`harness audit --apply`) is tracked per prompt
  version. When agreement drops after a change, or an audit shows a
  systematic misjudgement, revise the prompt (new version) - do not tune
  the agent to the judge.
* v1 files are kept for reproducing old runs; v2 added the three rules; v3
  added the failure class and the no-middle-band wording.
