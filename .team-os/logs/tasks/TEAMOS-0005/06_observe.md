# TEAMOS-0005 - 06 Observe

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：observe

## 观测指标与口径

- CLI 可用性：
  - `teamos project config ...` 可执行并对 Workspace 文件生效
  - `teamos project agents inject` 幂等（重复执行无 diff）
- 治理闸门：
  - `./teamos doctor` PASS
  - `./teamos policy check` PASS
  - `python3 -m unittest -q` PASS

## 结果

- 通过（见 `04_test.md`）。

## 结论

- 是否达标：
- 是否需要后续任务：需要（建议后续补齐 project repo 级别的 commit/push/PR 自动化，且与项目任务 ledger 对齐）。
