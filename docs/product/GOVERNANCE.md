# 变更治理 (Governance)

本文件是 **OpenTeam 的治理真相源**：定义“任务=更新单位”、风险闸门、决定性产物策略，以及 Repo/Workspace 边界。

## 1. 定义 (DoR/DoD)

### DoR (Definition of Ready)

一个任务进入实施前至少满足：

- 台账已创建：`.openteam/ledger/tasks/<TASK_ID>.yaml`
- `00~02` 日志存在并有初始内容
- 风险等级与闸门明确（R2/R3 有审批计划）
- 依赖、验收标准、回滚策略（若涉及发布）已记录

### DoD (Definition of Done)

一个任务关闭前至少满足：

- `03~07` 日志补齐（按适用程度可合并，但必须说明）
- 测试证据与验收结果已记录
- 变更与回滚信息已记录（若涉及发布）
- Retro 已产出，并生成 Self-Improve 条目（若存在改进点）
- `./openteam task close <TASK_ID>` 通过（该命令会执行 policy/repo purity/tests 等闸门）

## 2. 更新单位（Update Unit）与 Git 纪律

- **一个 Update Unit = 一个任务**（`TASK_ID`）。
- **分支可选**：不再强制“每任务一分支”。默认允许直接在 `main` 上完成任务并推送；如需协作/评审，可使用工作分支并按需创建 PR。
- **先 close 再提交**：必须先 `./openteam task close <TASK_ID>` 通过，才允许 `git commit`/`git push`。
- **推荐 ship 命令**：`./openteam task ship <TASK_ID> --summary "<...>"`（close→闸门→commit→push；push 失败标记 BLOCKED）
- **提交信息**：`<TASK_ID>: <short summary>`
- **推送失败即阻塞**：若无 remote/无权限/网络失败，必须在任务日志 `03_work.md` 记录原因与修复步骤，并将任务标记为 `BLOCKED`（由脚本完成）。

## 3. 风险与审批策略

- R0/R1：默认无需审批（仍需日志与证据）
- R2：执行前审批（尤其是网络端口、docker socket、依赖升级）
- R3：必须审批，且需要明确回滚/应急预案

审批记录必须写入任务日志（建议写在 `01_plan.md` 与 `05_release.md`）。

实现约束（确定性）：

- 高风险动作必须先走 approvals 引擎（risk classifier + policy）再执行。
- 集群模式：由 Brain(leader) 按策略自动 approve/deny，并优先写入 Postgres（`OPENTEAM_DB_URL`）。
- 单机模式：需要人工确认（交互式 YES）后才可执行；无 DB 时写入 Workspace 审计文件作为待同步证据。
- 查看审批记录：`./openteam approvals list`（DB 优先；否则输出 fallback 审计路径）。

## 4. 决定性产物策略（脚本优先）

- 任何可重复/可程序化的产物必须由 Python pipelines 生成，并通过 schema 校验后写入真相源。
- Agent/LLM 只能输出建议/草案；不得直接写入或手改真相源文件。
- 典型真相源写入口（决定性、可重建）：
  - Requirements（Raw-First）：`./openteam req add|verify|rebuild`
  - Prompt：`./openteam prompt compile`
  - Projects/Panel：`./openteam panel sync`

## 5. 评审策略

建议的评审清单：

- 设计评审：架构、边界、数据流、失败模式
- 安全评审：secrets、权限、网络暴露、供应链
- QA 评审：测试覆盖、回归范围、验收标准
- 运维评审：可观测性、可回滚性、备份恢复

## 6. 双轨并行

- 业务仓库与 OpenTeam 仓库可以并行演进。
- OpenTeam 的改动通过 Self-Improve 工作流管理，避免干扰业务交付节奏。

## 7. Repo Purity（硬隔离：Repo vs Workspace）

硬规则：

- `openteam/` git 仓库只允许 scope=`openteam` 的文件（OpenTeam 自身：代码/模板/策略/文档/evals/集成适配器等）。
- 任何 scope=`project:<id>` 的真相源文件（requirements/冲突报告/ledger/logs/prompts/plan/项目 repo workdir 等）必须落在 Workspace（默认 `~/.openteam/workspace`），不得出现在 `openteam/` 目录树内。

强制执行：

- `./openteam doctor` 必须检查并在违规时失败
- 回归测试必须覆盖 repo_purity（见 `evals/test_repo_purity.py`）

违规处理：

1. 先看迁移计划（不改动文件）：

```bash
cd openteam
./openteam workspace migrate --from-repo
```

2. 迁移执行属于高风险动作（会移动仓库内文件到 Workspace；数据不会丢，但会产生 git deletions），需人工确认后执行：

```bash
cd openteam
./openteam workspace migrate --from-repo --force
```

## 8. 需求处理协议 v3（Raw‑First + Feasibility + Sidecar Assessments）

核心原则：

- **Baseline 不可覆盖**：`baseline/original_description_v1.md` 创建后不得覆盖，只能新增版本（v2/v3...）。
- **Raw Input 只允许用户原文**：`raw_inputs.jsonl` 仅包含“用户输入原文 + 必要元数据”（append-only），严禁写入评估结论/扩展内容/self-improve 内容。
- **Raw 的评估不污染 Raw**：可行性评估结果通过旁路索引 `raw_assessments.jsonl`（append-only）关联 `raw_id -> outcome + report_path`。
- **Feasibility 必做**：每条 Raw 都必须生成决定性可行性报告 `feasibility/<raw_id>.md`，并落盘 outcome（`FEASIBLE|PARTIALLY_FEASIBLE|NOT_FEASIBLE|NEEDS_INFO`）。
- **Raw‑First**：任何“新增用户需求输入”（CLI/API/chat 的 `NEW_REQUIREMENT`）必须先落盘 Raw，再允许生成/更新 Expanded。
- **Expanded 禁止手改**：`requirements.yaml` / `REQUIREMENTS.md` 由生成器维护；手工修改会被判定为 drift，并应通过 `rebuild` 恢复决定性渲染。
- **冲突与漂移必须显式决策**：
  - 新输入与既有 Expanded 冲突：生成 `conflicts/*.md` 并进入 `NEED_PM_DECISION`
  - Expanded 与 Baseline 漂移（drift）：生成 `conflicts/*-DRIFT.md` 并进入 `NEED_PM_DECISION`
- **可行性闸门**：
  - `NEEDS_INFO` / `NOT_FEASIBLE`：必须进入 `NEED_PM_DECISION`，不得把不可执行内容写入可执行 Expanded 条目。
  - `PARTIALLY_FEASIBLE`：允许写入可行部分，同时把不可行部分作为风险/限制/待决策进入冲突/决策项。
- **Self‑Improve 与 Raw 分离**：Self‑Improve 的提案写入 `.openteam/ledger/self_improve/*.md`，并通过系统通道更新 Expanded；禁止写入 `raw_inputs.jsonl`。
- **并发安全**：关键写入口必须先获取锁（repo lock + scope lock）。返回 `LOCK_BUSY` 时不得并发写入同 scope，按诊断信息等待或重试。
- **幂等与可追溯**：每次 Expanded 更新必须写入 `CHANGELOG.md`，并引用 `raw_id`/报告路径作为证据。

强制执行（工具链）：

- CLI：
  - `openteam req add`：写入 Raw（用户原文）+ 生成可行性报告 + 更新 Expanded（自动 drift/conflict 检测）
  - `openteam req verify`：仅校验（drift/conflict）
  - `openteam req rebuild`：决定性重渲染（禁止手改时用于恢复）
  - `openteam req baseline set-v2`：baseline v2 提案（默认进入 `NEED_PM_DECISION`）
- Control Plane：
  - 写操作必须 leader-only（非 leader 返回 409 + leader 信息，CLI 自动转发到 leader）

## 9. 项目仓库 AGENTS.md 注入（Team-OS 项目操作手册）

目标：任何被 Team-OS 管理/接入的项目仓库根目录 `AGENTS.md` 必须包含 Team-OS 项目操作手册区块，便于在项目仓库内工作的成员（Codex/人类）遵守边界并正确操作 Workspace 真相源。

强制规则：

- 注入区块只允许脚本写入/更新（幂等，反复执行不重复插入，不破坏原有内容）。
- 使用固定标记替换，保留项目原有内容：
  - `<!-- OPENTEAM_MANUAL_START -->`
  - `<!-- OPENTEAM_MANUAL_END -->`

入口：

```bash
cd openteam
./openteam project agents inject --project <project_id>
```

挂钩（自动触发，leader-only 写入）：

- `./openteam project config init|validate --project <id>`
- `./openteam req add|import|rebuild --scope project:<id>`
- `./openteam task new --scope project:<id> --mode bootstrap|upgrade`

## Hub Risk Additions

The following actions are HIGH risk and must use approvals:

- `openteam hub expose ...`
- `openteam hub restore --file ...`
- `openteam hub push-config ...` (contains connection secrets)
- `openteam node add --execute ...` with remote password mode

Redis default is enabled but bound locally by default. Remote Redis exposure must be explicitly approved.
