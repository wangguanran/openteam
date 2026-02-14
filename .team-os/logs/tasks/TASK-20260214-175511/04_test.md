# TASK-20260214-175511 - 04 Test

- 标题：Bootstrap runtime (team-os-runtime bring-up)
- 日期：2026-02-15
- 当前状态：test

## 服务状态

```bash
cd team-os-runtime
docker compose ps
```

验收点：

- `postgres/temporal/orchestrator/openhands-agent-server` 为 `Up` 且关键服务为 `healthy`
- `temporal-admin-tools`、`temporal-create-namespace` 为一次性任务，期望 `Exited (0)`

## 健康检查

Orchestrator：

```bash
curl -fsS http://127.0.0.1:18080/healthz
```

期望：JSON 且 `status=ok`，并能列出挂载的 Team OS `roles/` 与 `workflows/` 文件列表。

OpenHands Agent Server：

```bash
curl -fsS http://127.0.0.1:18000/alive
```

期望：`{"status":"ok"}`

Temporal UI：

```bash
curl -fsS http://127.0.0.1:18081 >/dev/null
```

期望：返回 HTML（UI 可通过浏览器打开）。

## 安全检查（最小）

- `.env` 不入 git：本任务仅在 `team-os-runtime/.gitignore` 中允许 `.env.example`，并忽略真实 `.env`
- 端口默认仅绑定 `127.0.0.1`（不打开公网）
- OpenHands 挂载 docker socket 属高风险能力：仅本机使用，禁止对外暴露

