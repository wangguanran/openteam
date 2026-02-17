# TEAMOS-0005 - 03 Work

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：work

## 实施记录

- 变更点：
  - 新增 Workspace-local 项目配置 pipeline：`.team-os/scripts/pipelines/project_config.py`
    - 配置路径：`<WORKSPACE>/projects/<id>/state/config/project.yaml`
    - 命令：`teamos project config init|show|set|validate --project <id>`
    - schema：`.team-os/schemas/project_config.schema.json`
    - 默认模板：`.team-os/templates/project_config.yaml.j2`
  - 新增项目仓库 `AGENTS.md` 注入 pipeline：`.team-os/scripts/pipelines/project_agents_inject.py`
    - 标记替换：`<!-- TEAMOS_MANUAL_START -->` / `<!-- TEAMOS_MANUAL_END -->`
    - 无标记则追加；无文件则创建；幂等（内容相同不改动）
    - 默认 leader-only（非 leader/leader check 失败 => plan-only，安全退出）
    - 模板：`.team-os/templates/project_agents_manual_block.md`
  - 补齐项目手册所需命令：
    - 新增 `teamos prompt build`（compile 的 alias）
    - 新增 `teamos prompt diff`（pipeline：`.team-os/scripts/pipelines/prompt_diff.py`）
  - 自动挂钩（leader-only 写入由 pipeline 执行）：
    - `teamos project config init/validate` 后自动触发注入
    - `teamos req add/import/rebuild --scope project:<id>` 后自动触发注入
    - `teamos task new --scope project:<id> --mode bootstrap|upgrade` 后自动触发注入
  - 文档更新：
    - `AGENTS.md` 增加项目仓库 AGENTS 注入机制说明
    - `docs/GOVERNANCE.md` / `docs/EXECUTION_RUNBOOK.md` 补齐项目配置与注入入口
  - 回归测试：
    - `tests/test_project_config.py`
    - `tests/test_project_agents_inject.py`
- 关键命令（含输出摘要）：
  - `python3 -m unittest -q`：PASS（新增 2 个测试文件）
  - `./teamos project --help`：包含 `config/agents` 子命令
  - `./teamos prompt --help`：包含 `build/diff` 子命令
  - `./teamos doctor`：PASS
- 决策与理由：
  - 采用“标记区块替换”策略，确保不破坏项目原有 AGENTS 内容；并且可幂等更新。
  - 注入默认 leader-only：非 leader 不写项目仓库文件，仅输出计划结果，符合集群写入约束。

## 变更文件清单

- `.team-os/schemas/project_config.schema.json`
- `.team-os/templates/project_config.yaml.j2`
- `.team-os/templates/project_agents_manual_block.md`
- `.team-os/scripts/pipelines/project_config.py`
- `.team-os/scripts/pipelines/project_agents_inject.py`
- `.team-os/scripts/pipelines/prompt_diff.py`
- `teamos`
- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
- `tests/test_project_config.py`
- `tests/test_project_agents_inject.py`
