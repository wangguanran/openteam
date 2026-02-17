# TEAMOS-0005 - 02 TODO

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：todo

## TODO (可并行)

- [x] 新增 schema：`.team-os/schemas/project_config.schema.json`
- [x] 新增模板：`.team-os/templates/project_config.yaml.j2`、`.team-os/templates/project_agents_manual_block.md`
- [x] 新增 pipeline：`.team-os/scripts/pipelines/project_config.py`
- [x] 新增 pipeline：`.team-os/scripts/pipelines/project_agents_inject.py`
- [x] 新增 pipeline：`.team-os/scripts/pipelines/prompt_diff.py`（补齐 `teamos prompt diff`）
- [x] CLI 集成：
  - [x] `teamos project config ...`
  - [x] `teamos project agents inject ...`
  - [x] `teamos prompt build/diff ...`
- [x] 自动挂钩：
  - [x] `req add/import/rebuild`（project scope）
  - [x] `project config init/validate`
  - [x] `task new --mode bootstrap|upgrade`（project scope）
- [x] 回归测试：
  - [x] `tests/test_project_config.py`
  - [x] `tests/test_project_agents_inject.py`
- [ ] 运行闸门：policy/doctor/unittest
- [ ] `./teamos task ship TEAMOS-0005 --summary "..."`

## Skill Boot 计划 (如需联网检索)

- 本任务无需联网检索
