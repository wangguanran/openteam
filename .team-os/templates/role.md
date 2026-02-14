---
role_id: "<ROLE_ID>"
version: "0.1"
last_updated: "<YYYY-MM-DD>"
owners:
  - "<name_or_team>"
permissions:
  - "read:repo"
  - "write:repo (when approved if risky)"
---

# <ROLE_ID>

## 职责 (Responsibilities)

- ...

## 输入 (Inputs)

- 任务台账：`.team-os/ledger/tasks/<TASK_ID>.yaml`
- 任务日志：`.team-os/logs/tasks/<TASK_ID>/*`
- 相关工作流：`.team-os/workflows/*.yaml`

## 输出 (Outputs)

- 明确产物文件路径（必须落盘）
- 关键决策与证据链接（写入日志与台账）

## 权限边界 (Permissions)

- 默认不执行高风险动作；需要审批时必须先停下并请求批准
- 禁止写入 secrets；只允许写 `.env.example`

## 产物清单 (Artifacts)

- ...

## DoR / DoD

### DoR

- 输入已完整且可执行
- 风险等级明确；R2/R3 已规划审批

### DoD

- 输出产物落盘且可追溯
- 关键命令、测试与结果已记录
- 需要时完成 Retro 并提出自我升级点

## Skill Boot 要求

何时必须做 Skill Boot：

- 新平台/新子系统/高不确定信息（镜像名、端口、环境变量、最新行为）

Skill Boot 必产物：

- 来源摘要：`.team-os/kb/sources/`
- Skill Card：`.team-os/kb/roles/<ROLE_ID>/skill_cards/` 或 `.team-os/kb/platforms/<Platform>/skill_cards/`
- 角色记忆索引：`.team-os/memory/roles/<ROLE_ID>/index.md`

## 记忆写入规则

- 任何可复用的经验/坑/决策模板，必须写入：
  - `.team-os/memory/roles/<ROLE_ID>/index.md`
  - 并链接到对应 Skill Card 与来源摘要

