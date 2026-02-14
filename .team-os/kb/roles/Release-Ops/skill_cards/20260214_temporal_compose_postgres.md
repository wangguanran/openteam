# Skill Card: Temporal 官方 Compose (Postgres) 最小可用部署

- 日期：2026-02-14
- 适用角色/平台：Release-Ops / Runtime

## TL;DR

- 官方 compose 示例来自 `temporalio/samples-server/compose`。
- Postgres 版最小集合：Postgres + admin-tools（建库建表） + temporal server + temporal-ui + create-namespace。
- 关键端口：`7233`（Temporal gRPC），`8080`（UI）。

## 触发条件 (When To Use)

- 需要在单机通过 Docker Compose 自托管 Temporal（用于 durable workflow、失败重试、状态机持久化）。

## 操作步骤 (Do)

1. 采用官方 compose 的服务拓扑（见来源摘要），在本项目 `team-os-runtime/docker-compose.yml` 复刻：
   - Postgres 容器
   - `temporalio/admin-tools` 执行 `setup-postgres.sh` 初始化 schema
   - `temporalio/server` 启动 Temporal
   - `temporalio/ui` 提供 Web UI
   - `temporalio/admin-tools` 执行 `create-namespace.sh`（默认 namespace）
2. 建议在宿主机侧将对外端口限制到 `127.0.0.1`（避免公网暴露）。
3. 将 Postgres 密码与 OpenAI key 放入本地 `.env`（不入库），只提交 `.env.example`。

## 校验 (Verify)

- `docker compose ps` 显示 temporal 与 temporal-ui 为 `running`
- UI 可访问：`http://127.0.0.1:8080`
- gRPC 可用：容器内 healthcheck `nc -z localhost 7233` 通过

## 常见坑 (Pitfalls)

- 端口冲突：本机已有 `5432/8080/7233` 占用
- 未等待 schema 初始化完成导致 temporal 启动失败（需确保 admin-tools setup 成功）

## 安全注意事项 (Safety)

- 打开公网端口属于审批项（R2/R3）。
- 不要把 Postgres 密码写入 git。

## 参考来源 (Sources)

- `.team-os/kb/sources/20260214_temporal_compose_postgres.md`

