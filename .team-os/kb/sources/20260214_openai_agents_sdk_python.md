# 来源摘要: OpenAI Agents SDK (Python) 安装与最小示例

- 日期：2026-02-14
- 链接：
  - https://developers.openai.com/api/docs/guides/agents-sdk
  - https://github.com/openai/openai-agents-python
- 获取方式：官方 OpenAI 文档 + 官方 GitHub 仓库
- 适用范围：`team-os-runtime` orchestrator（控制平面）最小可运行骨架

## 摘要

OpenAI Agents SDK Python 的官方仓库为 `openai/openai-agents-python`，安装包名为 `openai-agents`（Python 3.10+）。官方 README 给出最小示例：`from agents import Agent, Runner`，并提示运行时需设置 `OPENAI_API_KEY` 环境变量。

## 可验证事实 (Facts)

- 安装：
  - `pip install openai-agents`（仓库 README “Get started” 段落）
- 运行时要求：
  - Python 3.10 或更高（仓库 README）
  - 需要 `OPENAI_API_KEY` 环境变量（仓库 README）
- 最小代码（概念）：
  - `from agents import Agent, Runner`
  - `Runner.run_sync(agent, "...")`

## 风险与注意事项

- `OPENAI_API_KEY` 属于 secrets，必须只存在于环境变量或本地 `.env`（不入库）。
- 任何调用模型会产生费用与数据流出，需要在任务台账中记录与可追溯（inputs/outputs/参数/版本）。

