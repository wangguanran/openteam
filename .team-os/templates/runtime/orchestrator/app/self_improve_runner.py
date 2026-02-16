import json
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

import yaml

from .requirements_store import AddReqOutcome, add_requirement_raw_first
from .state_store import team_os_root, teamos_requirements_dir
from . import workspace_store


class SelfImproveError(Exception):
    pass


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _append_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(text)


def _read_yaml(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _write_yaml(p: Path, data: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:60] or "item"


def _debounce_hours(repo_root: Path) -> int:
    pol = _read_yaml(repo_root / ".team-os" / "policies" / "evolution_policy.yaml")
    si = pol.get("self_improve") or {}
    try:
        return int(si.get("debounce_hours") or 6)
    except Exception:
        return 6


def _min_proposals(repo_root: Path) -> int:
    pol = _read_yaml(repo_root / ".team-os" / "policies" / "evolution_policy.yaml")
    si = pol.get("self_improve") or {}
    try:
        return int(si.get("min_proposals_per_run") or 3)
    except Exception:
        return 3


def _self_improve_state_path(repo_root: Path) -> Path:
    return repo_root / ".team-os" / "state" / "self_improve_last_run.json"


def _should_run(repo_root: Path, *, force: bool) -> tuple[bool, str]:
    if force:
        return True, "force"
    p = _self_improve_state_path(repo_root)
    if not p.exists():
        return True, "no_state"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        last = str(data.get("last_run_at") or "").strip()
        if not last:
            return True, "missing_last_run_at"
        import datetime as _dt

        # Accept both ...Z and isoformat with timezone.
        ts = last.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(ts)
        now = _dt.datetime.now(_dt.timezone.utc)
        hours = (now - dt).total_seconds() / 3600.0
        if hours >= float(_debounce_hours(repo_root)):
            return True, f"debounce_elapsed hours={hours:.2f}"
        return False, f"debounced hours={hours:.2f}"
    except Exception as e:
        return True, f"state_parse_error={e}"


def _emit_wake_event(repo_root: Path, *, event_type: str, actor: str, payload: dict[str, Any]) -> None:
    p = repo_root / ".team-os" / "ledger" / "self_improve" / "wake_events.jsonl"
    evt = {
        "ts": _utc_now_iso(),
        "event_type": event_type,
        "actor": actor,
        "project_id": "teamos",
        "workstream_id": "devops",
        "payload": payload,
    }
    _append_text(p, json.dumps(evt, ensure_ascii=False) + "\n")


def _frontmatter_yaml(md_text: str) -> dict[str, Any]:
    if not md_text.startswith("---"):
        return {}
    parts = md_text.split("\n---", 1)
    if len(parts) < 2:
        return {}
    y = parts[0].lstrip("-").strip()
    try:
        return yaml.safe_load(y) or {}
    except Exception:
        return {}


def scan_repo(repo_root: Path, *, api_routes: Optional[list[str]] = None) -> dict[str, Any]:
    # A) Structure
    required_files = [
        "AGENTS.md",
        "TEAMOS.md",
        "docs/EXECUTION_RUNBOOK.md",
        "docs/SECURITY.md",
        "docs/GOVERNANCE.md",
    ]
    required_dirs = [
        ".team-os/roles",
        ".team-os/workflows",
        ".team-os/kb/global",
        ".team-os/kb/roles",
        ".team-os/kb/platforms",
        ".team-os/kb/sources",
        ".team-os/memory/roles",
        ".team-os/ledger/tasks",
        ".team-os/ledger/self_improve",
        ".team-os/ledger/team_os_issues_pending",
        ".team-os/logs/tasks",
        ".team-os/templates",
        ".team-os/scripts",
        "prompt-library",
        "evals",
    ]

    missing_files = [f for f in required_files if not (repo_root / f).exists()]
    missing_dirs = [d for d in required_dirs if not (repo_root / d).exists()]

    # A3) gitignore patterns (best-effort)
    gi = _read_text(repo_root / ".gitignore")
    gi_required = [".env", ".codex/", "auth.json", "*_token*", "*credentials*", ".secrets/", "sshpass*"]
    gi_missing = [x for x in gi_required if x not in gi]

    # A4) Repo purity (governance): Team OS repo must contain ONLY Team OS itself.
    # Any project-scoped truth-source artifacts must live in Workspace (outside this git repo).
    repo_purity_violations: list[str] = []
    if (repo_root / "docs" / "requirements").exists():
        repo_purity_violations.append("docs/requirements exists (project requirements must live in Workspace; teamos self is docs/teamos/requirements)")
    if (repo_root / "prompt-library" / "projects").exists():
        repo_purity_violations.append("prompt-library/projects exists (project prompts must live in Workspace)")
    plan_root = repo_root / "docs" / "plan"
    if plan_root.exists():
        for d in sorted(plan_root.iterdir()):
            if d.is_dir() and d.name != "teamos":
                repo_purity_violations.append(f"docs/plan/{d.name} exists (project plan overlay must live in Workspace)")
    conv_root = repo_root / ".team-os" / "ledger" / "conversations"
    if conv_root.exists():
        for d in sorted(conv_root.iterdir()):
            if d.is_dir() and d.name != "teamos":
                repo_purity_violations.append(f".team-os/ledger/conversations/{d.name} exists (project conversations must live in Workspace)")

    # Non-teamos task ledgers/logs in repo are also violations.
    task_project: dict[str, str] = {}
    tasks_dir2 = repo_root / ".team-os" / "ledger" / "tasks"
    if tasks_dir2.exists():
        for y in sorted(tasks_dir2.glob("*.yaml")):
            data = _read_yaml(y)
            tid = str(data.get("id") or y.stem)
            pid = str(data.get("project_id") or "").strip() or "(missing)"
            task_project[tid] = pid
            if pid != "teamos":
                repo_purity_violations.append(f".team-os/ledger/tasks/{y.name} project_id={pid} (must live in Workspace)")
    logs_dir2 = repo_root / ".team-os" / "logs" / "tasks"
    if logs_dir2.exists():
        for d in sorted(logs_dir2.iterdir()):
            if not d.is_dir():
                continue
            tid = d.name
            pid = task_project.get(tid, "(missing_ledger)")
            if pid != "teamos":
                repo_purity_violations.append(f".team-os/logs/tasks/{tid}/ project_id={pid} (must live in Workspace)")

    runtime_template_mount_missing: list[str] = []
    compose_tpl = repo_root / ".team-os" / "templates" / "runtime" / "docker-compose.yml"
    if compose_tpl.exists():
        text = _read_text(compose_tpl)
        if "TEAMOS_WORKSPACE_ROOT" not in text:
            runtime_template_mount_missing.append("missing env TEAMOS_WORKSPACE_ROOT")
        if "/teamos-workspace" not in text:
            runtime_template_mount_missing.append("missing /teamos-workspace volume mount")

    # B1) roles contract keys
    role_dir = repo_root / ".team-os" / "roles"
    role_files = sorted(role_dir.glob("*.md")) if role_dir.exists() else []
    contract_keys = [
        "scope",
        "non_scope",
        "capability_tags",
        "inputs",
        "outputs",
        "tools_allowed",
        "quality_gates",
        "handoff_rules",
        "metrics_required",
        "memory_policy",
        "risk_policy",
    ]
    roles_missing_keys: dict[str, list[str]] = {}
    for p in role_files:
        fm = _frontmatter_yaml(_read_text(p))
        miss = [k for k in contract_keys if k not in fm]
        if miss:
            roles_missing_keys[p.name] = miss

    # B3/B4) trunk + plugins + evolution policy
    wf_trunk = repo_root / ".team-os" / "workflows" / "trunk.yaml"
    wf_plugins_dir = repo_root / ".team-os" / "workflows" / "plugins"
    wf_missing = []
    if not wf_trunk.exists():
        wf_missing.append(".team-os/workflows/trunk.yaml")
    if not wf_plugins_dir.exists():
        wf_missing.append(".team-os/workflows/plugins/")
    else:
        for need in ["repo_understanding.yaml", "risk_gate.yaml"]:
            if not (wf_plugins_dir / need).exists():
                wf_missing.append(f".team-os/workflows/plugins/{need}")

    pol_missing = []
    if not (repo_root / ".team-os" / "policies" / "evolution_policy.yaml").exists():
        pol_missing.append(".team-os/policies/evolution_policy.yaml")

    # C) telemetry schema + task artifacts
    schema_missing = []
    if not (repo_root / ".team-os" / "schemas" / "telemetry_event.schema.json").exists():
        schema_missing.append(".team-os/schemas/telemetry_event.schema.json")

    tasks_dir = repo_root / ".team-os" / "ledger" / "tasks"
    logs_root = repo_root / ".team-os" / "logs" / "tasks"
    task_artifacts_missing: dict[str, list[str]] = {}
    if tasks_dir.exists():
        for tpath in sorted(tasks_dir.glob("*.yaml")):
            tid = tpath.stem
            ldir = logs_root / tid
            want = [
                "00_intake.md",
                "01_plan.md",
                "02_todo.md",
                "03_work.md",
                "04_test.md",
                "05_release.md",
                "06_observe.md",
                "07_retro.md",
                "metrics.jsonl",
            ]
            miss = []
            for w in want:
                if not (ldir / w).exists():
                    miss.append(w)
            if miss:
                task_artifacts_missing[tid] = miss

    # E1) control plane routes (if provided)
    required_routes = [
        "/v1/status",
        "/v1/agents",
        "/v1/tasks",
        "/v1/focus",
        "/v1/chat",
        "/v1/requirements",
        "/v1/panel/github/sync",
        "/v1/panel/github/health",
        "/v1/panel/github/config",
        "/v1/cluster/status",
        "/v1/cluster/elect/attempt",
        "/v1/nodes",
        "/v1/nodes/register",
        "/v1/nodes/heartbeat",
        "/v1/tasks/new",
        "/v1/recovery/scan",
        "/v1/recovery/resume",
        "/v1/self_improve/run",
    ]
    routes_missing = []
    if api_routes is not None:
        have = set(api_routes)
        routes_missing = [r for r in required_routes if r not in have]

    return {
        "repo_root": str(repo_root),
        "missing_files": missing_files,
        "missing_dirs": missing_dirs,
        "gitignore_missing_patterns": gi_missing,
        "repo_purity_violations": repo_purity_violations,
        "runtime_template_mount_missing": runtime_template_mount_missing,
        "roles_missing_contract_keys": roles_missing_keys,
        "workflow_missing": wf_missing,
        "policy_missing": pol_missing,
        "schema_missing": schema_missing,
        "task_artifacts_missing": task_artifacts_missing,
        "routes_missing": routes_missing,
    }


@dataclass(frozen=True)
class Proposal:
    title: str
    text: str
    priority: str
    workstreams: list[str]
    acceptance: list[str]


def _default_proposals(scan: dict[str, Any], *, min_n: int) -> list[Proposal]:
    props: list[Proposal] = []

    if scan.get("gitignore_missing_patterns"):
        props.append(
            Proposal(
                title="Harden .gitignore for tokens/credentials and self-improve runtime state",
                text="补齐 .gitignore：覆盖 *_token* / *credentials* / ssh keys & certs / sshpass temp / self-improve state 等，防止 secrets 误入库。",
                priority="P0",
                workstreams=["devops", "security"],
                acceptance=[
                    "git status 不出现敏感文件",
                    "doctor 提示本地凭证落盘位置与忽略清单",
                ],
            )
        )

    if scan.get("repo_purity_violations") or scan.get("runtime_template_mount_missing"):
        props.append(
            Proposal(
                title="Enforce Repo vs Workspace separation (keep team-os repo pure) + runtime workspace mounts",
                text="强制 Repo/Workspace 边界：team-os git 仓库只存放 Team OS 自身；所有 project:<id> 的 requirements/ledger/logs/prompts/plan/repo workdir 全部落在 Workspace（默认 ~/.teamos/workspace）。补齐 repo_purity 检查与迁移工具，并确保 runtime 模板挂载 Workspace。",
                priority="P1",
                workstreams=["devops", "process"],
                acceptance=[
                    "repo_purity check PASS (no project artifacts inside repo)",
                    "teamos workspace migrate --from-repo dry-run 能给出迁移计划",
                    "runtime 模板包含 /teamos-workspace 挂载 + TEAMOS_WORKSPACE_ROOT",
                ],
            )
        )

    if scan.get("schema_missing") or scan.get("task_artifacts_missing"):
        props.append(
            Proposal(
                title="Enforce telemetry schema + metrics.jsonl per task (00~07 + metrics)",
                text="为每个任务强制生成 00~07 阶段日志与 metrics.jsonl，并提供 metrics check/analyze 命令；缺失的历史任务补齐空文件但不覆写已有内容。",
                priority="P0",
                workstreams=["devops", "backend"],
                acceptance=[
                    "每个活跃任务目录包含 00~07.md + metrics.jsonl",
                    "metrics check 能发现并报告缺失/格式错误",
                ],
            )
        )

    if scan.get("routes_missing"):
        props.append(
            Proposal(
                title="Complete missing Control Plane endpoints (cluster/nodes/tasks/new/recovery/self_improve)",
                text="补齐控制平面 API：cluster/nodes/lease/recovery/tasks/new/self_improve/run，默认 dry-run + 闸门拦截远端写/高风险动作。",
                priority="P0",
                workstreams=["backend", "devops"],
                acceptance=[
                    "上述端点不再 404",
                    "未配置/未授权时返回可执行修复步骤并记录事件",
                ],
            )
        )

    if scan.get("workflow_missing") or scan.get("policy_missing") or scan.get("roles_missing_contract_keys"):
        props.append(
            Proposal(
                title="Formalize role contracts + trunk/plugins workflow + evolution policy",
                text="补齐 roles 契约字段、ROLE_TAXONOMY、TRUNK+plugins workflow 与 evolution_policy，并在 doctor 中校验。",
                priority="P1",
                workstreams=["devops", "process"],
                acceptance=[
                    "doctor role/workflow checks PASS",
                    "新增角色/插件有清晰扩展方式",
                ],
            )
        )

    # Ensure minimum proposals even when repo is already mostly compliant.
    while len(props) < max(0, int(min_n)):
        n = len(props) + 1
        props.append(
            Proposal(
                title=f"Continuous improvement placeholder #{n}: add higher-signal evals/CI and observability",
                text="补齐 CI/evals 接入与 OpenTelemetry TODO 的可执行落盘计划（不影响本地运行，默认关闭远端写）。",
                priority="P2",
                workstreams=["devops"],
                acceptance=["存在可执行的 evals/CI 入口与文档", "不引入 secrets 入库风险"],
            )
        )

    return props


def _proposal_md(*, ts: str, actor: str, trigger: str, scan: dict[str, Any], outcomes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Self-Improve Proposal")
    lines.append("")
    lines.append(f"- ts: {ts}")
    lines.append(f"- actor: {actor}")
    lines.append(f"- trigger: {trigger}")
    lines.append("")
    lines.append("## Scan Summary")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(scan, ensure_ascii=False, indent=2)[:12000])
    lines.append("```")
    lines.append("")
    lines.append("## Proposals (with outcomes)")
    lines.append("")
    for i, o in enumerate(outcomes, 1):
        lines.append(f"### {i}. {o.get('title')}")
        lines.append("")
        lines.append(f"- priority: {o.get('priority')}")
        lines.append(f"- workstreams: {','.join(o.get('workstreams') or [])}")
        lines.append(f"- outcome: {o.get('outcome')}")
        if o.get("req_id"):
            lines.append(f"- req_id: {o.get('req_id')}")
        if o.get("duplicate_of"):
            lines.append(f"- duplicate_of: {o.get('duplicate_of')}")
        if o.get("conflicts_with"):
            lines.append(f"- conflicts_with: {','.join(o.get('conflicts_with') or [])}")
        if o.get("conflict_report_path"):
            lines.append(f"- conflict_report: {o.get('conflict_report_path')}")
        if o.get("pending_decisions"):
            lines.append(f"- pending_decisions: {len(o.get('pending_decisions') or [])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _audit_md(*, ts: str, scan: dict[str, Any]) -> str:
    def ok(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, list):
            return len(v) == 0
        if isinstance(v, dict):
            return len(v) == 0
        return False

    items = [
        ("missing_files", scan.get("missing_files")),
        ("missing_dirs", scan.get("missing_dirs")),
        ("gitignore_missing_patterns", scan.get("gitignore_missing_patterns")),
        ("roles_missing_contract_keys", scan.get("roles_missing_contract_keys")),
        ("workflow_missing", scan.get("workflow_missing")),
        ("policy_missing", scan.get("policy_missing")),
        ("schema_missing", scan.get("schema_missing")),
        ("task_artifacts_missing", scan.get("task_artifacts_missing")),
        ("routes_missing", scan.get("routes_missing")),
    ]

    lines: list[str] = []
    lines.append("# Self-Improve Run Audit Snapshot")
    lines.append("")
    lines.append(f"- ts: {ts}")
    lines.append(f"- repo_root: {scan.get('repo_root')}")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    for name, val in items:
        status = "PASS" if ok(val) else "FAIL"
        lines.append(f"- {name}: {status}")
    lines.append("")
    lines.append("## Details (truncated)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(scan, ensure_ascii=False, indent=2)[:12000])
    lines.append("```")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_issue_draft(*, title: str, body_lines: list[str]) -> str:
    lines = [f"# {title}", "", "## 背景", "", "- (self-improve)", "", "## 问题", ""]
    lines += body_lines
    lines += ["", "## 预期改进", "", "- (see acceptance)", "", "## 验收标准", "", "- ...", "", "## 证据/引用", "", "- proposal: (local)"]
    return "\n".join(lines).rstrip() + "\n"


def _write_pending_issue(repo_root: Path, *, ts: str, proposal: Proposal, outcome: AddReqOutcome) -> str:
    pend = repo_root / ".team-os" / "ledger" / "team_os_issues_pending"
    pend.mkdir(parents=True, exist_ok=True)
    rid = outcome.req_id or outcome.duplicate_of or _slug(proposal.title)
    path = pend / f"{ts}_{_slug(rid)}.md"
    if path.exists():
        return str(path)
    body = [
        f"- title: {proposal.title}",
        f"- priority: {proposal.priority}",
        f"- workstreams: {','.join(proposal.workstreams)}",
        f"- requirement_outcome: {outcome.classification}",
        f"- req_id: {outcome.req_id or ''}",
        f"- duplicate_of: {outcome.duplicate_of or ''}",
        f"- conflicts_with: {','.join(outcome.conflicts_with or [])}",
    ]
    _write_text(path, _render_issue_draft(title=proposal.title, body_lines=body))
    return str(path)


def run(
    *,
    dry_run: bool,
    force: bool,
    actor: str,
    trigger: str,
    api_routes: Optional[list[str]] = None,
    project_id: str = "teamos",
) -> dict[str, Any]:
    repo_root = team_os_root()
    ts = _utc_now_iso().replace(":", "").replace("-", "")

    should, reason = _should_run(repo_root, force=force)
    _emit_wake_event(repo_root, event_type="SELF_IMPROVE_WAKE", actor=actor, payload={"trigger": trigger, "should_run": should, "reason": reason, "dry_run": dry_run})
    if not should:
        return {"skipped": True, "reason": reason, "repo_root": str(repo_root)}

    scan = scan_repo(repo_root, api_routes=api_routes)

    # Proposals (always compute; dry_run only affects remote writes, not local truth-source updates).
    props = _default_proposals(scan, min_n=_min_proposals(repo_root))

    # Write audit snapshot (docs/)
    audit_path = repo_root / "docs" / "audits" / f"SELF_IMPROVE_RUN_{ts}.md"
    _write_text(audit_path, _audit_md(ts=ts, scan=scan))

    # Apply proposals to requirements truth source using the existing conflict detector.
    # - scope=teamos -> in-repo docs/teamos/requirements/
    # - scope=project:<id> -> workspace projects/<id>/state/requirements/
    if str(project_id) == "teamos":
        req_dir = teamos_requirements_dir()
    else:
        workspace_store.assert_project_paths_outside_repo(team_os_root=repo_root)
        workspace_store.ensure_project_scaffold(project_id)
        req_dir = workspace_store.requirements_dir(project_id)
    outcomes: list[dict[str, Any]] = []
    pending_issue_paths: list[str] = []
    created_req_ids: list[str] = []
    pending_decisions: list[dict[str, Any]] = []

    for pr in props:
        text = "\n".join(
            [
                pr.title,
                "",
                pr.text,
                "",
                "Acceptance:",
                *[f"- {x}" for x in pr.acceptance],
            ]
        ).strip()
        out = add_requirement_raw_first(
            project_id=project_id,
            req_dir=req_dir,
            requirement_text=text,
            priority=pr.priority,
            rationale="auto-generated by self-improve",
            constraints=["no secrets in git", "remote writes gated by env/approval"],
            acceptance=pr.acceptance,
            source="self-improve",
            channel="api",
            user="self-improve",
        )
        o = {
            "title": pr.title,
            "priority": pr.priority,
            "workstreams": pr.workstreams,
            "outcome": out.classification,
            "req_id": out.req_id,
            "duplicate_of": out.duplicate_of,
            "conflicts_with": out.conflicts_with,
            "conflict_report_path": out.conflict_report_path,
            "pending_decisions": out.pending_decisions,
            "actions_taken": out.actions_taken,
        }
        outcomes.append(o)
        pending_decisions.extend(out.pending_decisions or [])
        if out.req_id:
            created_req_ids.append(out.req_id)
        # Pending issue drafts are Team-OS scope (in-repo). For project scope, keep artifacts in workspace only.
        if str(project_id) == "teamos":
            pending_issue_paths.append(_write_pending_issue(repo_root, ts=ts, proposal=pr, outcome=out))

    proposal_path = repo_root / ".team-os" / "ledger" / "self_improve" / f"{ts}-proposal.md"
    _write_text(proposal_path, _proposal_md(ts=ts, actor=actor, trigger=trigger, scan=scan, outcomes=outcomes))

    # Update debounce state (gitignored).
    _write_text(
        _self_improve_state_path(repo_root),
        json.dumps(
            {"last_run_at": _utc_now_iso(), "last_run_ts_compact": ts, "dry_run": bool(dry_run), "proposals": len(props)}, ensure_ascii=False, indent=2
        )
        + "\n",
    )

    return {
        "skipped": False,
        "repo_root": str(repo_root),
        "audit_path": str(audit_path),
        "proposal_path": str(proposal_path),
        "created_req_ids": created_req_ids,
        "pending_issue_paths": pending_issue_paths[:50],
        "pending_decisions": pending_decisions[:50],
        "scan_summary": {
            "missing_files": len(scan.get("missing_files") or []),
            "missing_dirs": len(scan.get("missing_dirs") or []),
            "roles_missing_contract_keys": len(scan.get("roles_missing_contract_keys") or {}),
            "task_artifacts_missing": len(scan.get("task_artifacts_missing") or {}),
            "routes_missing": len(scan.get("routes_missing") or []),
        },
        "dry_run": bool(dry_run),
    }
