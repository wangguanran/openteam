# DEMO-PANEL-0001 Plan

- 定义映射：`.team-os/integrations/github_projects/mapping.yaml`
- dry-run：`./teamos panel sync --project demo --dry-run --full`
- 真实同步（需授权）：`./teamos panel sync --project demo --full`

退出条件：

- dry-run 输出包含 tasks + milestone + decision 的 planned actions

