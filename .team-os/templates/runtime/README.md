# team-os-runtime

单机运行时环境（Docker Compose），用于 24/7 跑：

- Orchestrator（Python + OpenAI Agents SDK）
- OpenHands Agent Server（隔离执行平面）
- Temporal + UI（durable workflow）
- Postgres（Temporal DB + 运行时元数据预留）

## 安全提示 (先读)

- 本目录仅提交 `.env.example`，真实 `.env` 不得入库。
- `openhands-agent-server` 默认挂载 Docker socket（高风险能力）。不要暴露到公网；任何公网暴露属于审批项。

## 启动/停止

```bash
cd team-os-runtime
cp .env.example .env
# 编辑 .env，至少填写 POSTGRES_PASSWORD，按需填写 OPENAI_API_KEY
make up
make ps
```

停止：

```bash
cd team-os-runtime
make down
```

## 日志与健康检查

```bash
cd team-os-runtime
make logs
```

Orchestrator health：

```bash
curl -fsS http://127.0.0.1:${ORCHESTRATOR_PORT:-18080}/healthz
```

Temporal UI：

```bash
open http://127.0.0.1:${TEMPORAL_UI_PORT:-18081}
```

OpenHands Agent Server health（若暴露到宿主）：

```bash
curl -fsS http://127.0.0.1:${OPENHANDS_AGENT_SERVER_PORT:-18000}/alive
```

## Makefile

- `make up` / `make down`
- `make pull`
- `make ps`
- `make logs`
- `make doctor`
