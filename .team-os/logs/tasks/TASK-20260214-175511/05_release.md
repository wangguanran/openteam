# TASK-20260214-175511 - 05 Release

- 标题：Bootstrap runtime (team-os-runtime bring-up)
- 日期：2026-02-15
- 当前状态：release

## 发布内容

- Runtime：OpenHands 服务补齐启动与健康检查；`.env` 补齐 OH_SECRET_KEY；文档更新与输出脱敏。

## 发布步骤（可重复）

```bash
cd team-os-runtime
make pull
make up
make ps
```

## 回滚/降级策略

- 如果 OpenHands 启动失败：
  - 先 `docker compose logs openhands-agent-server --tail=200`
  - 必要时可临时停掉 OpenHands（不推荐长期）：`docker compose stop openhands-agent-server`
  - Orchestrator 不应依赖 OpenHands 才能存活（健康检查应保持可用）

- 如果需要重置数据（高风险，谨慎）：
  - `docker compose down -v` 会删除卷数据；应在任务日志中明确记录原因、影响与证据

