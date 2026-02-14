# TASK-20260214-175511 - 03 Work

- 标题：Bootstrap runtime (team-os-runtime bring-up)
- 日期：2026-02-15
- 当前状态：work

## 变更摘要（无 secrets）

- OpenHands Agent Server：
  - 拉取镜像 `ghcr.io/openhands/agent-server:latest-python`
  - 启动服务并确认对外仅绑定 `127.0.0.1:${OPENHANDS_AGENT_SERVER_PORT:-18000}`
  - 为 Compose 增加 healthcheck（`GET /alive`）并把 `OH_SECRET_KEY` 作为 `.env` 变量注入
- `.env` secrets：
  - 在 `team-os-runtime/.env` 中生成并写入 `OH_SECRET_KEY` 与通用 `PASSWORD`（不打印、不入 git）
  - 自动生成 `.env.bak.<timestamp>` 备份
- 文档与运行入口：
  - `team-os-runtime/README.md` 修正 OpenHands health endpoint（`/alive`）
  - `team-os-runtime/Makefile` 的 `config` 输出做脱敏，避免 `docker compose config` 展示 secrets
  - `team-os/docs/EXECUTION_RUNBOOK.md` “部署验证”写入已验证摘要（无 secrets）

## 执行记录（命令级证据）

> 仅记录关键命令；完整输出见 `docker compose logs ...`（不落盘 secrets）。

```bash
# 1) 拉取 OpenHands 镜像（必要时可用镜像代理域名作为备选）
docker pull ghcr.io/openhands/agent-server:latest-python

# 2) 启动 OpenHands
cd team-os-runtime
docker compose up -d openhands-agent-server

# 3) 生成/写入 secrets（OH_SECRET_KEY、PASSWORD）到 team-os-runtime/.env（不回显）
#    并生成 .env.bak.<timestamp> 备份

# 4) 应用 compose 变更（healthcheck + env 注入）
cd team-os-runtime
docker compose up -d --force-recreate openhands-agent-server
```

