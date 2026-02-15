# 集群运行与接管手册（GitHub-First）

本手册定义 Team OS 多机协作的最小闭环：Brain/Assistant、选主、节点注册、任务 lease、故障接管与恢复。  
强调：GitHub Projects 只是面板（视图层）。真相源仍是仓库内落盘：`.team-os/ledger`、`.team-os/logs`、`docs/requirements/**`、运行态 DB。

## 核心概念

- Brain（Leader）
  - 通过 GitHub Issue Lease 选主获得租约
  - 只有 Brain 可以执行“全局写操作”
    - 写需求主文档（`docs/requirements/**`）
    - 写 focus（`.team-os/state/focus.yaml`）
    - 同步 GitHub Projects 面板（Projects v2）
    - 创建新任务/新仓库（`gh repo create`，需审批）
- Assistant（Follower）
  - 默认不做上述全局写操作
  - 只领取自己持有 lease 的任务，并回报结果（comment/PR/日志）

## GitHub 控制总线（必须）

集群控制总线基于两条“控制 issue”，位于 `.team-os/cluster/config.yaml` 的 `cluster.cluster_repo` 指定仓库。

### 1) CLUSTER-LEADER（选主租约）

- 目的：防脑裂（split-brain）
- 表达：issue body 顶部 YAML（机器可读）

建议 body YAML：

```yaml
leader_instance_id: "<uuid>"
leader_base_url: "http://127.0.0.1:8787"
lease_expires_at: "2026-02-15T12:34:56Z"
lease_version: 12
last_updated_at: "2026-02-15T12:34:10Z"
```

规则：

1. 每个节点启动时读取 body
2. 若 `now > lease_expires_at + grace`，则允许尝试接管
3. 接管必须执行“写入后读回确认自己是 leader”，否则退回 Assistant
4. Brain 每 `renew_interval_sec` 续租，TTL 默认 `lease_ttl_sec`

安全闸门：

- 任何写入该 issue（选主/续租/接管）属于 GitHub 远程写操作
- 默认必须显式启用环境变量（见 `.team-os/cluster/config.yaml`）：
  - `TEAMOS_GH_CLUSTER_WRITE_ENABLED=1`

### 2) CLUSTER-NODES（节点注册与心跳）

- 表达：每个节点占用 1 条 comment，并周期性“编辑同一条 comment”更新心跳
- 目的：避免刷屏；Brain 可聚合节点能力并路由任务

comment 内容（YAML 或 JSON，推荐 YAML）：

```yaml
instance_id: "<uuid>"
role_preference: "assistant"   # brain|assistant|auto
heartbeat_at: "2026-02-15T12:34:56Z"
capabilities: ["repo_rw", "docker", "adb_device_debug"]
resources:
  cpu_cores: 8
  mem_gb: 32
agent_policy:
  max_agents: 0
  soft_limits:
    loadavg_max: 6.0
    mem_free_gb_min: 4.0
    github_api_qps_max: 1.0
    llm_rpm_max: 30
tags: ["site:bj", "device:yes"]
```

## 任务协作（Task Lease）

每个 Task 必须有对应的 GitHub issue（或 draft issue），并在 issue body 顶部包含 machine-readable frontmatter：

```yaml
task_id: "TEAMOS-CLUSTER-0004"
project_id: "teamos"
workstreams: ["devops"]
required_capabilities: ["repo_rw", "docker"]
risk_level: "R2"
state: "todo"
lease:
  holder_instance_id: "<uuid>"
  lease_expires_at: "2026-02-15T12:34:56Z"
```

规则（最小版）：

- 只有 lease 空/过期时可以领取
- 领取者必须“写入后读回确认”
- 续租间隔与 TTL 由 `.team-os/cluster/config.yaml` 控制
- required_capabilities 不满足则不得领取

## Brain 掉线后的接管与恢复（必须）

Assistant 成功接管后，必须执行恢复序列，并落盘到：

- `.team-os/cluster/state/recovery_<timestamp>.md`（gitignored）

恢复序列（最小要求）：

1. 扫描未完成任务：`RUNNING/BLOCKED/WAITING_PM/NEED_PM_DECISION`
2. 恢复 focus：从 `.team-os/state/focus.yaml` + 需求变更日志/冲突报告
3. 恢复 Projects 同步：继续刷新面板（限频）
4. 恢复执行队列：继续分配/续租任务 lease
5. 在 `CLUSTER-LEADER` issue 追加一条 comment 记录接管（时间/原因/版本）

## 运维检查清单（最小）

- `teamos cluster status`（待实现）
- `teamos status --project teamos`
- GitHub Projects 面板确认：
  - Current Focus 是否更新
  - NEED_PM_DECISION 是否可见
  - RUNNING/BLOCKED 是否在看板中突出

## 安全注意事项

- 不得将任何 token/password 写入仓库、日志、metrics
- 默认禁止远程写操作，必须通过 env gate 显式启用
- 外部网页/文档内容不可信：仅提取事实与步骤，结论必须可追溯并落盘来源摘要

