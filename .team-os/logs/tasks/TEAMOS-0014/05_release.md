# TEAMOS-0014 - 05 Release

- 标题：TEAMOS-RAW-FEASIBILITY-V3
- 日期：2026-02-17
- 当前状态：release

## 发布内容

- Raw-First v3（user-only raw_inputs + feasibility + raw_assessments）
- requirements pipeline 与 control-plane 适配更新
- 回归测试新增覆盖

## 审批记录 (如需)

- 本任务无高风险动作（不含数据删除/强推/公网暴露/生产发布等），无需审批。

## 发布步骤

```bash
python3 -m unittest -q

./teamos task close TEAMOS-0014

git add -A
git commit -m "TEAMOS-0014: Raw v3 feasibility assessments"
git push -u origin teamos/TEAMOS-0014-raw-feasibility-v3
```

## 回滚方案

- 通过 git revert 回滚该分支对应 commit；或从 legacy 备份恢复 `docs/teamos/requirements/raw_inputs.v2_legacy_*.jsonl`。
