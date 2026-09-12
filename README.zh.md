# agent-eval-harness

一个小型 LLM Agent 评测框架。只依赖标准库 + PyYAML,自带 demo 完全离线,一秒跑完。

0.2 版把它从"跑分工具"变成一套评测方法。五条规则,每条对应一个命令:

1. **先客观,后开放。** 用例分层(`unit` -> `complex` -> `external` -> `dynamic`),前一层设门槛(gate),不过就不跑后面。算术都错的 agent 不值得花 judge 预算。
2. **每个答案两个人写。** 每条用例记录 `owner` 和 `peer` 两份独立写出的答案;`harness lint` 不允许两者不一致却标成 `agreed`。
3. **Judge 必须校准。** Judge 提示词是带版本号的文件;每次判决都存 reason;`harness audit` 抽样让人工打标,按 judge 版本统计人机一致率。
4. **版本要成矩阵。** 每次运行记录用例集 x agent x judge 三个版本;`compare` 在版本不一致时大声警告;`harness matrix` 输出 agent x 层级 x 工具的表。
5. **坏例要分类。** `data` / `tool_choice` / `ambiguity` / `reasoning` / `judge`;`ambiguity` 类只能带 `--rewrite` 晋升——该修的是问题,不是 agent。

demo 是一个虚构的金融助手(ticker 解析、财报日期、涨跌幅计算、"不给买卖建议"合规、引用),两个离线规则 agent。`v2` 几乎处处优于 `baseline`,却在合规上悄悄回归——这正是评测存在的意义。所有数据均为合成。

## 快速开始(离线,一分钟内)

```bash
pip install pyyaml
python -m harness lint                               # 用例集体检:peer 答案、状态、容差、id
python -m harness run --agent baseline --gate unit:0.9 --judge fake
python -m harness run --agent v2       --gate unit:0.9 --judge fake
python -m harness compare baseline v2 --md compare.md
python -m harness matrix --agents baseline,v2 --reuse -v
python -m harness audit runs/v2-<ts>.json            # 生成人工打标表
python -m harness run --agent baseline --tier dynamic --as-of 2027-01-05
python -m unittest discover -s tests                 # 92 个标准库测试
```

`--judge fake` 是一个确定性的离线替身,只用来走通 judge 链路(提示词文件、reason、审计表),按词重叠打分,不能当作真实评测。去掉这个参数(或安装 `anthropic` 并设置 `ANTHROPIC_API_KEY` 后用 `--judge anthropic`)才是真实 judge。没有 judge 时,judge 类检查记为 *skipped*,绝不算 failed。

输出示例:

```
$ python -m harness run --agent baseline --gate unit:0.9 --judge fake
tier      tool               n   pass    avg   skip
unit      policy             4   100.0%  1.00  0
unit      ticker_resolution  4    50.0%  0.50  0
unit      (all)              22   59.1%  0.62  0
complex   (all)              6     n/a    n/a  6
Gates:
  gate unit:0.9 -> 59.1%  FAIL
  tier complex skipped: gate unit:0.9 failed (pass rate 59.1%)
Judge coverage: 2 judged, 20 deterministic, 12 skipped
```

```
$ python -m harness matrix --agents baseline,v2 --reuse
agent     version  unit        complex     external    dynamic      ALL
baseline  1.0      59% / 0.62  n/a         n/a         n/a          59% / 0.62
v2        2.0      91% / 0.94  80% / 0.72  75% / 0.75  100% / 1.00  88% / 0.89
v2 vs baseline: wins 8, losses 1, ties 13
  regressions: policy-001
```

`policy-001` 就是重点:v2 在拒绝里泄漏了 "strong buy"。总通过率看不出来,分工具表和回归列表看得见。

## 为什么这样做

**先客观后开放(分层 + gate)。** judge 打分贵、噪声大、容易争论;确定性的 unit 用例免费且无歧义。按层顺序跑并设门槛(`--gate unit:0.9`),坏 agent 在事实层就被拦下,被跳过的层带原因记录在案(不是悄悄消失),judge 预算只花在值得的 agent 上。

**每个答案两个人写(owner/peer + lint)。** review 里发现的"agent 错误",很多其实是期望值写错了。两个人独立写答案、记录计算过程和来源,用例集本身才可审。`lint` 把流程变成 error(disputed、agreed 但 owner != peer、id 重复、未知 scorer)和 warning(缺 peer、容差相对量级小得离谱)。`run` 默认跳过 `draft`/`disputed`,`--include-unagreed` 可强制包含。

**Judge 必须校准(版本化提示词 + audit)。** judge = 模型 + 提示词,提示词一改数字就动。提示词放在 `judges/<name>.v<N>.md`,版本号和 judge 给出的 reason 一起写进每次运行、每条检查结果。`audit` 把*全部* judge 判失败的用例加上随机抽样的判通过用例导出 CSV,人工打标后 `audit --apply` 按 judge 版本报告一致率、误判通过/误判失败数和分歧清单。一致率低于九成左右,该修的是 judge 不是 agent。

**版本要成矩阵(用例集 x agent x judge)。** "v2 88%" 不说明用哪套用例、哪个 judge 就没有意义。每次运行记录 `set_version`(全部用例文件的内容哈希)、`agent_version`(模块的 `VERSION`)、`judge_version`、`harness_version`。`compare` 在版本不一致时警告并列出未匹配用例;`matrix` 把多个 agent 放在同一套用例上并列,并列出每个 agent 相对第一个的回归。

**坏例必须分类(歧义就改问题)。** 线上失败只写"答错了"没法行动。类别说明谁来修:`data`(数据源)、`tool_choice`(路由)、`reasoning`(模型/提示词)、`judge`(评分器)、`ambiguity`(问题本身)。歧义最常见,错误的修法是调 agent 直到它猜中题意;`promote --rewrite` 把澄清后的 prompt 放进用例集并保留原句。每次晋升在 `cases/CHANGELOG.md` 追加一行,记录前后的用例集版本。

## 实战笔记

把这套方法用在一个真实的金融问答 agent 上一年之后,留下六条经验。它们解释了上面每条规则为什么长成这样。

1. **评审里发现的"agent 错误",一大半是期望值写错或题目口径没写死。** 先修题集,再修 agent。这就是 owner/peer 双写和 `lint` 存在的原因。
2. **数据层错误会伪装成模型错误。** 接口给错数、调错工具、历史覆盖不够,表现出来都像"模型答错"。工具级客观题必须单独成一层跑,否则永远在怪模型。
3. **换版本后失败暴增,先查口径,再查退化。** 收盘价还是盘中价、TTM 还是某季度年化,题干没写死,agent 换一种理解就被判错。这是 `ambiguity` 类坏例要改题不改 agent 的来历。
4. **容差必须人定。** AI 生成的 rubric 只能借格式;百万量级的数字给容差 1 这种事它会干。`lint` 对容差量级的警告就是为此。
5. **judge 预算只花在过了事实层的 agent 上;判 0 的逐条看,判 1 的抽样看。** 这是 gate 和 `audit` 抽样规则的来源。
6. **"没数据"和"瞎编"是两种失败,评测要能分开。** 前者修数据源和拒答策略,后者修 prompt 和引用要求。`citation`、`policy` 打分器和 `data` / `reasoning` 两个坏例类别把它们拆开。

## 命令

| 命令 | 作用 |
|---|---|
| `run --agent A [--tier unit,complex] [--tool T] [--gate unit:0.9] [--judge auto\|anthropic\|fake\|none] [--as-of 日期] [--include-unagreed] [--md out.md]` | 按层运行一个 agent,保存 `runs/A-<ts>.json` |
| `compare A B [--out cmp.json] [--md cmp.md]` | 分层/分工具并列、逐用例 win/loss/tie、回归列表、版本警告 |
| `report run.json [-v] [--md out.md]` | 重新打印已保存的运行 |
| `matrix --agents A,B[,C] [--reuse] [-v] [--out m.json\|m.md]` | agent x 层级(x 工具),相对第一个 agent 的回归 |
| `lint [--cases dir]` | 用例集静态检查;仅 error 时退出码非零 |
| `audit run.json [--sample all\|random:N] [--out sheet.csv]` / `audit --apply sheet.csv` | judge 审计表 / 人机一致率(存到 `runs/audits/`) |
| `badcase add --category C ...` / `badcase list` / `badcase promote ID [--rewrite "..."] [--peer "..."]` | 采集、按类别列出、晋升进用例集并写 changelog |
| `import --csv f.csv\|--jsonl f.jsonl --map "prompt=question,expected=answer,tool=category" --source S --license L` | 外部基准 -> 带来源/许可的 `external` 层用例文件 |

## 用例格式

`cases/golden/` 下每个工具一个 YAML。短格式(`scorer` + 参数)或长格式(`checks`,分数取均值,全部通过才算通过)。

```yaml
version: 3                       # 改动用例集时递增;也参与 set_version
tool: earnings_date
tier: unit                       # unit | complex | external | dynamic(文件默认,单条可覆盖)
source: "sample-bench"           # 仅外部集:每次运行都会记录
license: "CC0-1.0"
cases:
  - id: earn-003                 # 全局唯一
    prompt: "When is Nvidia's next earnings report?"
    context: {}                  # 可选,原样传给 agent
    tags: [company-name]
    scorer: contains             # 短格式……
    expected: "2026-11-18"
    answer:                      # 两个人,一个期望值
      owner: "2026-11-18"        # 用例负责人写的
      peer: "2026-11-18"         # 同事独立写的
      calculation: null          # 推导过程(如 "(125-100)/100*100 = 25")
      source: "fixture:agents/fixtures/market.json"
      status: agreed             # draft | agreed | disputed —— run 默认跳过 draft/disputed
  - id: earn-006
    prompt: "Cite your source: when does AMZN report?"
    checks:                      # ……或长格式
      - {type: contains, expected: "2026-10-29"}
      - {type: citation, min: 1}
  - id: dyn-001                  # dynamic 层:期望值随日期移动
    tier: dynamic
    prompt: "As of {as_of}, when is Apple's next earnings report?"
    resolver: agents.resolvers:earnings_date     # fn(as_of, **resolver_args) -> 占位符字典
    resolver_args: {ticker: AAPL}
    scorer: contains
    expected: "{next_earnings}"
```

没有 `answer` 块的用例视为 `agreed`(lint 会警告缺 peer)。`{today}` / `{as_of}` 可用于 prompt、context 和检查参数;`run --as-of` 默认今天,dynamic 层用例的 context 会自动注入 `as_of`。

## 打分器

确定性打分器让"失败"成为事实。答案作者填写的五个*校验字段*对应表中标注字段名的打分器;一条检查只放一个要求。

| type | 字段 | 参数 | 通过条件 |
|---|---|---|---|
| `exact` | | `expected`, `case_sensitive` | 整段回答等于期望(空白/大小写归一) |
| `contains` | | `expected`(字符串或列表), `case_sensitive` | 每个期望子串都出现 |
| `regex` | | `pattern`, `ignore_case` | `re.search` 命中 |
| `numeric` | | `expected`, `tolerance`, `relative` | 回答中某个数在容差内 |
| `json_key` | | `path`, `expected` | `data`(或 JSON 回答)中路径值相等 |
| `policy` | | `forbidden`(列表) | 没有出现任何禁用短语 |
| `citation` | | `min` | 至少 `min` 条非空引用 |
| `correctness` | correctness | `expected` | 字符串走 `exact`,数字走零容差 `numeric` |
| `range` | range | `lo`, `hi`, `field?` | 回答中某个数(或 `data[field]`)落在 `[lo, hi]` |
| `keyword` | keyword | `points`(列表), `min_hit?` | 分数 = 命中数 / 总数;命中 >= `min_hit`(默认全部)则通过 |
| `requirement` | requirement | `text`, `calculation?`, `must_contain_any?` | judge 对这一条要求说 *yes*;无 judge 时由 `must_contain_any` 兜底,否则跳过 |
| `tolerance` | tolerance | `expected`, `abs` \| `rel` | 显式数值容差(默认 `abs: 0`) |
| `rubric` | | `name`, `min_grade?` | `rubrics/<name>.yaml` 的门槛优先加权评分(见下) |
| `llm_judge` | | `rubric`(文本), `threshold` | 自由文本评分 0-10 归一后 >= 阈值;无 judge 则跳过 |

新增打分器:在 `harness/scorers.py` 写一个 `(answer: dict, check: dict) -> Score` 并注册到 `SCORERS`。`Score` 含 `score`、`passed`、`detail` 和一个随运行保存的 `extra` 字典(judge reason 与版本、关键词命中、rubric 维度分……)。

### 门槛优先的加权 rubric

`rubrics/research_answer.yaml` 是一个通用示例:

```yaml
gates:                                   # 先查;命中即封顶
  - {id: no_advice,  cap: F, forbidden: ["you should buy", "strong buy"]}   # 确定性
  - {id: has_source, cap: C, min_citations: 1}                              # 确定性
  - {id: on_topic,   cap: F, judge: true, description: "Addresses the question asked."}
dimensions:                              # 权重合计 100,每维 judge 打 0-5 再缩放
  - {id: accuracy,     weight: 40, description: "..."}
  - {id: completeness, weight: 30, description: "..."}
  - {id: reasoning,    weight: 20, description: "..."}
  - {id: clarity,      weight: 10, description: "..."}
bands: {A: 90, B: 75, C: 60, D: 40}      # 各档下限;低于 D 为 F
pass_band: C
```

确定性门槛(`forbidden` / `required_any` / `required_all` / `min_citations`)完全不需要 judge,所以离线也能因禁用短语判失败。维度逐个评分,每次调用都告诉 judge 前面维度已扣过的缺陷(**单次归因规则**:一个缺陷只在一个维度扣分)。结果保存各维度分、命中的门槛、总分和等级。

### Judge

提示词是 `string.Template` 格式的 markdown:`judges/requirement.v1.md`(是/否)、`judges/rubric.v1.md`(0-10)、`judges/dimension.v1.md`(0-5)。加一个 `requirement.v2.md` 就会用最高版本,运行记录里写明用了哪个。后端:`--judge auto`(有 `ANTHROPIC_API_KEY` 时用 Anthropic 适配器,否则无)、`anthropic`、`fake`、`none`;也可 `HARNESS_JUDGE=fake`。

## 坏例回流

```bash
python -m harness badcase add --agent v2 --category ambiguity \
    --prompt "When does Meta report?" --expected "2026-10-28"
python -m harness badcase list                       # 按类别分组
python -m harness badcase promote bc-20260912-d0bb \
    --rewrite "When does Meta Platforms (META) next report earnings?" --peer "2026-10-28"
tail -1 cases/CHANGELOG.md
```

晋升的用例带 `answer` 块;不给 `--peer` 则为 `draft`,补上 peer 答案前 `run` 会跳过它。

## 外部基准

```bash
python -m harness import --csv examples/external_sample.csv \
    --map "prompt=question,expected=answer,tool=category" \
    --source "sample-bench" --license "CC0-1.0" --out cases/golden/external_sample-bench.yaml
```

文件进入 `external` 层,`source` / `license` 记在文件级;运行结果记录在 `set_provenance`,报告单独列出外部集。外部答案是基准自带的,lint 不再要求 peer。

## 接入你自己的 agent

```python
VERSION = "1.4.0"                       # 记为 agent_version

def answer(prompt: str, context: dict) -> dict:
    return {"answer": "The ticker symbol is AAPL.", "citations": ["https://..."], "data": {"ticker": "AAPL"}}
```

`context` 是用例的 `context:`(dynamic 层还会带 `as_of`)。异常会变成带 traceback 的失败用例。可按名字(`agents/my_agent.py`)、模块路径或文件路径运行。`agents/anthropic_agent.py` 是可选的 Claude 适配器。

## CI 提示

```yaml
- run: pip install pyyaml
- run: python -m harness lint                                   # disputed / 答案不一致 -> 失败
- run: python -m unittest discover -s tests
- run: python -m harness run --agent v2 --gate unit:0.9 --md run.md
- run: python -m harness compare baseline v2 --out compare.json  # tally.loss > 0 则失败
```

把已发布版本的参考运行纳入版本控制,每个 PR 都和它比;"Regressions in ..." 那一行就是 review 清单。

## 目录

```
harness/   cases.py scorers.py judge.py rubric.py runner.py compare.py matrix.py
           report.py markdown.py lint.py audit.py badcase.py importer.py cli.py
agents/    baseline.py v2.py resolvers.py anthropic_agent.py fixtures/market.json
cases/     golden/*.yaml(每个工具/层一个文件)  backlog/  CHANGELOG.md
judges/    requirement.v1.md rubric.v1.md dimension.v1.md      rubrics/  research_answer.yaml
examples/  external_sample.csv        runs/  运行结果, runs/audits/    tests/  unittest 套件
```

MIT 许可 - Jing Zeng。
