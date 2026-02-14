# TASK-20260214-175511 - 06 Observe

- 标题：Bootstrap runtime (team-os-runtime bring-up)
- 日期：2026-02-15
- 当前状态：observe

## 观测点（最小可用）

- 进程与健康状态：

```bash
cd team-os-runtime
docker compose ps
```

- 日志：

```bash
cd team-os-runtime
docker compose logs -f --tail=200
```

- 关键探活：

```bash
curl -fsS http://127.0.0.1:18080/healthz
curl -fsS http://127.0.0.1:18000/alive
```

## 观察结论

- 服务均能启动并保持健康状态（见 `docker compose ps`）
- 探活端点可用；端口均绑定 `127.0.0.1`（未对公网暴露）

