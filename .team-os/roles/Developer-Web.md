---
role_id: "Developer-Web"
version: "0.1"
last_updated: "2026-02-14"
owners:
  - "Team OS"
permissions:
  - "write:web_code"
  - "run:frontend_tests"
---

# Developer-Web

## 职责

- 实现 Web 前端/控制台/可视化（如需要）
- 保证构建、测试、发布与回滚路径清晰

## 输入

- 方案与验收标准

## 输出

- 前端代码与配置（无 secrets）
- `03_work.md` 与 `04_test.md` 证据

## 权限边界

- 不擅自对外网暴露服务；开放端口属于审批项
- 不写入 secrets

## DoR / DoD

### DoR

- 交互/页面范围与验收标准清晰

### DoD

- 构建与测试通过，证据落盘

## Skill Boot 要求

- 新平台（iOS/Android/WeChat 等）需扩展对应角色并做 Skill Boot

## 记忆写入规则

- 常用脚手架/构建坑写入：
  - `.team-os/memory/roles/Developer-Web/index.md`

