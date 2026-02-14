# 来源摘要: OpenHands Agent Server (Remote Agent Server)

- 日期：2026-02-14
- 链接：
  - https://docs.openhands.dev/sdk/guides/agent-server/docker-sandbox
  - https://docs.openhands.dev/sdk/arch/agent-server
- 获取方式：官方文档
- 适用范围：`team-os-runtime` 的隔离执行平面（OpenHands Agent Server）

## 摘要

OpenHands 提供 Remote Agent Server 作为远程执行后端（HTTP/WebSocket），用于隔离执行命令与文件操作，并可管理 Docker 等 sandbox/workspace。文档给出了官方预构建镜像示例（`ghcr.io/openhands/agent-server:latest-python`）以及典型部署端口 `8000`，并强调在 Docker 模式下通常需要挂载 Docker socket 来管理 workspace 容器（高风险，需要最小化与审批）。

## 可验证事实 (Facts)

- 官方 Docker sandbox 示例中使用的镜像：`ghcr.io/openhands/agent-server:latest-python`（用于 `DockerWorkspace`）。见 docker-sandbox 文档中的示例代码。
- Agent Server 架构文档给出本地运行示例：`openhands-agent-server --port 8000`，以及 Docker 运行示例（映射 `8000:8000` 并挂载 `/var/run/docker.sock`）。
- Agent Server 提供典型 HTTP API：
  - `/workspaces`（create/get/delete/execute）
  - `/conversations`（create/get/messages/stream）
  - `/health`（健康检查）
  - `/metrics`（Prometheus metrics）

## 关键参数/端口/环境变量

- 端口：
  - `8000/tcp`：Agent Server HTTP（文档示例）
- 风险点：
  - Docker 运行示例包含 `-v /var/run/docker.sock:/var/run/docker.sock`（Agent Server 可控制宿主 Docker，属于高风险能力）

## 风险与注意事项

- 如果将 Agent Server 端口暴露到外网，必须启用鉴权/ACL，并纳入审批闸门。
- 挂载 Docker socket 等同于将宿主 Docker 控制权授予容器；建议：
  - 仅在本机/内网使用
  - 不暴露公网端口
  - 限制可用镜像/网络（后续再加固）

