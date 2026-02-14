# Skill Card: OpenAI Agents SDK (Python) 最小可运行骨架

- 日期：2026-02-14
- 适用角色/平台：Developer-AI / Orchestrator

## TL;DR

- Python 包：`openai-agents`
- 导入：`from agents import Agent, Runner`
- 运行前设置：`OPENAI_API_KEY`（secrets，不入库）

## 触发条件 (When To Use)

- 需要实现 Orchestrator（控制平面），驱动多角色与工具调用，并要求可审计 trace。

## 操作步骤 (Do)

1. 在 orchestrator 的 `requirements.txt` 添加：
   - `openai-agents`
2. 使用最小示例验证安装（不要在启动时自动调用模型）：
   - 仅在显式触发的 endpoint/命令中执行 `Runner.run_sync(...)`
3. 运行时通过环境变量注入 `OPENAI_API_KEY`（本地 `.env`，不入库）。

## 校验 (Verify)

- 容器启动时能成功 import `agents`
- `GET /healthz` 正常返回

## 常见坑 (Pitfalls)

- Python 版本低于 3.10
- 未设置 `OPENAI_API_KEY`

## 安全注意事项 (Safety)

- 任何模型调用会产生费用；需要在任务日志中记录模型/参数/输入输出与证据路径。

## 参考来源 (Sources)

- `.team-os/kb/sources/20260214_openai_agents_sdk_python.md`

