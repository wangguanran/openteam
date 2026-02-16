# 变更治理 (Governance)

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

## 2. 风险与审批策略

- R0/R1：默认无需审批（仍需日志与证据）
- R2：执行前审批（尤其是网络端口、docker socket、依赖升级）
- R3：必须审批，且需要明确回滚/应急预案

审批记录必须写入任务日志（建议写在 `01_plan.md` 与 `05_release.md`）。

## 3. 评审策略

建议的评审清单：

- 设计评审：架构、边界、数据流、失败模式
- 安全评审：secrets、权限、网络暴露、供应链
- QA 评审：测试覆盖、回归范围、验收标准
- 运维评审：可观测性、可回滚性、备份恢复

## 4. 双轨并行

- 业务仓库与 Team OS 仓库可以并行演进。
- Team OS 的改动通过 Self-Improve 工作流管理，避免干扰业务交付节奏。

## 5. Repo Purity（硬隔离：Repo vs Workspace）

硬规则：

- `team-os/` git 仓库只允许 scope=`teamos` 的文件（Team OS 自身：代码/模板/策略/文档/evals/集成适配器等）。
- 任何 scope=`project:<id>` 的真相源文件（requirements/冲突报告/ledger/logs/prompts/plan/项目 repo workdir 等）必须落在 Workspace（默认 `~/.teamos/workspace`），不得出现在 `team-os/` 目录树内。

强制执行：

- `teamos doctor` / `./scripts/teamos.sh doctor` 必须检查并在违规时失败
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
