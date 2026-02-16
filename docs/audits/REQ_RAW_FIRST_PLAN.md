# 需求处理协议 v2（Raw-First）实施计划（变更设计稿）

生成时间：2026-02-17

## 1. 目标与约束回顾

目标：当用户通过 `teamos CLI / Control Plane API / chat` 输入任何“需求”时，系统必须 **先逐字落盘原始输入（Raw‑First）**，再生成/更新 **Expanded Requirements**。若已有 Expanded，则必须先校验：

1. Baseline Drift Check：Expanded 是否与 Baseline（原始需求基线）发生漂移/背离
2. New‑Input Conflict Check：本次新增需求是否与现有 Expanded 冲突/覆盖/互斥

约束：必须兼容现有 Team OS 机制（Workspace 隔离、Repo Purity、Projects 面板、集群 leader-only 写入、断点恢复、OAuth/Codex）。

## 2. 现状扫描（v1）

### 2.1 当前需求存储

- Team‑OS 自身需求（scope=teamos）：
  - `docs/teamos/requirements/requirements.yaml`
  - `docs/teamos/requirements/REQUIREMENTS.md`
  - `docs/teamos/requirements/CHANGELOG.md`
  - `docs/teamos/requirements/conflicts/`
- 项目需求（scope=project:<id>，Workspace）：
  - `<WORKSPACE>/projects/<id>/state/requirements/requirements.yaml`
  - `<WORKSPACE>/projects/<id>/state/requirements/REQUIREMENTS.md`
  - `<WORKSPACE>/projects/<id>/state/requirements/CHANGELOG.md`
  - `<WORKSPACE>/projects/<id>/state/requirements/conflicts/`

### 2.2 当前写入逻辑（Control Plane）

模板控制平面逻辑位于：

- `.team-os/templates/runtime/orchestrator/app/requirements_store.py`
  - `add_requirement(...)`：写入 `requirements.yaml`/`REQUIREMENTS.md`/`CHANGELOG.md`，并生成 conflict report
  - `req_conflict.py`：提供 duplicate/conflict 的规则检测 + workstream 推断
  - 可选语义检查：通过 `codex exec` + schema（`requirement_distill_and_classify.schema.json`）
- `.team-os/templates/runtime/orchestrator/app/main.py`
  - `/v1/chat` NEW_REQUIREMENT -> `_handle_new_requirement` -> `add_requirement`
  - `/v1/requirements` POST/GET（旧接口）

### 2.3 已有缺口（与 Raw‑First v2 不一致）

- 缺少 Baseline 目录与版本化：
  - 无 `baseline/original_description_v1.md`（不可覆盖，只能追加 v2/v3...）
- 缺少 Raw Inputs 逐字留存：
  - 无 `raw_inputs.jsonl`（append-only、可审计）
- 缺少 Drift Check：
  - Expanded 可能被手工改写并悄然背离；缺少可检测/可修复/可阻断机制
- 缺少严格的 v2 流程顺序保证：
  - 现有逻辑是“直接写 Expanded”并记录 changelog，并不保证 Raw‑First
- CLI/API 缺少 `scope` 统一：
  - 当前 CLI 用 `--project` + `project_id`；v2 要求显式 `--scope teamos|project:<id>`
- leader-only 写入门禁未覆盖需求写入：
  - 当前需求写入未强制要求 Brain/leader（需要补齐 409 + leader redirect）

## 3. 设计（v2）

### 3.1 统一 Scope

- `scope=teamos`：允许写入 repo 内 `docs/teamos/requirements/`
- `scope=project:<id>`：必须写入 Workspace `.../projects/<id>/state/requirements/`
- 兼容旧接口：
  - `/v1/requirements`（旧）映射为 `/v1/requirements/add`（scope=project:<project_id>，或 project_id==teamos -> scope=teamos）

### 3.2 目标目录结构（新增）

对每个 scope 的 requirements 根目录新增：

- `baseline/`
  - `original_description_v1.md`
  - `original_description_v2.md`（需要理由 + NEED_PM_DECISION）
- `raw_inputs.jsonl`（append-only）

并保持：

- `requirements.yaml`（Expanded 真相源）
- `REQUIREMENTS.md`（由 YAML 决定性渲染）
- `CHANGELOG.md`
- `conflicts/`

### 3.3 v2 核心流程（写入端）

当收到 NEW_REQUIREMENT / req add：

1. 解析 scope，定位 requirements 根目录（Repo vs Workspace）
2. Raw‑First：先 append `raw_inputs.jsonl` 并校验 schema
3. Baseline 确保：
   - baseline v1 不存在：用本次输入初始化 v1（或 legacy 提示）
   - baseline set-v2：只新增 v2，并创建 NEED_PM_DECISION 决策项
4. Drift Check（fix 模式）：
   - 结构/渲染一致性：requirements.yaml 合规、REQUIREMENTS.md 与渲染一致
   - baseline 元信息一致（hash/version）
   - 无法自动修复则进入 NEED_PM_DECISION 并停止新增拓展
5. New-Input Conflict Check：
   - DUPLICATE/CONFLICT/COMPATIBLE
6. COMPATIBLE 时拓展 Expanded（追加新 REQ 条目），并写 MD/CHANGELOG
7. Post-Check：
   - 再跑 drift/conflict（check 模式）
8. 触发联动：
   - Panel sync（debounce + leader-only）
   - Telemetry events（requirement_added/conflict/drift/...）

### 3.4 LLM 使用策略

- 默认规则初筛必须可离线运行
- 若 `codex login status` OK，则可用 `codex exec` 做：
  - 需求结构化提炼
  - 语义冲突判定补充
  - drift 自动修正建议
- LLM 不可用时：
  - 不阻塞 Raw Input 落盘
  - 对高风险主题（OAuth/API key/公网暴露/docker.sock 等）进入 NEED_PM_DECISION

## 4. 变更范围（实现清单）

### 4.1 新增（repo 内）

- `.team-os/schemas/requirement_raw_input.schema.json`
- `.team-os/schemas/requirements.schema.json`
- `.team-os/scripts/requirements/*`（脚本入口，调用模板控制平面实现，保证可回归）

### 4.2 修改（Control Plane 模板）

- `.team-os/templates/runtime/orchestrator/app/requirements_store.py`
  - 增加 Raw‑First + Baseline + Drift/Conflict 的 v2 pipeline（保持旧 `add_requirement` 兼容或作为 wrapper）
- `.team-os/templates/runtime/orchestrator/app/main.py`
  - 新增 endpoints：
    - `POST /v1/requirements/add`
    - `POST /v1/requirements/import`
    - `GET  /v1/requirements/show`
    - `POST /v1/requirements/verify`
  - 为需求写入增加 leader-only 门禁（非 leader 返回 409 + leader 信息）
  - 保持 `/v1/requirements` 旧接口可用（内部转发）

### 4.3 修改（CLI）

- `teamos req add`：
  - 新增 `--scope`（默认从 `default_project_id` 推导；`teamos` 自动映射为 scope=teamos）
- 新增命令：
  - `teamos req import --file ... --scope ...`
  - `teamos req verify --scope ...`
  - `teamos req rebuild --scope ...`
  - `teamos req baseline show|set-v2 --scope ...`
- 增强 HTTP 409 leader redirect：
  - CLI 遇到 409 且返回 JSON 包含 `leader_base_url` 时，自动重试到 leader

### 4.4 修改（Workspace scaffold）

- `workspace_store.ensure_project_scaffold` 与 CLI `workspace init`：
  - 创建 `state/requirements/baseline/` 目录（幂等）

### 4.5 文档与治理

- `docs/EXECUTION_RUNBOOK.md`：新增 Raw‑First v2 章节（scope=teamos vs project）
- `docs/GOVERNANCE.md`：Baseline 不可覆盖、冲突处理/NEED_PM_DECISION
- `AGENTS.md`：禁止手改 Expanded（会被 rebuild 覆盖并记录）、需求输入必须 Raw‑First

### 4.6 测试（unittest）

新增 `evals/test_requirements_raw_first.py` 覆盖：

- Raw‑First append-only
- baseline v1 init / v2 只新增
- drift check（MD 与 YAML 不一致）-> verify fail + add flow fix/阻断
- conflict check -> conflict report + NEED_PM_DECISION
- compatible -> 追加 req + 渲染 + changelog 引用 raw timestamp
- project scope 路径必须在 Workspace（Repo Purity PASS）

## 5. 风险与回滚

- 风险：现存 `requirements.yaml` 没有 baseline/raw_inputs，首次启用 v2 可能需要自动补齐“legacy baseline”以避免阻断。
- 回滚：
  - teamos scope 可依赖 git 历史回滚
  - project scope 在 workspace 侧写入 `history/` 以便人工回退（不影响 repo purity）

