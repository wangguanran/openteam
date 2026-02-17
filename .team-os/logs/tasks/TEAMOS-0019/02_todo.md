# TEAMOS-0019 - 02 TODO

- 标题：Verify: Req v3 + locks + approvals DB
- 日期：2026-02-17
- 当前状态：todo

## TODO (可并行)

- [x] 新增审计脚本：`.team-os/scripts/pipelines/audit_reqv3_locks.py`
- [x] CLI 接入：`./teamos audit reqv3-locks`
- [x] 生成审计报告：`docs/audits/REQV3_LOCKS_AUDIT_20260217T091619Z.md`
- [x] 修复 locks：DB 锁争用不降级；仅 DB 不可用才 fallback
- [x] 稳定并发锁回归测试：file-lock 测试强制使用 file backend
- [x] 修复 self-improve system channel 写入与 changelog raw_ref
- [ ] 跑最终自检：`unittest` / `policy check` / `doctor`
- [ ] `./teamos task close TEAMOS-0019` → commit → push

## Skill Boot 计划 (如需联网检索)

- 本任务不需要联网检索。
