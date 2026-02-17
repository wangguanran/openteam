# TEAMOS-0019 - 01 Plan

- 标题：Verify: Req v3 + locks + approvals DB
- 日期：2026-02-17
- 当前状态：plan

## 方案概述

- 通过新增“确定性审计脚本 + CLI 子命令”的方式完成端到端验收：
  - 脚本：`.team-os/scripts/pipelines/audit_reqv3_locks.py`
  - CLI：`./teamos audit reqv3-locks`
  - 输出：`docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`
- 审计脚本按真实执行路径调用现有 pipelines（req v3 / feasibility / locks / approvals / self-improve），并对关键产物进行可追溯校验（schema/路径/sha/关键断言）。
- 为保证稳定性：修复 DB 锁行为（争用时不降级到 file lock），并在测试里显式选择 file-lock backend 覆盖两条路径。

## 拆分与里程碑

- M1：落盘审计脚本与 CLI 接入；生成审计报告（PASS）。
- M2：修复并发锁 DB/文件锁降级策略；修复相关回归测试稳定性。
- M3：修复 self-improve 写入通道为 system channel（不写 raw_inputs），并修复 changelog raw_ref。
- M4：运行 `unittest`/`policy check`/`doctor`，关闭任务并按分支提交推送。

## 风险评估与闸门

- 风险等级：R1
- 审批点：
  - 仅模拟 HIGH 风险审批记录写入 DB（action_kind=repo_create），不触发真实 GitHub 行为。

## 依赖

- 可选：本地 Postgres（`TEAMOS_DB_URL`）用于 approvals/db migrate 验证；无 DB 时可用 `--skip-db` 跳过相关审计项。

## 验收标准

- `./teamos audit reqv3-locks` 生成报告且所有检查为 PASS：`docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`
- `python3 -m unittest -q` 通过
- `./teamos policy check` 通过
- `./teamos doctor` 通过（含 DB 时需通过 DB 连通性检查）
