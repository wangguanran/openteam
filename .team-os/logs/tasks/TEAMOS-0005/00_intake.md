# TEAMOS-0005 - 00 Intake

- 标题：TEAMOS-PROJECT-AGENTS-MANUAL
- 日期：2026-02-17
- 当前状态：intake

## 一句话需求

- 为所有 Team-OS 管理的项目仓库（Workspace `projects/<id>/repo`）提供 **脚本化、幂等、决定性** 的 `AGENTS.md` 注入机制，使项目仓库根目录自动包含 Team-OS 项目操作手册区块，并提供项目配置的 Workspace-local 读写/校验入口。

## 目标/非目标

- 目标：
- 新增决定性 pipelines：
  - `.team-os/scripts/pipelines/project_config.py`（Workspace 内 `project.yaml` 的 init/show/set/validate + schema 校验）
  - `.team-os/scripts/pipelines/project_agents_inject.py`（向项目仓库根 `AGENTS.md` 注入/更新手册区块；标记替换；幂等）
- 新增 schema/template：
  - `.team-os/schemas/project_config.schema.json`
  - `.team-os/templates/project_config.yaml.j2`
  - `.team-os/templates/project_agents_manual_block.md`
- CLI 增强：
  - `teamos project config ...`
  - `teamos project agents inject ...`
  - `teamos prompt build/diff ...`（满足项目操作手册命令要求）
- 自动挂钩：
  - `project config init/validate`
  - `req add/import/rebuild`（scope=project）
  - `task new --scope project:<id> --mode bootstrap|upgrade`
- 回归测试：至少覆盖注入幂等/替换正确/保留原内容、project config init/set/validate。
- 非目标：
- 不在本任务内对“项目仓库”执行自动 commit/push/PR（仅生成/更新工作区文件；项目仓库的提交策略留给项目任务/流程控制）。

## 约束与闸门

- 风险等级：R1
- 需要审批的动作（如有）：无（不执行删除/覆盖数据、开放公网端口、生产发布、强推等高风险动作）
- 关键约束：
  - Repo/Workspace 隔离：所有 project 真相源在 Workspace；禁止写入 `team-os/` 目录树
  - 注入必须幂等：重复运行不重复插入；仅替换标记区块；保留项目原有内容
  - 注入标记固定：`<!-- TEAMOS_MANUAL_START -->` / `<!-- TEAMOS_MANUAL_END -->`

## 澄清问题 (必须回答)

- Q: 项目仓库 AGENTS.md 是否允许覆盖原内容？
  - A: 不允许。仅允许在固定标记区块内替换内容；无标记时仅追加注入区块到文件末尾。

## 需要哪些角色/工作流扩展

- 角色：
- 工作流：

## 产物清单 (本任务必须落盘的文件路径)

- `.team-os/scripts/pipelines/project_config.py`
- `.team-os/scripts/pipelines/project_agents_inject.py`
- `.team-os/scripts/pipelines/prompt_diff.py`
- `.team-os/schemas/project_config.schema.json`
- `.team-os/templates/project_config.yaml.j2`
- `.team-os/templates/project_agents_manual_block.md`
- `teamos`
- `AGENTS.md`
- `docs/GOVERNANCE.md`
- `docs/EXECUTION_RUNBOOK.md`
- `tests/test_project_config.py`
- `tests/test_project_agents_inject.py`
