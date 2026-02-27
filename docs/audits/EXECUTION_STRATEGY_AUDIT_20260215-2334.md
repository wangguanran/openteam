# Team OS Execution Strategy Audit (20260215-2334 UTC)

> 目标：对 Team OS “执行策略/机制/约束”做全量合规审计（事无巨细），并给出可回归验证的修复方案。  
> 本文件为 **Step 1 初稿**：只做扫描与判定，不实施修复；后续步骤会在同一文件中更新 FAIL -> PASS/WAIVED，并补充提交摘要与下一轮改进计划。

## 审计范围与证据

- 仓库：`/Users/wangguanran/OpenTeam/team-os`
- 分支：`main`
- HEAD：`a3c02a9`
- 本机：macOS，Python `3.9.6`，`pytest` 未安装
- 控制平面（本机 runtime）：`http://127.0.0.1:8787`

### 低风险证据命令（已执行）

```bash
cd /Users/wangguanran/OpenTeam/team-os
git status --porcelain
git rev-parse --abbrev-ref HEAD
git log -5 --oneline
python3 -V
python3 -m pip --version
pytest --version   # not installed
./teamos --help
curl -fsS http://127.0.0.1:8787/v1/status
./teamos --profile local doctor
```

## 总体结论（初稿）

- PASS：基础仓库骨架、项目 requirements 框架、面板同步（GitHub Projects view-layer）已具备雏形；Control Plane 基础端点可用。
- FAIL（高优先级）：**缺少“运行 teamos 即触发”的自我优化闭环**、缺少 telemetry/metrics 体系、缺少 workflow trunk+plugins 与 evolution policy、Control Plane/CLI 缺失大量集群/恢复/任务新建端点与命令、`.gitignore` 未覆盖 tokens/credentials 等关键模式、缺少 `evals/` 回归入口。

---

# Checklist 审计结果（初稿）

## A) 仓库结构与基础规范

### A1. 核心规范文件存在且自洽 — PASS

- Evidence:
  - `AGENTS.md`, `TEAMOS.md`, `docs/EXECUTION_RUNBOOK.md`, `docs/SECURITY.md`, `docs/GOVERNANCE.md` 均存在。

### A2. 必须目录齐全 — FAIL

- Evidence:
  - `.team-os/*` 关键目录存在（roles/workflows/kb/memory/ledger/logs/templates/scripts/state/integrations/cluster）。
  - `prompt-library/` 存在。
  - `evals/` **不存在**。
- Gap:
  - 缺少 `evals/`：无法提供“自我升级 PR 必跑回归”的最低落盘与执行入口。
- Impact:
  - K4（自我优化必须可回归验证）无法达成；自我升级不可持续。
- Fix (proposed):
  - 新增 `evals/`，至少包含：
    - `evals/README.md`（约定、如何运行、DoD）
    - `evals/smoke_self_improve.sh`（最小回归：doctor/self-improve dry-run/status）
    - `evals/test_requirements_conflict_detection.py`（或 `unittest` 等价测试）
- Acceptance:
  - `./evals/smoke_self_improve.sh` 在本机可运行并产生证据落盘（proposal/audit/telemetry）。
- Files:
  - `evals/*`

### A3. .gitignore 覆盖 secrets/auth/credentials 等 — FAIL

- Evidence:
  - `.gitignore` 已忽略：`.env*`, `.codex/`, `auth.json`, `.team-os/state/*.db`, `.team-os/cluster/state/*`, `.team-os/ledger/conversations/`。
- Gap:
  - 未覆盖：`*_token*`、`*credentials*`、`id_rsa*`、`known_hosts`、`sshpass` 临时文件、`*.pem`、`*.p12`、`*.key`、`*.crt`、`*.cer`、`.secrets/`、临时工作区等常见敏感/凭证落盘模式。
- Impact:
  - 违反“禁止 secrets 入库”硬约束（1），增加误提交风险。
- Fix (proposed):
  - 扩充 `.gitignore`，并在 `docs/SECURITY.md` 增补“本地凭证落盘位置与忽略清单”。
- Acceptance:
  - `git status` 不会显示上述敏感文件；提供 `teamos doctor` secret-scan（轻量 regex）提示。
- Files:
  - `.gitignore`, `docs/SECURITY.md`, `teamos`（doctor 扩展）

## B) 角色（Role）与工作流（Workflow）的可进化机制

### B1. 角色是“能力契约”且字段齐全 — FAIL

- Evidence:
  - `roles/*.md` 存在 YAML frontmatter（role_id/version/permissions 等），但缺少 checklist 要求的完整契约字段集合。
- Gap:
  - 缺少/不统一：`scope/non_scope`、`capability_tags`、`tools_allowed`、`quality_gates`、`handoff_rules`、`metrics_required`、`memory_policy`、`risk_policy`。
- Impact:
  - 无法自动路由/拆分/验收；无法与集群 capability/required_capabilities 对齐。
- Fix (proposed):
  - 定义 role schema（文档 + 简易校验脚本），并将现有角色按最小字段补齐。
  - 更新 `templates/role.md` 作为唯一模板源。
- Acceptance:
  - 新增 `teamos doctor` 检查：roles frontmatter 必含字段；缺失则 FAIL。
- Files:
  - `roles/*.md`, `templates/role.md`, `teamos`, `docs/GOVERNANCE.md`

### B2. Role Registry 足够细 + ROLE_TAXONOMY.yaml — FAIL

- Gap:
  - 缺少 `ROLE_TAXONOMY.yaml`；无法表达 driver.camera/lcd/sensor 等层级扩展。
- Fix (proposed):
  - 新增 `roles/ROLE_TAXONOMY.yaml`（含示例层级、capability_tags 约束、扩展规则）。
- Acceptance:
  - `teamos doctor` 能读取并校验 taxonomy 与 role_id 一致性（子角色可选）。
- Files:
  - `roles/ROLE_TAXONOMY.yaml`, `teamos`

### B3. 工作流必须“主干 + 插件” — FAIL

- Evidence:
  - `workflows/*.yaml` 仅有 Genesis/Discovery/Delivery/Incident/Self-Improve 单文件。
- Gap:
  - 缺少 trunk_stages + plugins 结构；缺少 `workflows/plugins/`；缺少触发条件/闸门插件化落盘。
- Fix (proposed):
  - 引入 `workflows/trunk.yaml` 与 `workflows/plugins/*.yaml`（至少包含 `repo_understanding` 插件、`risk_gate` 插件）。
- Acceptance:
  - `teamos doctor` 校验 trunk+plugins 结构存在；control plane `/v1/tasks/new` 在 upgrade 模式强制触发插件（见 H/I）。
- Files:
  - `workflows/*`

### B4. evolution_policy.yaml 存在并可配置 — FAIL

- Gap:
  - 缺少 `policies/evolution_policy.yaml`。
- Fix (proposed):
  - 新增政策文件，定义复杂度向量（D/M/A/R/U/T/E/F）、拆分触发器、新增角色触发器、规则优先级。
- Acceptance:
  - `teamos self-improve --dry-run` 能引用该政策生成 ROLE_SPLIT/PLUGIN_ADD/GATE_TUNE 建议。
- Files:
  - `policies/evolution_policy.yaml`, self-improve 实现（CLI/CP）

### B5. metrics 收集/分析脚本存在 — FAIL

- Gap:
  - 缺少 `scripts/metrics/collect_from_logs.py`、`analyze_evolution.py` 等。
- Fix (proposed):
  - 新增 metrics 脚本（纯本地、可离线），生成 self_improve 提案。
- Acceptance:
  - `teamos metrics analyze` 输出改进候选并落盘为 proposal（见 K）。
- Files:
  - `scripts/metrics/*`, `teamos`

## C) 任务台账/日志/遥测（任务×阶段×角色）

### C1. 每任务必须有 00~07 + metrics.jsonl — FAIL

- Evidence:
  - `.team-os/logs/tasks/*` 中多数任务仅有 `00~02`，且无 `metrics.jsonl`。
- Gap:
  - 任务全流程日志与 metrics 未强制生成；无法追溯与分析。
- Fix (proposed):
  - 更新任务模板/创建脚本：强制生成 `00~07` + `metrics.jsonl`（空文件也要落盘）。
  - 为历史任务补齐缺失日志文件（仅追加，不覆盖已有内容）。
- Acceptance:
  - `teamos doctor` 检查所有 OPEN/DOING/BLOCKED 任务具备完整日志与 metrics。
- Files:
  - `templates/task_log_*.md`, `templates/task_ledger.yaml`, `scripts/new_task.sh`（或等价）、`teamos`

### C2. telemetry schema 存在且 metrics.jsonl 符合 — FAIL

- Gap:
  - 缺少 `schemas/telemetry_event.schema.json`；无法校验 events。
- Fix (proposed):
  - 新增 schema；self-improve/requirements/panel sync/agent heartbeat 等事件写入 metrics.jsonl 并通过 schema 校验。
- Acceptance:
  - `teamos metrics check` 能校验每个 task 的 metrics.jsonl。
- Files:
  - `schemas/telemetry_event.schema.json`, metrics 写入代码（CP/CLI）

### C3. teamos 提供 metrics-check/analyze 命令 + 关闭闸门 — FAIL

- Gap:
  - CLI 无 metrics 子命令；任务关闭前无强制校验。
- Fix (proposed):
  - `teamos metrics check|analyze`；在 `teamos task close`（待新增）或 control plane close gate 中拦截缺失指标。
- Acceptance:
  - 关闭任务前必须通过 metrics_required（role/workflow 定义）。
- Files:
  - `teamos`, role/workflow 定义、metrics 脚本

## D) 需求登记与冲突检测（项目级与 Team‑OS 自身）

### D1. 每项目 requirements 四件套存在 — PASS

- Evidence:
  - `docs/requirements/DEMO|demo_panel|teamos/` 均包含：`requirements.yaml`、`REQUIREMENTS.md`、`CHANGELOG.md`、`conflicts/`。

### D2. 新增需求必须先冲突检测并生成报告 — PASS (code-present)

- Evidence:
  - 控制平面包含 `req_conflict.py` / `requirements_store.py`，且 `docs/requirements/*/conflicts/*.md` 已存在历史冲突报告。
- Risk/Note:
  - 需要补充单元测试覆盖“DUPLICATE/CONFLICT/COMPATIBLE”三类分支（见 A2/C2）。

### D3. Team‑OS 自身 requirements 真相源存在 — PASS (equivalent)

- Evidence:
  - Team‑OS 作为 project_id=`teamos` 的 requirements 真相源位于：`docs/requirements/teamos/requirements.yaml`（等价于 `docs/teamos/requirements.yaml` 要求）。
- Follow-up:
  - 在 `TEAMOS.md`/Runbook 中显式声明：`docs/requirements/<project_id>/requirements.yaml` 为统一真相源路径。

## E) Control Plane API 与 teamos CLI 合规

### E1. Control Plane 端点齐全 — FAIL

- Evidence:
  - 存在（可达）：`/v1/status`, `/v1/agents`, `/v1/tasks`, `/v1/focus (GET/POST)`, `/v1/chat (POST)`, `/v1/requirements (GET/POST, 需 project_id)`, `/v1/panel/github/*`。
  - 缺失（404）：`/v1/cluster/*`, `/v1/nodes/*`, `/v1/tasks/new`, `/v1/recovery/*`, `/v1/self_improve/run`。
- Gap:
  - 集群选主/节点心跳/任务新建与 repo 策略/恢复续跑/self-improve 执行器均未落地为 API。
- Impact:
  - 无法满足集群协作、断点续跑、自动 repo bootstrap/upgrade gate、自我升级的强制闭环。
- Fix (proposed):
  - 在现有 FastAPI 服务中增量加入缺失 endpoints（可先 MVP：dry_run + 明确 501/blocked gate），并落盘 state 到 `.team-os/cluster/state/` 与 runtime DB。
- Acceptance:
  - `curl` 访问上述端点均返回 200/明确错误码（非 404）；dry_run 可离线演示。
- Files:
  - `templates/runtime/orchestrator/app/main.py`（或拆分模块）、`runtime_db.py`, `state_store.py`, `docs/*`

### E2. teamos CLI 命令覆盖完整 — FAIL

- Evidence:
  - 当前 CLI 命令：`config/status/focus/agents/tasks/panel/chat/req/doctor`。
- Gap:
  - 缺少：`self-improve`、`cluster status`、`node add/join-script`、`repo create`、`task new/resume`、`metrics check/analyze` 等。
- Fix (proposed):
  - 扩展 `./teamos` 子命令与参数；保持无依赖（argparse + urllib）原则。
- Acceptance:
  - `./teamos --help` 显示所有必需命令；每条命令至少支持 dry-run。
- Files:
  - `teamos`

### E3. OAuth 默认且必须拦截未登录 — FAIL (partial)

- Evidence:
  - `./teamos --profile local doctor` 可检查 `codex login status` 并输出已登录。
- Gap:
  - doctor 未检查 `gh auth status`；LLM/写面板/写需求等动作未统一走 OAuth gate（leader-only + approvals）。
- Fix (proposed):
  - 统一 gate：control plane self-improve 与 panel sync（real）前必须检查 codex/gh 登录状态；未登录写 telemetry event 并返回可执行修复步骤。
- Acceptance:
  - 未登录时任何 LLM 相关调用返回明确错误并记录事件；`teamos doctor` 覆盖 codex+gh。
- Files:
  - `teamos`, control plane auth/gate 模块, `docs/AUTH.md`

## F) GitHub Projects 面板（主面板）与“一次性写入”

### F1. mapping 完整（含 req_id↔issue_id↔item_id）— FAIL

- Evidence:
  - `integrations/github_projects/mapping.yaml` 存在并绑定 `teamos -> projects/3`。
- Gap:
  - mapping 未显式记录 `req_id`/`issue_id`/`item_id` 的可追溯映射（需要落盘到 truth-source 或 runtime DB，并可全量重建）。
- Fix (proposed):
  - 增补 mapping 策略 + 在 runtime DB `panel_kv` 保存 resolved ids（可重建），并提供导出到 `.team-os/ledger/panel_sync/` 的审计快照。
- Acceptance:
  - full sync 可在空项目面板上重建 items；dry-run 输出稳定的 key 列表。
- Files:
  - `integrations/github_projects/mapping.yaml`, `panel_github_sync.py`, `runtime_db.py`

### F2. 可从真相源全量重建/重同步（幂等）— PASS (tasks/decisions/milestones)

- Evidence:
  - `panel_github_sync.py` 以 `Task ID` 字段作为 stable key 读取现有 items 并 upsert；重复 sync 不重复创建同 key item。

### F3. Self‑Improve Roadmap 写入 Team‑OS Projects — FAIL

- Gap:
  - 缺少 Team‑OS 自我升级 Roadmap 的自动生成与同步（K2/K4/F3）。
- Fix (proposed):
  - Self‑Improve 产出的 proposals 写入 `docs/requirements/teamos/requirements.yaml` 并 sync 到 `projects/3`（可标记 Capability/Maturity 字段）。
- Acceptance:
  - `teamos self-improve --dry-run` 生成 ≥3 改进项草案并可被 `panel sync --dry-run` 展示；真实 sync 需 gh auth + 用户批准（写远端）。
- Files:
  - self-improve 实现、panel sync 扩展、requirements（teamos）

## G) 多机协作集群（GitHub‑Only 最低可用）

### G1. leader lease + nodes registry（GitHub Issues 总线）— FAIL

- Evidence:
  - 已有文档与 config 骨架：`docs/CLUSTER_RUNBOOK.md`, `.team-os/cluster/config.yaml`。
- Gap:
  - 缺少实际实现：CLUSTER-LEADER/CLUSTER-NODES issue 约定、续租/接管、nodes comment 心跳编辑、leader-only 写隔离。
- Fix (proposed):
  - 实现 GitHub bus（优先 `gh` CLI 或 GitHub API），并在 control plane 提供 `/v1/cluster/*` `/v1/nodes/*`。
- Acceptance:
  - 两个节点启动后：cluster status 可见 leader/nodes；leader 过期 assistant 可接管并落盘 recovery 记录。
- Files:
  - control plane cluster 模块、`docs/CLUSTER_RUNBOOK.md`, `teamos`

### G2. Brain 掉线 assistant 接管与恢复序列落盘 — FAIL

- Gap:
  - 缺少 `.team-os/cluster/state/recovery_*.md` 自动生成机制与 API 触发点。
- Fix/Acceptance:
  - 见 I（recovery scan/resume）与 cluster 接管实现。

### G3. Task lease（GitHub task issues frontmatter）— FAIL

- Gap:
  - 未实现 task issue frontmatter + lease 续租/接管。

### G4. 异构能力协作（capabilities 生效与拆子任务）— FAIL

- Gap:
  - 缺少 capability 注册与 required_capabilities 路由/分配/阻塞关系落盘。

## H) 仓库处理策略（repo 可选 + 自动创建 + upgrade 理解闸门）

### H1. 未指定 repo 自动创建（gh cli）— FAIL

- Gap:
  - 未实现 `/v1/tasks/new` 与 `teamos task new --create-repo`；缺少“创建 repo 属高风险需批准”的闸门实现。

### H2. 非空 repo upgrade 前必须 Repo Understanding Gate — FAIL

- Evidence:
  - 已有文档：`docs/REPO_BOOTSTRAP_AND_UPGRADE.md`。
- Gap:
  - 缺少模板 `templates/repo_understanding.md` 与 workflow plugin `workflows/plugins/repo_understanding.yaml` 与强制执行点。

### H3. 空仓库 bootstrap — FAIL

- Gap:
  - 未实现 bootstrap 生成骨架、CI/test 占位、requirements/plan/workstreams 初始化流程。

## I) 中断恢复/断点续跑

### I1. 控制平面启动自动 scan 未完成任务并恢复 — FAIL

- Gap:
  - 缺少 `/v1/recovery/scan`、启动时自动扫描机制、停在闸门（WAITING_APPROVAL/NEED_PM_DECISION）。

### I2. ledger 必含 repo/workdir/branch/mode/checkpoint/recovery — FAIL

- Gap:
  - 现有 ledger 未强制这些字段；模板需扩展。

### I3. 恢复过程必须落盘 + telemetry — FAIL

- Gap:
  - 无恢复日志落盘与事件 schema。

## J) 共享中枢 DB（Postgres/Redis）接入

### J1/J2/J3 — FAIL (sqlite-only)

- Evidence:
  - 当前 runtime DB 仅支持 sqlite（`runtime_db.py`），未支持 `TEAMOS_DB_URL`/`TEAMOS_REDIS_URL`。
- Fix (proposed):
  - 引入 DB 抽象：默认 sqlite；当 `TEAMOS_DB_URL` 为 postgres 时启用（最小：agent_registry/events/panel_sync_runs）。
  - doctor 增加连通性校验与最小权限提示。
- Acceptance:
  - 无 DB 时 sqlite fallback 生效；配置 DB 时能通过 doctor 并写入数据。

## K) “运行 teamos 即触发自我优化升级”

### K1. 任意 teamos 命令触发 self-improve scheduler（异步、debounce）— FAIL

- Gap:
  - 当前 CLI 不会自动触发 self-improve；缺少 scheduler 与落盘证据。

### K2. 无项目也能自我优化（生成 ≥3 改进项）— FAIL

- Gap:
  - 缺少扫描器：文档缺口/工程缺口/安全缺口/日志缺口/面板映射缺口等。

### K3. 节流（6 小时 debounce）+ 可手动 force — FAIL

### K4. 可回归验证（evals/PR gate）— FAIL

- Fix (proposed, summary):
  - 新增 Self‑Improve Scheduler：
    - CLI：新增 `teamos self-improve`（dry-run/force）+ “任何命令前后唤醒调度器”（不阻塞）。
    - Control Plane：新增 `/v1/self_improve/run`（dry_run 支持）+ 落盘：
      - `.team-os/ledger/self_improve/<ts>-proposal.md`
      - `docs/audits/EXECUTION_STRATEGY_AUDIT_*.md`（本文件更新）
      - pending issues：`.team-os/ledger/team_os_issues_pending/`
    - 面板：将 teamos requirements/proposals 同步到 `projects/3`（真实写远端需 gh auth + 用户批准）。
- Acceptance:
  - `./teamos self-improve --dry-run` 生成 ≥3 改进项并落盘。
  - 任意 `./teamos status`/`./teamos doctor` 触发 scheduler，可在 metrics/ledger 中看到事件证据。

---

# Step 1 输出物（本文件）之外的“待修复清单（初稿）”

- 需要实现/补齐的关键能力（按优先级）：
  - K: Always-on Self‑Improve（scheduler + API + CLI + debounce + artifacts + panel sync）
  - C: telemetry schema + metrics.jsonl + metrics 命令与 gate
  - E/G/H/I: cluster/nodes/task-new/recovery/self-improve 端点与 CLI 命令
  - B: roles 契约化 + taxonomy + workflow trunk+plugins + evolution policy
  - A3: .gitignore 扩充
  - A2: `evals/` 回归入口

# Step 2 修复计划（最小变更但机制完整）

> 原则：不做无关重构；新增能力优先“可运行 + 可验证 + 可追溯 + 可演进”。  
> 远端写操作（GitHub Issues/Projects、创建 repo 等）默认 **dry-run**；仅在显式批准/配置允许时执行真实写入。

1) 基础与回归入口（A2/A3）
- 变更：新增 `evals/`；补齐 `.gitignore` 敏感模式；补 `tests/__init__.py` 让 `unittest discover` 生效。
- 验证：
  - `python3 -m unittest discover -q` 发现并运行 tests
  - `./evals/smoke_self_improve.sh` 通过
- 风险：低（新增文件与忽略规则）。

2) Telemetry/Metrics 体系（C1/C2/C3 + B5）
- 变更：新增 `schemas/telemetry_event.schema.json`；新增 `scripts/metrics/*`；任务模板与 new-task 逻辑补齐 `00~07` 与 `metrics.jsonl`；CLI 增加 `metrics check/analyze`。
- 验证：
  - `./teamos metrics check --all`（或等价）通过
  - self-improve 生成 proposal 可引用 metrics 聚类
- 风险：中（会为历史任务补齐缺失文件，必须“只增不覆写”）。

3) Always-on Self‑Improve（K1-K4 + E1/E2/F3）
- 变更：
  - Control Plane：实现 `POST /v1/self_improve/run`（支持 dry_run/force；落盘 proposal/telemetry；可选触发 panel sync dry-run）
  - CLI：新增 `teamos self-improve`；并在任意 `teamos <cmd>` 前/后异步唤醒（debounce=6h，可 `--force`）
  - 落盘：`.team-os/ledger/self_improve/<ts>-proposal.md` + pending issue 草稿
- 验证：
  - `./teamos self-improve --dry-run` 生成 ≥3 改进项（落盘）
  - 任意 `./teamos status` 后能在 ledger/telemetry 中看到 self-improve 触发事件
- 风险：中（涉及后台触发与节流；需避免递归触发）。

4) Role 契约化与 taxonomy（B1/B2）
- 变更：补齐所有 `roles/*.md` frontmatter 字段；新增 `roles/ROLE_TAXONOMY.yaml`；doctor 校验。
- 验证：`./teamos doctor` role-check PASS。
- 风险：低（文档与校验）。

5) Workflow trunk+plugins + evolution policy（B3/B4）
- 变更：新增 `workflows/trunk.yaml` 与 `workflows/plugins/*.yaml`（至少 repo_understanding/risk_gate）；新增 `policies/evolution_policy.yaml`；doctor 校验。
- 验证：`./teamos doctor` workflow-check PASS。
- 风险：低（新增文件；保持旧 workflow YAML 兼容不删除）。

6) Control Plane/CLI 补齐关键端点与命令（E1/E2/G/H/I/J）
- 变更：
  - CP：补 `/v1/cluster/*`, `/v1/nodes/*`, `/v1/tasks/new`, `/v1/recovery/*`（支持 dry-run；leader-only 写隔离；落盘 recovery 记录）
  - CLI：补 `cluster/node/repo/task` 命令（最小可用：dry-run + 明确闸门提示）
  - DB：实现 `TEAMOS_DB_URL` postgres 可选后端（缺依赖则 doctor 提示）；保留 sqlite fallback
- 验证：
  - `curl` 访问端点不再 404
  - `./teamos cluster status` 输出结构化状态
- 风险：中高（涉及 GitHub 协作总线与远端写；默认 dry-run + 需要显式允许）。

7) 文档与治理补齐（A1/F/G/H/I/J/K）
- 变更：更新 `AGENTS.md`/`TEAMOS.md`/Runbook/Security/Governance，确保与实现一致。
- 验证：`./teamos doctor` 输出提供可执行修复步骤与闸门提示。

---

# 本次修复提交摘要（占位）

> Step 1 初稿阶段不包含任何修复提交。  
> 后续 Step 3 完成修复后，在此处补充：commit 列表、关键文件变更摘要、验证命令与输出证据。

# 下一轮改进计划（占位）

> Step 5 会将“Next Improvements”写入 Team‑OS Roadmap（GitHub Projects 或 pending drafts），并在此处列出编号与链接/文件路径。
