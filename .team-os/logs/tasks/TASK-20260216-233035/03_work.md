# TASK-20260216-233035 - 03 Work

- 标题：TEAMOS-SCRIPT-PIPELINES
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增确定性 pipelines：`.team-os/scripts/pipelines/`（doctor/task_create/task_close/requirements_raw_first/prompt_compile 等）。
  - 新增 schemas：`.team-os/schemas/task_ledger.schema.json`、`.team-os/schemas/prompt_manifest.schema.json`。
  - 新增模板：`.team-os/templates/requirements_md.j2`、`.team-os/templates/prompt_master.md.j2`、`.team-os/templates/repo_understanding.md.j2`。
  - `teamos` CLI：
    - `teamos doctor` 改为调用 `pipelines/doctor.py`
    - `teamos task new/close` 改为调用 `pipelines/task_create.py` 与 `pipelines/task_close.py`
    - 新增 `teamos prompt compile` 调用 `pipelines/prompt_compile.py`
  - requirements 决定性渲染：`render_requirements_md` 改为使用模板并按 `req_id` 排序，`save_requirements` 写入前排序。
  - 生成理解闸门产物：`docs/team_os/REPO_UNDERSTANDING.md`（由 pipeline 生成）。
  - 为通过 doctor：重建并重启 runtime control-plane 镜像，使 openapi 覆盖 cluster/tasks/nodes 端点。
- 关键命令（含输出摘要）：
  - `python3 -m unittest -q` -> OK
  - `./teamos policy check --quiet` -> PASS
  - `python3 .team-os/scripts/pipelines/requirements_raw_first.py rebuild --scope teamos` -> 写入决定性 `REQUIREMENTS.md`
  - `./teamos prompt compile --scope teamos` -> 生成 `prompt-library/teamos/*`
  - `python3 .team-os/scripts/pipelines/repo_understanding_gate.py --task-id TASK-20260216-233035` -> 生成理解文档
  - `docker compose build control-plane && docker compose up -d control-plane` -> 更新 control-plane 使 doctor openapi PASS
- 决策与理由：
  - Prompt 内容不包含时间戳/绝对路径，避免“同输入不同输出”的非确定性；编译元信息写入 manifest/changelog。
  - Task close 先做本地闸门（schema/metrics/policy/purity/tests），后续任务再强化到 commit/push 强制纪律（见后续任务）。

## 变更文件清单

- `team-os/teamos`
- `team-os/.team-os/scripts/pipelines/*`
- `team-os/.team-os/schemas/task_ledger.schema.json`
- `team-os/.team-os/schemas/prompt_manifest.schema.json`
- `team-os/.team-os/templates/requirements_md.j2`
- `team-os/.team-os/templates/prompt_master.md.j2`
- `team-os/.team-os/templates/repo_understanding.md.j2`
- `team-os/.team-os/templates/runtime/orchestrator/app/requirements_store.py`
- `team-os/docs/team_os/REPO_UNDERSTANDING.md`
- `team-os/docs/teamos/requirements/REQUIREMENTS.md`
- `team-os/prompt-library/teamos/*`
