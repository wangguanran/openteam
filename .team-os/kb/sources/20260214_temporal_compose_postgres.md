# 来源摘要: Temporal 官方 Compose (Postgres) 示例

- 日期：2026-02-14
- 链接：
  - https://raw.githubusercontent.com/temporalio/samples-server/main/compose/docker-compose-postgres.yml
  - https://raw.githubusercontent.com/temporalio/samples-server/main/compose/.env
  - https://raw.githubusercontent.com/temporalio/samples-server/main/compose/scripts/setup-postgres.sh
  - https://raw.githubusercontent.com/temporalio/samples-server/main/compose/scripts/create-namespace.sh
- 获取方式：官方 GitHub 仓库（raw 文件）
- 适用范围：`team-os-runtime` 的 Temporal（自托管）最小可用部署

## 摘要

Temporal 官方在 `temporalio/samples-server` 仓库提供了 docker compose 示例（Postgres 版以及带 ES 版）。Postgres 版 compose 通过 `temporalio/admin-tools` 在启动时执行脚本创建/更新 Temporal 数据库与 visibility 数据库 schema，并启动 `temporalio/server` 与 `temporalio/ui`，默认暴露 gRPC Frontend 端口 `7233` 与 UI 端口 `8080`。

## 可验证事实 (Facts)

- 示例 compose 文件：`docker-compose-postgres.yml`。其中服务包括：
  - `postgresql`（Postgres 容器）
  - `temporal-admin-tools`（执行 `setup-postgres.sh` 创建/更新 schema）
  - `temporal`（`temporalio/server:${TEMPORAL_VERSION}`）
  - `temporal-create-namespace`（执行 `create-namespace.sh`，默认 namespace `default`）
  - `temporal-ui`（`temporalio/ui:${TEMPORAL_UI_VERSION}`）
- `.env` 文件提供了推荐版本号：
  - `TEMPORAL_VERSION=1.29.1`
  - `TEMPORAL_ADMINTOOLS_VERSION=1.29.1-tctl-1.18.4-cli-1.5.0`
  - `TEMPORAL_UI_VERSION=2.34.0`
  - `POSTGRESQL_VERSION=16`
- `temporal` 服务对外暴露端口 `7233`（Temporal gRPC Frontend）。
- `temporal-ui` 服务对外暴露端口 `8080`（Web UI）。

## 关键参数/端口/环境变量

- 端口：
  - `7233/tcp`：Temporal Frontend gRPC
  - `8080/tcp`：Temporal UI
  - `5432/tcp`：Postgres（容器内）
- Temporal Server（compose 示例中）关键环境变量：
  - `DB=postgres12`
  - `DB_PORT=5432`
  - `POSTGRES_USER=temporal`
  - `POSTGRES_PWD=temporal`
  - `POSTGRES_SEEDS=postgresql`（指向 compose 服务名）
  - `BIND_ON_IP=0.0.0.0`
  - `DYNAMIC_CONFIG_FILE_PATH=config/dynamicconfig/development-sql.yaml`
- Admin tools 脚本（`setup-postgres.sh`）使用 `temporal-sql-tool` 对 `temporal` 与 `temporal_visibility` 两个 DB 执行 `create/setup-schema/update-schema`。
- `create-namespace.sh` 使用 `temporal operator namespace describe/create` 创建默认 namespace（默认 `default`）。

## 风险与注意事项

- 官方示例属于“本地/示例”级别配置：用户名/密码、端口暴露、动态配置文件均需要根据环境加固。
- 对外网暴露 `7233/8080` 属于高风险动作，应仅绑定 `127.0.0.1` 或置于内网，并加 ACL/反代鉴权（在 Team OS 中属于审批项）。

