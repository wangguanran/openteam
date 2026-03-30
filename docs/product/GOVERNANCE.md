# 变更治理

本文件定义 OpenTeam 当前单节点产品形态下的治理规则：任务是更新单位，运行态在本地 runtime，项目真相源在 Workspace，所有高风险动作都要留下审批与证据。

## 1. DoR / DoD

### DoR

一个任务进入实施前至少满足：

- 台账已创建：`~/.openteam/runtime/default/state/ledger/tasks/<TASK_ID>.yaml`
- `00~02` 阶段日志已落盘
- 风险等级与审批点明确
- 验收标准、依赖、回滚策略已记录

### DoD

一个任务关闭前至少满足：

- `03~07` 阶段日志补齐
- 测试证据与验收结果已记录
- 变更摘要与剩余风险已记录
- 如有改进点，已产出 retro
- `./openteam task close <TASK_ID>` 通过

## 2. 更新单位与 Git 纪律

- 一个 Update Unit = 一个任务
- 分支不是强制项，但任务边界必须清晰
- 先 `task close`，再 `git commit` / `git push`
- 推荐使用：`./openteam task ship <TASK_ID> --summary "<summary>"`
- 提交信息：`<TASK_ID>: <short summary>`

## 3. 风险与审批

- R0 / R1：默认无需审批，仍需日志与证据
- R2：执行前审批
- R3：必须审批，并明确回滚/应急预案

审批记录必须出现在任务日志中。当前单节点模式下，审批与审计证据默认记录在本地 runtime：

- `~/.openteam/runtime/default/state/runtime.db`
- `~/.openteam/runtime/default/state/audit/`

查看审批记录：

```bash
./openteam approvals list
```

## 4. 决定性产物策略

- 任何可程序化产物都必须通过脚本或 pipeline 生成
- Agent/LLM 只能给建议或草案，不得手改真相源
- 典型入口：
  - `./openteam req add|verify|rebuild`
  - `./openteam prompt compile`
  - `./openteam panel sync`

## 5. 评审策略

建议至少覆盖以下维度：

- 设计评审：边界、数据流、失败模式
- 安全评审：secrets、权限、对外写入
- QA 评审：测试覆盖、回归范围、验收标准
- 运维评审：可观测性、恢复、回滚

## 6. Repo / Workspace / Runtime 边界

- Repo：平台代码、模板、文档、测试
- Workspace：任何 `project:<id>` 真相源
- Runtime：OpenTeam 自身 ledger、logs、audit、`runtime.db`

强制执行：

- `./openteam doctor` 必须拦截 repo purity 违规
- 回归测试必须覆盖文档契约与 repo purity

若仓库中残留了项目态文件，先做 dry-run：

```bash
./openteam workspace migrate --from-repo
```

真正迁移属于高风险动作：

```bash
./openteam workspace migrate --from-repo --force
```

## 7. 需求处理原则

- Raw input 只记录用户原文
- Expanded requirements 由决定性生成器维护
- 冲突、漂移、不可行项必须显式进入决策
- 关键写入口必须具备锁与幂等保证
- 每次 Expanded 更新都要留下 changelog 与证据引用

## 8. 项目仓库 AGENTS.md 注入

项目仓库根的 `AGENTS.md` 注入只允许脚本执行，必须保留原仓库内容，并使用固定标记区块：

- `<!-- OPENTEAM_MANUAL_START -->`
- `<!-- OPENTEAM_MANUAL_END -->`

入口：

```bash
./openteam project agents inject --project <project_id>
```

## 9. 高风险动作示例

以下动作默认都需要审批：

- 打开公网端口或配置公网反向代理
- 删除、覆盖或迁移真相源数据
- 旋转、导出、写入真实 secrets
- 对 GitHub 或其他外部系统执行写操作
- 修改审批策略、弱化审计、绕过日志要求
- 使用 `workspace migrate --from-repo --force`

不再把 Hub、Cluster、Node 相关命令作为 operator 指南的一部分。
