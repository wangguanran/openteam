# Skill Card: OpenHands Agent Server (Docker) 最小部署与风险闸门

- 日期：2026-02-14
- 适用角色/平台：Release-Ops / Execution Plane

## TL;DR

- 官方预构建镜像示例：`ghcr.io/openhands/agent-server:latest-python`。
- 文档示例端口：`8000`；健康检查：`GET /health`；metrics：`GET /metrics`。
- 典型 Docker 模式需要挂载 `/var/run/docker.sock`（高风险，需审批与最小化）。

## 触发条件 (When To Use)

- 需要隔离执行构建/测试/脚本，将“执行能力”从控制平面剥离到 sandbox。

## 操作步骤 (Do)

1. 在 `team-os-runtime/docker-compose.yml` 添加 `openhands-agent-server` 服务：
   - 镜像：`ghcr.io/openhands/agent-server:latest-python`
   - 内网端口：`8000`
   - 按需挂载 docker socket（见安全注意事项）
2. Orchestrator 通过 compose 内网访问 `http://openhands-agent-server:8000`。

## 校验 (Verify)

- `curl -fsS http://127.0.0.1:<mapped_port>/health`（若映射到宿主）
- 或在 compose 网络内访问：`curl -fsS http://openhands-agent-server:8000/health`

## 常见坑 (Pitfalls)

- 没挂载 docker socket 时，Agent Server 可能无法创建/管理 workspace 容器（取决于你的使用方式）。
- 把 `8000` 端口暴露到公网会带来高风险。

## 安全注意事项 (Safety)

- `-v /var/run/docker.sock:/var/run/docker.sock` 近似 root 级权限能力，必须审批并在 `docs/SECURITY.md` 中记录风险。
- 默认仅绑定 `127.0.0.1` 或不对宿主暴露端口。

## 参考来源 (Sources)

- `.team-os/kb/sources/20260214_openhands_agent_server.md`

