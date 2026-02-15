# 来源摘要：GitHub Projects v2 (GraphQL) 用于面板同步

- 日期：2026-02-15
- 主题：GitHub Projects v2 作为 Team OS 面板（视图层），通过 GraphQL 创建/更新字段与 items（draft issue），并更新自定义字段值
- 来源：
  - GitHub GraphQL API endpoint: https://api.github.com/graphql
  - 复现工具：GitHub CLI `gh api graphql`（使用本机 OAuth 登录态，不在仓库落盘）

## 摘要

Team OS 的 Projects 面板同步需要通过 GitHub Projects v2 的 GraphQL API 实现以下能力：

- 通过 `projectV2(number: ...)` 获取 Project v2 的 node id、url 等元数据
- 读取 Project 字段列表（fields），并按需创建缺失的自定义字段（custom fields）
- 向 Project 添加 item（MVP 使用 `draft issue` item），并更新 item 的自定义字段值（text/number/date/single select）

## 关键事实与实现要点（用于落盘）

- Projects v2 的自定义字段类型常用为：`TEXT` / `NUMBER` / `DATE` / `SINGLE_SELECT`（另有 iteration 等，但本 MVP 不依赖）。
- Projects v2 没有通用的 “boolean field”/“multi-select field”：
  - `Need PM Decision` 用 `SINGLE_SELECT` (Yes/No) 表示
  - `Workstreams` 用 `TEXT`（逗号分隔）表示，便于 contains 过滤
- `DATE` 字段是日期（`YYYY-MM-DD`），不包含时间；`Last Heartbeat` 用 `TEXT` 存 ISO-8601。
- 为实现“可全量重建/重同步”，必须有稳定主键：
  - 本实现使用自定义字段 `Task ID` 存储稳定 key
  - decision/milestone 使用前缀 key：`DECISION:<REQ>` / `MILESTONE:<MS>`

## 可复现查询（示例）

> 下面命令用于验证/调试（不会输出 token）。你需要先 `gh auth login`。

查询一个 Project v2（按 owner + number）：

```bash
gh api graphql -f query='
query($owner:String!, $number:Int!) {
  user(login:$owner) { projectV2(number:$number) { id number title url } }
}' -F owner="<your-user>" -F number=1
```

读取字段列表（fields）：

```bash
gh api graphql -f query='
query($projectId:ID!) {
  node(id:$projectId) {
    ... on ProjectV2 {
      fields(first:100) { nodes { __typename id name dataType } }
    }
  }
}' -F projectId="<project-node-id>"
```

