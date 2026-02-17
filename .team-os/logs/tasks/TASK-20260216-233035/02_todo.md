# TASK-20260216-233035 - 02 TODO

- 标题：TEAMOS-SCRIPT-PIPELINES
- 日期：2026-02-17
- 当前状态：todo

## TODO (可并行)

- [ ] 新增目录 `team-os/.team-os/scripts/pipelines/` 与公共模块（路径/IO/模板渲染/稳定 ID/hash）。
- [ ] 新增 schemas：`prompt_manifest.schema.json`、`task_ledger.schema.json`。
- [ ] 新增模板：`requirements_md.j2`、`prompt_master.md.j2`、`repo_understanding.md.j2`。
- [ ] 实现 pipelines：`doctor.py`、`task_create.py`、`task_close.py`、`requirements_raw_first.py`、`prompt_compile.py`。
- [ ] 更新 `team-os/teamos` CLI：`task new/close`、`doctor` 调用 pipelines（本地优先）。
- [ ] 生成理解文档：`team-os/docs/team_os/REPO_UNDERSTANDING.md`。
- [ ] 自检：`python3 -m unittest -q`、`./teamos policy check`、repo_purity check。
- [ ] `teamos task close TASK-20260216-233035` 通过。

## Skill Boot 计划 (如需联网检索)

- 本任务预计不需要联网检索（如确需外部事实，将按规范落盘到 `.team-os/kb/sources/`）。
