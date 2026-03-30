# Delivery Studio Runbook

`delivery-studio` 是 OpenTeam 当前优先交付路径。默认从本机 cockpit 进入，不再要求任何 Hub、Cluster 或远程节点前置操作。

## Operator Flow

```bash
openteam cockpit --team delivery-studio --project <project_id>
```

建议流程：

1. 进入 cockpit
2. 创建新请求或导入变更
3. 等待进入审批或 blocking gate
4. 生成计划并开始执行
5. 走 review
6. 仅在 `panel-review/blocking-gate` 与仓库 CI 通过后合并

约束：

- lock 之后的修改必须作为新的 change request
- 交付真相源始终位于 Workspace，而不是仓库

```text
~/.openteam/workspace/projects/<project_id>/state/delivery_studio
```

## 本地依赖面

- 本地 Control Plane：`http://127.0.0.1:8787`
- 本地状态接口：`GET /v1/status`
- 本地运行态数据库：`~/.openteam/runtime/default/state/runtime.db`

## GitHub Projects Fields

- Request ID
- Project
- Priority
- Stage
- Spec Approved
- Change Request
- Review Gate
- CI
- Release Ready
- Owner
- Blocked Reason
- Needs You

## Branch Protection

当前已确认的 review gate 是：

- `panel-review/blocking-gate`

其余 CI check 以仓库当前实际发布的 job 名称为准。若 repo host 还没有单独发布 delivery-studio 命名的 checks，就继续使用现有 CI checks，并在这些 checks 可用后再补齐 branch protection。
