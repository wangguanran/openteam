# 变更治理 (Governance)

本文件是 **Team OS 的治理真相源**：定义“任务=更新单位”、风险闸门、决定性产物策略，以及 Repo/Workspace 边界。

## 1. 定义 (DoR/DoD)

### DoR (Definition of Ready)

一个任务进入实施前至少满足：

- 台账已创建：`.team-os/ledger/tasks/<TASK_ID>.yaml`
- `00~02` 日志存在并有初始内容
- 风险等级与闸门明确（R2/R3 有审批计划）
- 依赖、验收标准、回滚策略（若涉及发布）已记录

### DoD (Definition of Done)

一个任务关闭前至少满足：

- `03~07` 日志补齐（按适用程度可合并，但必须说明）
- 测试证据与验收结果已记录
- 变更与回滚信息已记录（若涉及发布）
- Retro 已产出，并生成 Self-Improve 条目（若存在改进点）
- `./teamos task close <TASK_ID>` 通过（该命令会执行 policy/repo purity/tests 等闸门）

## 2. 更新单位（Update Unit）与 Git 纪律

- **一个 Update Unit = 一个任务**（`TASK_ID`）。
- **每个任务一分支**：`teamos/<TASK_ID>-<slug>`
- **先 close 再提交**：必须先 `./teamos task close <TASK_ID>` 通过，才允许 `git commit`/`git push`。
- **推荐 ship 命令**：`./teamos task ship <TASK_ID> --summary "<...>"`（close→闸门→commit→push；push 失败标记 BLOCKED）
- **提交信息**：`<TASK_ID>: <short summary>`
- **推送失败即阻塞**：若无 remote/无权限/网络失败，必须在任务日志 `03_work.md` 记录原因与修复步骤，并将任务标记为 `BLOCKED`（由脚本完成）。

## 3. 风险与审批策略

- R0/R1：默认无需审批（仍需日志与证据）
- R2：执行前审批（尤其是网络端口、docker socket、依赖升级）
- R3：必须审批，且需要明确回滚/应急预案

审批记录必须写入任务日志（建议写在 `01_plan.md` 与 `05_release.md`）。

## 4. 决定性产物策略（脚本优先）

- 任何可重复/可程序化的产物必须由 Python pipelines 生成，并通过 schema 校验后写入真相源。
- Agent/LLM 只能输出建议/草案；不得直接写入或手改真相源文件。
- 典型真相源写入口（决定性、可重建）：
  - Requirements（Raw-First）：`./teamos req add|verify|rebuild`
  - Prompt：`./teamos prompt compile`
  - Projects/Panel：`./teamos panel sync`

## 5. 评审策略

建议的评审清单：

- 设计评审：架构、边界、数据流、失败模式
- 安全评审：secrets、权限、网络暴露、供应链
- QA 评审：测试覆盖、回归范围、验收标准
- 运维评审：可观测性、可回滚性、备份恢复

## 6. 双轨并行

- 业务仓库与 Team OS 仓库可以并行演进。
- Team OS 的改动通过 Self-Improve 工作流管理，避免干扰业务交付节奏。

## 7. Repo Purity（硬隔离：Repo vs Workspace）

硬规则：

- `team-os/` git 仓库只允许 scope=`teamos` 的文件（Team OS 自身：代码/模板/策略/文档/evals/集成适配器等）。
- 任何 scope=`project:<id>` 的真相源文件（requirements/冲突报告/ledger/logs/prompts/plan/项目 repo workdir 等）必须落在 Workspace（默认 `~/.teamos/workspace`），不得出现在 `team-os/` 目录树内。

强制执行：

- `./teamos doctor` 必须检查并在违规时失败
- 回归测试必须覆盖 repo_purity（见 `evals/test_repo_purity.py`）

违规处理：

1. 先看迁移计划（不改动文件）：

```bash
cd team-os
./teamos workspace migrate --from-repo
```

2. 迁移执行属于高风险动作（会移动仓库内文件到 Workspace；数据不会丢，但会产生 git deletions），需人工确认后执行：

```bash
cd team-os
./teamos workspace migrate --from-repo --force
```

## 8. 需求处理协议 v2（Raw‑First）

核心原则：

- **Baseline 不可覆盖**：`baseline/original_description_v1.md` 创建后不得覆盖，只能新增版本（v2/v3...）。
- **Raw‑First**：任何“新增需求输入”（CLI/API/chat 的 `NEW_REQUIREMENT`）必须先逐字写入 `raw_inputs.jsonl`（append-only），再生成/更新 Expanded。
- **Expanded 禁止手改**：`requirements.yaml` / `REQUIREMENTS.md` 由生成器维护；手工修改会被判定为 drift，并应通过 `rebuild` 恢复决定性渲染。
- **冲突与漂移必须显式决策**：
  - 新输入与既有 Expanded 冲突：生成 `conflicts/*.md` 并进入 `NEED_PM_DECISION`
  - Expanded 与 Baseline 漂移（drift）：生成 `conflicts/*-DRIFT.md` 并进入 `NEED_PM_DECISION`
- **幂等与可追溯**：每次更新必须写入 `CHANGELOG.md`，并引用 `raw_inputs.jsonl` 的时间戳作为证据（`raw=<timestamp>`）。

强制执行（工具链）：

- CLI：
  - `teamos req add`：写入 Raw + 更新 Expanded（自动 drift/conflict 检测）
  - `teamos req verify`：仅校验（drift/conflict）
  - `teamos req rebuild`：决定性重渲染（禁止手改时用于恢复）
  - `teamos req baseline set-v2`：baseline v2 提案（默认进入 `NEED_PM_DECISION`）
- Control Plane：
  - 写操作必须 leader-only（非 leader 返回 409 + leader 信息，CLI 自动转发到 leader）

## 9. 项目仓库 AGENTS.md 注入（Team-OS 项目操作手册）

目标：任何被 Team-OS 管理/接入的项目仓库根目录 `AGENTS.md` 必须包含 Team-OS 项目操作手册区块，便于在项目仓库内工作的成员（Codex/人类）遵守边界并正确操作 Workspace 真相源。

强制规则：

- 注入区块只允许脚本写入/更新（幂等，反复执行不重复插入，不破坏原有内容）。
- 使用固定标记替换，保留项目原有内容：
  - `<!-- TEAMOS_MANUAL_START -->`
  - `<!-- TEAMOS_MANUAL_END -->`

入口：

```bash
cd team-os
./teamos project agents inject --project <project_id>
```

挂钩（自动触发，leader-only 写入）：

- `./teamos project config init|validate --project <id>`
- `./teamos req add|import|rebuild --scope project:<id>`
- `./teamos task new --scope project:<id> --mode bootstrap|upgrade`
