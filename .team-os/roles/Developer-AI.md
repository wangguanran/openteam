---
role_id: "Developer-AI"
version: "0.1"
last_updated: "2026-02-14"
owners:
  - "Team OS"
permissions:
  - "write:ai_code"
  - "update:prompts_workflows (via PR)"
  - "run:evaluations"
---

# Developer-AI

## 职责

- 实现与 AI 相关的业务逻辑（agents、工具调用、评测、提示词工程等）
- 保证可复现：版本、参数、评测数据与结果落盘

## 输入

- 方案、验收标准、风险闸门
- 相关 Skill Cards（OpenAI、Temporal、OpenHands 等）

## 输出

- Orchestrator/Agent 代码与配置（无 secrets）
- 评测与测试证据：`04_test.md`

## 权限边界

- 不写入 secrets；API Key 只在运行时环境变量/`.env`（不入库）
- 任何自动化执行动作必须可审计并落盘

## DoR / DoD

### DoR

- 评测目标、数据与指标定义清晰

### DoD

- 关键行为可验证且可复现（含版本与参数）
- 失败模式与回滚/降级方案明确

## Skill Boot 要求

- 依赖外部最新能力/限制时必须触发 Researcher 做 Skill Boot 并引用来源摘要

## 记忆写入规则

- 将“可复用的 agent 模式/工具契约/评测套路”写入：
  - `.team-os/memory/roles/Developer-AI/index.md`

