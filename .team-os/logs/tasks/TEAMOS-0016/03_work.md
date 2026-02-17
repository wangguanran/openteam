# TEAMOS-0016 - 03 Work

- 标题：TEAMOS-CONCURRENCY-LOCKS
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增锁模块：`.team-os/scripts/pipelines/locks.py`
    - repo lock + scope lock
    - DB advisory lock（TEAMOS_DB_URL 可用时）+ file lock fallback（TTL + heartbeat renew + crash recovery）
    - LOCK_BUSY 诊断（holder 元数据）
  - 在关键写入口强制接入锁：
    - requirements: `.team-os/scripts/pipelines/requirements_raw_first.py`
    - feasibility: `.team-os/scripts/pipelines/feasibility_assess.py`
    - prompt: `.team-os/scripts/pipelines/prompt_compile.py`
    - system requirements channel: `.team-os/scripts/pipelines/system_requirements_update.py`
    - tasks: `.team-os/scripts/pipelines/task_create.py` / `.team-os/scripts/pipelines/task_close.py`
  - Workspace scaffold 补齐 `state/locks/` 目录，锁落在 Workspace（project scope）/repo state（teamos scope）。
  - 新增并发回归测试（subprocess 并发 + stale lock 恢复 + LOCK_BUSY 诊断）。

- 关键命令（含输出摘要）：
  - `python3 -m unittest -q` -> OK (33 tests)

- 决策与理由：
  - 锁优先做在“确定性 pipelines 写入口”层，避免运行时函数层出现嵌套锁/重入死锁；并用并发回归测试验证不会破坏 requirements.yaml/prompt 产物。

## 变更文件清单

- `.team-os/scripts/pipelines/locks.py`
- `.team-os/scripts/pipelines/requirements_raw_first.py`
- `.team-os/scripts/pipelines/feasibility_assess.py`
- `.team-os/scripts/pipelines/prompt_compile.py`
- `.team-os/scripts/pipelines/system_requirements_update.py`
- `.team-os/scripts/pipelines/task_create.py`
- `.team-os/scripts/pipelines/task_close.py`
- `.team-os/templates/runtime/orchestrator/app/workspace_store.py`
- `evals/test_concurrency_locks.py`
