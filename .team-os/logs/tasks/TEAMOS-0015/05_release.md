# TEAMOS-0015 - 05 Release

- 标题：TEAMOS-SELF-IMPROVE-SEPARATION
- 日期：2026-02-17
- 当前状态：release

## 发布内容

- Self-Improve 与 Raw 完全隔离（system update channel + source 标记）
- 新增脚本入口 `system_requirements_update.py` + 回归测试

## 审批记录 (如需)

- 本任务无高风险动作（不含数据删除/强推/公网暴露/生产发布等），无需审批。

## 发布步骤

```bash
python3 -m unittest -q

./teamos task close TEAMOS-0015

git add -A
git commit -m "TEAMOS-0015: Separate self-improve from raw inputs"
git push -u origin teamos/TEAMOS-0015-self-improve-separation
```

## 回滚方案

- git revert 本任务对应 commit。
