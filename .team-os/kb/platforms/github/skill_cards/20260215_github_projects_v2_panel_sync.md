# Skill Card：GitHub Projects v2 面板同步（Team OS）

## 适用场景

- 需要一个“主面板/视图层”展示 Team OS 运行态（focus/agents/tasks/decisions）与开发计划（milestones/roadmap）
- 要求：真相源在本地仓库与运行态数据库，GitHub Projects 可随时从真相源全量重建/重同步

## 真相源与视图层边界（Hard Rule）

- 真相源：`.team-os/ledger/**`、`docs/requirements/**`、`.team-os/state/**`、`.team-os/state/runtime.db`
- 视图层：GitHub Projects v2（Table/Board/Roadmap）
- 任何在 Projects UI 的手工编辑，不得成为事实源；需要回写时必须先落盘到真相源，再触发同步。

## 配置入口（必须落盘）

- Projects 映射文件：`.team-os/integrations/github_projects/mapping.yaml`
  - `projects.<project_id>.owner_type/owner/project_number/project_url`
  - `fields.*`：字段名/类型/选项（full sync 可按名创建/复用）
  - `status_mapping`：ledger 状态 -> Project 状态选项 key

## Projects 建议字段与视图

字段（最小集合）：

- `TeamOS Status`（SINGLE_SELECT：Todo/In Progress/In Review/Blocked/Done）
- `Workstreams`（TEXT：逗号分隔，便于过滤）
- `Risk`（SINGLE_SELECT：LOW/MED/HIGH）
- `Need PM Decision`（SINGLE_SELECT：Yes/No）
- `Current Focus`（TEXT）
- `Active Agents`（NUMBER）
- `Last Heartbeat`（TEXT：ISO-8601；Projects `DATE` 不包含时间）
- `Start Date` / `Target Date`（DATE：Roadmap 用）
- `Task ID`（TEXT：稳定主键；用于重同步）
- `Links`（TEXT）

视图（至少 3 个，建议手工在 UI 创建）：

1. Table: Backlog（字段齐全 + 可筛选）
2. Board: Delivery（按 `TeamOS Status` 分列）
3. Roadmap: Timeline（使用 Start/Target Date）

建议额外视图：

- Decisions：过滤 `Need PM Decision = Yes`

## 同步机制（Control Plane）

Control Plane endpoints：

- `POST /v1/panel/github/sync`：手动同步（支持 `dry_run` 与 `mode=full|incremental`）
- `GET /v1/panel/github/health`：同步健康度（最近同步、失败次数、是否建议 full）
- `GET /v1/panel/github/config`：mapping 摘要与 URL

同步数据源聚合：

- tasks：`.team-os/ledger/tasks/*.yaml`
- focus：`.team-os/state/focus.yaml`
- agents：运行态 SQLite（`.team-os/state/runtime.db`）
- decisions：`docs/requirements/**/requirements.yaml` 中的 `NEED_PM_DECISION`
- milestones：`docs/plan/<project_id>/plan.yaml`

稳定映射键：

- 每个 task/milestone/decision 以 `Task ID` 字段存储稳定 key
  - task：`<task_id>`
  - decision：`DECISION:<REQ-xxxx>`
  - milestone：`MILESTONE:<MS-xxx>`

## 认证与最小权限

- 推荐：GitHub CLI OAuth（`gh auth login`）+ 运行时通过 `GITHUB_TOKEN="$(gh auth token -h github.com)"` 注入
- 禁止：token 入库（仅 `.env.example` 可入库）
- 参考最小权限：见 `docs/SECURITY.md`（Projects 相关通常需 `project`；按 org/repo 类型补充）

## 常见问题排查

- `panel sync` dry-run 正常但真实同步失败：
  - 检查 `mapping.yaml` 是否填了 `owner/project_number` 或 `project_node_id`
  - 检查 token 是否可用：`gh auth status` 或环境变量 `GITHUB_TOKEN`
  - 先执行 `--full` 让字段按名创建/复用
- 视图过滤不生效：
  - `Workstreams` 为 TEXT（逗号分隔），使用 contains/filter
- 同步太频繁导致 rate limit：
  - 关闭自动同步或增加 `TEAMOS_PANEL_GH_SYNC_INTERVAL_SEC`

