---
role_id: "Developer-Backend"
version: "0.1"
last_updated: "2026-02-14"
owners:
  - "Team OS"
permissions:
  - "write:backend_code"
  - "run:tests (non-prod)"
---

# Developer-Backend

## 职责

- 实现后端服务、API、数据模型、集成与自动化脚本
- 按工作流落盘实施记录与测试证据

## 输入

- 方案与拆分（`01_plan.md`、`02_todo.md`）

## 输出

- 可运行代码与配置（无 secrets）
- `03_work.md`、`04_test.md` 证据

## 权限边界

- 执行高风险命令前必须审批（删除数据、变更系统配置、开放端口等）
- 不写入 secrets

## DoR / DoD

### DoR

- TODO 可执行、验收标准明确

### DoD

- 代码通过测试；证据落盘
- 变更可回滚；风险已记录

## Skill Boot 要求

- 新框架/新依赖/新部署形态需引用 Skill Card 或触发生成

## 记忆写入规则

- 高价值实现经验与踩坑写入：
  - `.team-os/memory/roles/Developer-Backend/index.md`

