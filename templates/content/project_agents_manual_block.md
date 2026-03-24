<!-- OPENTEAM_MANUAL_START -->

## Team-OS 项目操作手册（自动注入，勿手工编辑本区块）

- manual_version: {{MANUAL_VERSION}}
- project_id: {{PROJECT_ID}}

### 0) 重要边界（必须遵守）

1. `openteam` 仓库只包含 Team-OS 自身文件；项目真相源在 Workspace（默认：`~/.openteam/workspace`）。
2. 项目 requirements/ledger/logs/prompts/plan 等都在 Workspace：`~/.openteam/workspace/projects/{{PROJECT_ID}}/state/`，不要提交进项目仓库。
3. 本文件只用于指导如何用 Team-OS 操作项目；本区块内容由脚本幂等更新。

### 1) 如何在项目仓库内使用 Team-OS CLI

如果你当前在项目仓库根目录，建议先设置 Team-OS 仓库路径（或把 `openteam` 放入 PATH）：

```bash
export OPENTEAM_REPO_PATH="/path/to/openteam"
$OPENTEAM_REPO_PATH/openteam doctor
```

（注：`openteam` 会用 `--workspace-root` 或默认 `~/.openteam/workspace` 访问项目真相源。）

### 2) 查看/修改项目配置（Workspace 内）

```bash
openteam project config show --project {{PROJECT_ID}}
openteam project config set --project {{PROJECT_ID}} --key panel.project_url --value "https://github.com/orgs/<org>/projects/<n>"
openteam project config validate --project {{PROJECT_ID}}
```

### 3) 增加/校验项目需求（Raw-First）

```bash
openteam req add --scope project:{{PROJECT_ID}} "原始需求文本（Raw）"
openteam req verify --scope project:{{PROJECT_ID}}
```

### 4) 生成/预览项目 Prompt（如启用）

```bash
openteam prompt build --scope project:{{PROJECT_ID}}
openteam prompt diff  --scope project:{{PROJECT_ID}}
```

### 5) 面板（GitHub Projects，先 dry-run）

```bash
openteam panel show --project {{PROJECT_ID}}
openteam panel sync --project {{PROJECT_ID}} --dry-run
openteam panel sync --project {{PROJECT_ID}} --full --dry-run
```

### 6) 项目任务流程（建议）

```bash
openteam task new --scope project:{{PROJECT_ID}} --title "<...>" --workstreams "<...>"
openteam task close <TASK_ID>
```

<!-- OPENTEAM_MANUAL_END -->

