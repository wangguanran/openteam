# 仓库创建、Bootstrap 与 Upgrade（非空仓库先理解架构闸门）

本手册定义两类新任务的仓库策略，并明确“高风险闸门”。

## 场景 1：未指定 repo（自动创建新仓库）

目标：允许你只给出任务标题与基本参数，系统自动创建一个新的 GitHub 仓库并 bootstrap Team OS 结构。

规则：

- 创建仓库属于高风险动作，必须审批后执行
- 默认创建 private（可配置）
- 创建后必须初始化：
  - `.team-os/`（roles/workflows/kb/memory/ledger/logs/templates）
  - `docs/requirements/**`（需求主事实源）
  - `docs/plan/**`（里程碑/roadmap overlay）
  - GitHub Projects 面板绑定与同步（视图层）

预期命令（待实现）：

```bash
teamos task new --title "..." --create-repo --org <org?> --private --workstreams "backend,ai,web"
```

## 场景 2：指定 repo 且非空（Upgrade 模式）

目标：对已有代码库进行开发前，必须先“读代码理解架构”，通过闸门后才能进入 Delivery 修改代码。

### Repo Understanding 闸门（必须）

在任何代码变更前，必须生成架构理解文档（落盘到目标项目仓库）：

- 推荐路径：`docs/team_os/REPO_UNDERSTANDING.md`

内容必须包含：

- 总体架构与模块边界（目录结构、关键模块）
- 构建方式（命令、依赖、环境要求）
- 测试命令（unit/integration/e2e）
- 风险点与改动建议（兼容性、依赖、回滚）
- 证据引用
  - 关键文件路径
  - 命令输出（例如 `tree` / `rg` / `cat` / `npm test` / `pytest`）

通过闸门的标准（DoD）：

- 文档完整且可复现
- Reviewer 审查通过（含安全审查项）
- 若缺少关键信息（无法构建/测试），必须把任务状态置为 `BLOCKED` 并明确阻塞点

### 为什么必须先理解架构？

- 防止盲改导致回归
- 提前暴露构建/测试/依赖风险
- 为多机协作（编译机/设备机）建立可执行的分工边界

## 变更记录与审计

- 任何 repo 操作必须写入任务日志与 `metrics.jsonl`（控制平面运行时负责）
- GitHub Projects 面板只作为视图层，必须支持从真相源全量重建

