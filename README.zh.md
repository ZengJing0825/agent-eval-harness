# agent-eval-harness

一个轻量的 LLM Agent 评测框架。只依赖标准库 + PyYAML,默认 demo 完全离线。

四个核心思路:

1. **动态黄金集** - 用例以带版本号的 YAML 存放,可以在 PR 里 review、diff,并随时间增长。
2. **工具级客观测试** - 每条用例标注它考察的工具/能力,尽量用确定性打分器(exact / contains / regex / 数值容差 / JSON 路径 / 禁用短语 / 引用存在),失败是事实而不是观点。
3. **同行对比** - 两个 agent 版本跑同一批用例,输出逐用例的 win / loss / tie 与分工具的差异,直接列出回归项。
4. **坏例回流** - 线上失败样本一条命令进入 backlog,再一条命令晋升为黄金用例,形成闭环。

## 快速开始

```bash
pip install pyyaml
python -m harness run --agent baseline
python -m harness run --agent v2
python -m harness compare baseline v2
```

自带的 demo 是一个小型金融助手任务集(ticker 解析、财报日期查询、涨跌幅计算、拒绝买卖建议的合规检查、引用存在)。`v2` 在四个工具上优于 `baseline`,却在合规检查上悄悄回归 - 这正是评测存在的意义。

## 接入你自己的 agent

一个模块、一个函数:

```python
def answer(prompt: str, context: dict) -> dict:
    return {"answer": "...", "citations": ["..."]}
```

`python -m harness run --agent your_module` 即可。可选的 `agents/anthropic_agent.py` 会把同一批 prompt 发给 Claude(需要 `anthropic` SDK 和 `ANTHROPIC_API_KEY`,缺失时自动跳过)。

更多细节(用例格式、打分器参数、CI 接入)见英文版 [README.md](README.md)。

MIT 许可 - Jing Zeng。
