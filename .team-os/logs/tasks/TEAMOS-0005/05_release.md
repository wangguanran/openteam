# TEAMOS-0005 - 05 Release

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：release

## 发布内容

- Team-OS 新增项目配置与项目仓库 AGENTS 手册注入能力（决定性 pipelines + CLI + tests + docs）。

## 审批记录 (如需)

- 本任务为 R1，无需审批。

## 发布步骤

```bash
cd team-os
./teamos task ship TEAMOS-0005 --scope teamos --summary "project config + project AGENTS manual injection"
```

## 回滚方案

- 回滚 Team-OS 仓库：对该任务对应 commit 执行 `git revert`。
- 回滚项目仓库 AGENTS 注入：再次运行 `teamos project agents inject`（使用更低版本模板）或在项目仓库回退对应 commit。
