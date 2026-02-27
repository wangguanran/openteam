#!/usr/bin/env python3
"""
Always-on self-improve daemon (leader-only).

Design goals:
- Deterministic: rules/templates only (no LLM).
- Governance-safe: leader-only writes; non-leader is scan-only.
- Outputs are reproducible and schema-validated via existing pipelines:
  - proposals: .team-os/ledger/self_improve/<ts>-proposal.md
  - requirements: system channel updates requirements.yaml -> REQUIREMENTS.md -> CHANGELOG.md (does NOT write raw_inputs.jsonl)
- Daemon mode is host-level (git repo available). Remote writes are gated by policy.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

from _common import PipelineError, add_default_args, resolve_repo_root, utc_now_iso, write_json
from _db import connect, get_db_url
from db_migrate import apply_migrations as _apply_migrations


def _http_json(url: str, *, timeout_sec: int = 5) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            obj = json.loads(body) if body else {}
            return obj if isinstance(obj, dict) else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise PipelineError(f"HTTP {e.code} {e.reason}: {body[:300]}") from e
    except Exception as e:
        raise PipelineError(f"HTTP request failed: {e}") from e


def _load_base_url(*, profile: str = "") -> str:
    cfg = Path.home() / ".teamos" / "config.toml"
    if not cfg.exists():
        return "http://127.0.0.1:8787"
    try:
        try:
            import tomli  # type: ignore

            doc = tomli.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            import tomllib

            doc = tomllib.loads(cfg.read_bytes())
    except Exception:
        return "http://127.0.0.1:8787"

    cur = str(profile or doc.get("current_profile") or "").strip()
    profiles = doc.get("profiles") or {}
    if not cur:
        cur = "local" if "local" in profiles else (sorted(list(profiles.keys()))[0] if profiles else "")
    p = (profiles or {}).get(cur) or {}
    base = str(p.get("base_url") or "").strip().rstrip("/")
    return base or "http://127.0.0.1:8787"


def _is_pid_running(pid: int) -> bool:
    try:
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _pid_path(repo: Path) -> Path:
    return repo / ".team-os" / "state" / "self_improve_daemon.pid"


def _state_path(repo: Path) -> Path:
    # Runtime state (gitignored).
    return repo / ".team-os" / "state" / "self_improve_state.json"


def _log_path(repo: Path) -> Path:
    return repo / ".team-os" / "state" / "self_improve_daemon.log"


def _sha256_text(s: str) -> str:
    return sha256((s or "").encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(_read_text(path))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _read_policy(repo: Path) -> dict[str, Any]:
    # Default policy: safe-by-default (no remote writes).
    default = {
        "self_improve": {
            "enabled": True,
            "leader_only": True,
            "interval_sec": 3600,
            "debounce_sec": 6 * 3600,
            "min_proposals_per_run": 3,
            "write_proposal_md": True,
            "write_requirements": True,
            "panel_sync": {"enabled": True, "mode": "full", "dry_run": True},
            "dedupe": {"enabled": True, "max_keys": 300},
        }
    }
    pol_path = repo / "policies" / "self_improve.yaml"
    from _common import read_yaml

    doc = read_yaml(pol_path) if pol_path.exists() else {}
    si = (doc.get("self_improve") or {}) if isinstance(doc, dict) else {}
    out = default
    out["self_improve"].update(si if isinstance(si, dict) else {})
    return out


def _now_epoch() -> int:
    return int(time.time())


def _debounce_ok(state: dict[str, Any], *, debounce_sec: int, force: bool) -> tuple[bool, str]:
    if force:
        return True, "force"
    last = str((state.get("last_run") or {}).get("ts") or "").strip()
    if not last:
        return True, "no_last_run"
    try:
        import datetime as _dt

        dt = _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
        now = _dt.datetime.now(_dt.timezone.utc)
        age = (now - dt).total_seconds()
        if age >= float(debounce_sec):
            return True, f"debounce_elapsed age_sec={int(age)}"
        return False, f"debounced age_sec={int(age)}"
    except Exception as e:
        return True, f"state_parse_error={e}"


def _next_run_iso(*, interval_sec: int) -> str:
    import datetime as _dt

    dt = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=int(interval_sec))
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _leader_status(*, base_url: str) -> dict[str, Any]:
    st = _http_json(base_url + "/v1/status", timeout_sec=5)
    cs = _http_json(base_url + "/v1/cluster/status", timeout_sec=5)
    me = str(st.get("instance_id") or "").strip()
    leader = (cs.get("leader") or {}) if isinstance(cs.get("leader"), dict) else {}
    leader_id = str(leader.get("leader_instance_id") or "").strip()
    is_leader = bool(me and leader_id and me == leader_id)
    return {
        "ok": True,
        "base_url": base_url,
        "instance_id": me,
        "leader_instance_id": leader_id,
        "is_leader": is_leader,
    }


@dataclass(frozen=True)
class Proposal:
    key: str
    title: str
    text: str
    priority: str
    workstreams: list[str]
    acceptance: list[str]
    evidence: list[str]


def _proposal_key(title: str, text: str, acceptance: list[str]) -> str:
    blob = "\n".join([str(title or "").strip(), str(text or "").strip(), "\n".join(acceptance or [])]).strip()
    return _sha256_text(blob)[:16]


def _scan(repo: Path) -> dict[str, Any]:
    # Deterministic, local-only scan.
    missing_docs = []
    if not (repo / "docs" / "WORKSPACE_RUNBOOK.md").exists():
        missing_docs.append("docs/WORKSPACE_RUNBOOK.md")

    missing_evals = []
    if not (repo / "evals" / "test_self_improve_daemon.py").exists():
        missing_evals.append("evals/test_self_improve_daemon.py")

    missing_pipelines = []
    if not (repo / "scripts" / "pipelines" / "self_improve_daemon.py").exists():
        missing_pipelines.append("scripts/pipelines/self_improve_daemon.py")
    if not (repo / "scripts" / "pipelines" / "task_ship.py").exists():
        missing_pipelines.append("scripts/pipelines/task_ship.py")

    teamos_text = _read_text(repo / "teamos")
    auto_wake_present = "_auto_wake_self_improve(args)" in teamos_text

    return {
        "repo_root": str(repo),
        "missing_docs": missing_docs,
        "missing_evals": missing_evals,
        "missing_pipelines": missing_pipelines,
        "cli_auto_wake_present": auto_wake_present,
    }


def _proposals_from_scan(scan: dict[str, Any], *, min_n: int) -> list[Proposal]:
    props: list[Proposal] = []

    if scan.get("cli_auto_wake_present"):
        title = "Remove CLI auto-wake writes; require daemon-based self-improve"
        text = "移除 `teamos` CLI 在任意命令入口自动触发 self-improve 的行为，避免产生非任务化写入（wake_events/proposals/requirements）。改为 host-level daemon + leader-only。"
        acc = ["普通 CLI 命令不再产生 self-improve 写入", "自我优化通过 daemon 常驻运行，并可查询状态/停止"]
        evidence = ["teamos: contains _auto_wake_self_improve(args)"]
        props.append(
            Proposal(
                key=_proposal_key(title, text, acc),
                title=title,
                text=text,
                priority="P0",
                workstreams=["governance", "devops"],
                acceptance=acc,
                evidence=evidence,
            )
        )

    for p in (scan.get("missing_docs") or []):
        if str(p) == "docs/WORKSPACE_RUNBOOK.md":
            title = "Add WORKSPACE_RUNBOOK.md (repo/workspace boundary, init/migrate/doctor)"
            text = "补齐 Workspace 运行手册：初始化/迁移/校验（repo purity）、常见故障与恢复；明确 project 真相源只能落在 Workspace（repo 外）。"
            acc = ["新增 docs/WORKSPACE_RUNBOOK.md", "runbook 覆盖 workspace init/migrate/doctor 的可执行命令"]
            evidence = [f"missing: {p}"]
            props.append(
                Proposal(
                    key=_proposal_key(title, text, acc),
                    title=title,
                    text=text,
                    priority="P1",
                    workstreams=["governance"],
                    acceptance=acc,
                    evidence=evidence,
                )
            )

    for p in (scan.get("missing_evals") or []):
        if str(p) == "evals/test_self_improve_daemon.py":
            title = "Add evals coverage for self_improve_daemon (scheduler/dedupe/leader-only)"
            text = "补齐 evals：覆盖 self-improve daemon 的最小行为（>=3 proposals、state 更新、leader-only 写入拦截、debounce/dedupe）。"
            acc = ["新增 evals/test_self_improve_daemon.py（离线、决定性）", "CI/本地可运行并稳定通过"]
            evidence = [f"missing: {p}"]
            props.append(
                Proposal(
                    key=_proposal_key(title, text, acc),
                    title=title,
                    text=text,
                    priority="P1",
                    workstreams=["devops"],
                    acceptance=acc,
                    evidence=evidence,
                )
            )

    for p in (scan.get("missing_pipelines") or []):
        if str(p) == "scripts/pipelines/task_ship.py":
            title = "Implement task ship (close -> gates -> commit -> push; mark BLOCKED on push failure)"
            text = "实现 `./teamos task ship <TASK_ID>`：强制 close→secrets/purity/tests→commit→push；push 失败则将任务标记 BLOCKED 并记录原因。"
            acc = ["新增 pipeline: scripts/pipelines/task_ship.py", "CLI: ./teamos task ship 可用并可 dry-run"]
            evidence = [f"missing: {p}"]
            props.append(
                Proposal(
                    key=_proposal_key(title, text, acc),
                    title=title,
                    text=text,
                    priority="P0",
                    workstreams=["governance", "devops"],
                    acceptance=acc,
                    evidence=evidence,
                )
            )

    # Deterministic padding (ensures >= min_n outputs even on a clean repo).
    while len(props) < max(0, int(min_n)):
        n = len(props) + 1
        title = f"Continuous self-improve placeholder #{n}: expand evals/docs coverage"
        text = "持续增强 Team OS 的 evals 覆盖与文档完整性（决定性、默认无远端写）。"
        acc = ["新增至少 1 个 eval 或文档章节", "不引入 secrets 入库风险"]
        evidence = ["padding: ensure min proposals per run"]
        props.append(
            Proposal(
                key=_proposal_key(title, text, acc),
                title=title,
                text=text,
                priority="P2",
                workstreams=["governance"],
                acceptance=acc,
                evidence=evidence,
            )
        )

    # Stable ordering: by priority then title.
    prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return sorted(props, key=lambda p: (prio_rank.get(p.priority, 9), p.title, p.key))


def _proposal_md(*, ts: str, actor: str, trigger: str, scan: dict[str, Any], applied: list[dict[str, Any]], leader: dict[str, Any], policy: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Self-Improve Proposal")
    lines.append("")
    lines.append(f"- ts: {ts}")
    lines.append(f"- actor: {actor}")
    lines.append(f"- trigger: {trigger}")
    lines.append(f"- leader_only: {bool((policy.get('self_improve') or {}).get('leader_only'))}")
    lines.append(f"- is_leader: {leader.get('is_leader')}")
    if leader.get("reason"):
        lines.append(f"- leader_reason: {leader.get('reason')}")
    lines.append("")
    lines.append("## Scan Summary")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(scan, ensure_ascii=False, indent=2)[:12000])
    lines.append("```")
    lines.append("")
    lines.append("## Proposals (apply outcomes)")
    lines.append("")
    for i, o in enumerate(applied, 1):
        lines.append(f"### {i}. {o.get('title')}")
        lines.append("")
        lines.append(f"- key: {o.get('key')}")
        lines.append(f"- priority: {o.get('priority')}")
        lines.append(f"- workstreams: {','.join(o.get('workstreams') or [])}")
        lines.append(f"- apply: {o.get('apply')}")
        if o.get("result"):
            r = o["result"]
            lines.append(f"- classification: {r.get('classification')}")
            if r.get("req_id"):
                lines.append(f"- req_id: {r.get('req_id')}")
            if r.get("duplicate_of"):
                lines.append(f"- duplicate_of: {r.get('duplicate_of')}")
            if r.get("conflicts_with"):
                lines.append(f"- conflicts_with: {','.join(r.get('conflicts_with') or [])}")
            if r.get("conflict_report_path"):
                lines.append(f"- conflict_report: {r.get('conflict_report_path')}")
        if o.get("error"):
            lines.append(f"- error: {str(o.get('error'))[:300]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _requirements_add(repo: Path, ws_root: Path, *, scope: str, text: str, workstream: str, priority: str, source: str) -> dict[str, Any]:
    """
    Self-improve writes must not pollute Raw inputs.

    Use the system update channel (non-raw) to update Expanded requirements deterministically.
    """
    script = repo / "scripts" / "pipelines" / "system_requirements_update.py"
    if not script.exists():
        raise PipelineError(f"missing pipeline: {script}")
    argv = [
        sys.executable,
        str(script),
        "--repo-root",
        str(repo),
        "--workspace-root",
        str(ws_root),
        "--scope",
        scope,
        "--text",
        text,
        "--workstream",
        workstream,
        "--priority",
        priority,
        "--source",
        source,
        "--rationale",
        "SYSTEM_SELF_IMPROVE",
    ]
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        raise PipelineError(f"system_requirements_update failed: {err[:300] or out[:300]}")
    try:
        obj = json.loads(out) if out else {}
        return obj if isinstance(obj, dict) else {"_raw": out}
    except Exception:
        return {"_raw": out}


def _panel_sync(repo: Path, *, project_id: str, mode: str, dry_run: bool, base_url: str, profile: str) -> dict[str, Any]:
    # Use CLI to hit control-plane. dry_run avoids GitHub writes.
    cli = repo / "teamos"
    if not cli.exists():
        raise PipelineError(f"missing CLI: {cli}")
    argv = [str(cli)]
    if profile:
        argv += ["--profile", profile]
    argv += ["panel", "sync", "--project", project_id]
    if str(mode).strip().lower() == "full":
        argv.append("--full")
    if dry_run:
        argv.append("--dry-run")
    p = subprocess.run(argv, cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        raise PipelineError(f"panel sync failed: {err[:300] or out[:300]}")
    return {"ok": True, "stdout_tail": out.splitlines()[-20:]}


def run_once(
    repo: Path,
    ws_root: Path,
    *,
    actor: str,
    trigger: str,
    scope: str,
    force: bool,
    dry_run_local: bool,
    profile: str,
    base_url: str,
) -> dict[str, Any]:
    pol = _read_policy(repo)
    si = pol.get("self_improve") or {}
    enabled = bool(si.get("enabled"))
    write_requirements = bool(si.get("write_requirements"))

    state_path = _state_path(repo)
    state = _read_json(state_path)
    state.setdefault("schema_version", 1)
    state.setdefault("dedupe", {})
    state.setdefault("last_errors", [])

    leader_only = bool(si.get("leader_only"))
    leader: dict[str, Any] = {"ok": False, "is_leader": False, "reason": "unknown", "base_url": base_url}
    try:
        leader = _leader_status(base_url=base_url)
    except Exception as e:
        leader = {"ok": False, "is_leader": False, "reason": str(e)[:200], "base_url": base_url}

    debounce_sec = int(si.get("debounce_sec") or 0)
    ok_deb, deb_reason = _debounce_ok(state, debounce_sec=debounce_sec, force=force)

    scan = _scan(repo)
    props = _proposals_from_scan(scan, min_n=int(si.get("min_proposals_per_run") or 3))

    # Decide truth-source writes
    can_write = enabled and write_requirements and (not dry_run_local)
    if leader_only:
        can_write = can_write and bool(leader.get("is_leader"))
    if not ok_deb:
        can_write = False

    ts = utc_now_iso().replace(":", "").replace("-", "")
    run_id = f"si-{ts}"
    applied: list[dict[str, Any]] = []
    dedupe_cfg = si.get("dedupe") or {}
    dedupe_enabled = bool(dedupe_cfg.get("enabled"))
    max_keys = int(dedupe_cfg.get("max_keys") or 300)
    dedupe_map = (state.get("dedupe") or {}) if isinstance(state.get("dedupe"), dict) else {}

    for pr in props:
        item: dict[str, Any] = {"key": pr.key, "title": pr.title, "priority": pr.priority, "workstreams": pr.workstreams}
        already = (pr.key in dedupe_map) if dedupe_enabled else False
        want_apply = can_write and (force or (not already))
        item["apply"] = "APPLY" if want_apply else ("DEDUPED" if already else ("READONLY" if enabled else "DISABLED"))

        if want_apply:
            # Requirements Raw-First (deterministic; schema validated inside the pipeline).
            try:
                text = "\n".join(
                    [
                        pr.title,
                        "",
                        pr.text,
                        "",
                        "Evidence:",
                        *[f"- {x}" for x in (pr.evidence or [])],
                        "",
                        "Acceptance:",
                        *[f"- {x}" for x in (pr.acceptance or [])],
                    ]
                ).rstrip()
                res = _requirements_add(
                    repo,
                    ws_root,
                    scope=scope,
                    text=text,
                    workstream=(pr.workstreams[0] if pr.workstreams else "governance"),
                    priority=pr.priority,
                    source="SYSTEM_SELF_IMPROVE",
                )
                item["result"] = {k: res.get(k) for k in ("classification", "req_id", "duplicate_of", "conflicts_with", "conflict_report_path", "pending_decisions")}
                dedupe_map[pr.key] = utc_now_iso()
            except Exception as e:
                item["error"] = str(e)[:400]
        applied.append(item)

    # Trim dedupe map.
    if dedupe_enabled and isinstance(dedupe_map, dict) and len(dedupe_map) > max_keys:
        # Keep the newest by timestamp (isoformat sorts).
        items = sorted([(k, str(v)) for k, v in dedupe_map.items()], key=lambda kv: kv[1])
        dedupe_map = {k: v for (k, v) in items[-max_keys:]}

    proposal_path = ""
    if enabled and bool(si.get("write_proposal_md")) and bool(leader.get("is_leader")) and (not dry_run_local) and ok_deb:
        md = _proposal_md(ts=ts, actor=actor, trigger=trigger, scan=scan, applied=applied, leader=leader, policy=pol)
        outp = repo / ".team-os" / "ledger" / "self_improve" / f"{ts}-proposal.md"
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md, encoding="utf-8")
        proposal_path = str(outp)

    # Optional panel sync (view-layer). Defaults to dry-run only.
    panel_cfg = si.get("panel_sync") or {}
    panel_out: dict[str, Any] = {"ok": False, "skipped": True, "reason": "disabled"}
    if enabled and (not dry_run_local) and bool(panel_cfg.get("enabled")):
        try:
            panel_out = _panel_sync(
                repo,
                project_id="teamos",
                mode=str(panel_cfg.get("mode") or "full"),
                dry_run=bool(panel_cfg.get("dry_run")),
                base_url=base_url,
                profile=profile,
            )
        except Exception as e:
            panel_out = {"ok": False, "skipped": False, "error": str(e)[:400]}

    success_applies = len([x for x in applied if x.get("apply") == "APPLY" and (not x.get("error"))])

    # Update state (always; gitignored runtime state).
    state["enabled"] = enabled
    state["policy_sha256"] = _sha256_text(_read_text(repo / "policies" / "self_improve.yaml"))
    state["leader"] = leader
    state["last_run"] = {
        "ts": utc_now_iso(),
        "ok": True,
        "debounce_ok": ok_deb,
        "debounce_reason": deb_reason,
        "wrote_truth": bool(proposal_path) or (success_applies > 0),
        "proposal_path": proposal_path,
        "applied_count": success_applies,
        "panel_sync": panel_out,
    }
    state["next_run_at"] = _next_run_iso(interval_sec=int(si.get("interval_sec") or 3600))
    state["dedupe"] = dedupe_map
    write_json(state_path, state, dry_run=False)

    # Optional: record run into Postgres (shared hub) when configured.
    db_record: dict[str, Any] = {"ok": False, "skipped": True, "reason": "TEAMOS_DB_URL not set"}
    dsn = get_db_url()
    if dsn:
        try:
            conn = connect(dsn)
            try:
                # Ensure schema (migrations are idempotent).
                mig_dir = repo / "migrations"
                migrations: list[tuple[str, Path]] = []
                for p in sorted(mig_dir.glob("*.sql")):
                    name = p.name
                    if len(name) >= 4 and name[:4].isdigit():
                        migrations.append((name[:4], p))
                if migrations:
                    _apply_migrations(conn, migrations)

                leader_instance_id = str(leader.get("leader_instance_id") or "")
                instance_id = str(leader.get("instance_id") or leader_instance_id or "")
                is_leader = bool(leader.get("is_leader"))

                # Dedupe key for this run (stable for a given set of applied proposal keys).
                applied_keys = sorted([str(x.get("key") or "") for x in applied if x.get("apply") == "APPLY" and str(x.get("key") or "").strip()])
                dedupe_key = _sha256_text("\\n".join(applied_keys))[:32]

                details = {
                    "policy": {"enabled": enabled, "leader_only": leader_only, "debounce_ok": ok_deb, "debounce_reason": deb_reason},
                    "leader": leader,
                    "scan": scan,
                    "panel_sync": panel_out,
                }

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO self_improve_runs (
                          run_id, ts, instance_id, is_leader, trigger, scope,
                          ok, applied_count, dedupe_key, proposal_path, details
                        ) VALUES (
                          %s, now(), %s, %s, %s, %s,
                          %s, %s, %s, %s, %s::jsonb
                        )
                        ON CONFLICT(run_id) DO UPDATE SET
                          ts=EXCLUDED.ts,
                          instance_id=EXCLUDED.instance_id,
                          is_leader=EXCLUDED.is_leader,
                          trigger=EXCLUDED.trigger,
                          scope=EXCLUDED.scope,
                          ok=EXCLUDED.ok,
                          applied_count=EXCLUDED.applied_count,
                          dedupe_key=EXCLUDED.dedupe_key,
                          proposal_path=EXCLUDED.proposal_path,
                          details=EXCLUDED.details
                        """,
                        (
                            run_id,
                            instance_id,
                            True if is_leader else False,
                            str(trigger or ""),
                            str(scope or ""),
                            True,
                            int(success_applies),
                            dedupe_key,
                            str(proposal_path or ""),
                            json.dumps(details, ensure_ascii=False, sort_keys=True),
                        ),
                    )
                conn.commit()
                db_record = {"ok": True, "skipped": False, "run_id": run_id}
            finally:
                conn.close()
        except Exception as e:
            db_record = {"ok": False, "skipped": False, "error": str(e)[:300]}

    return {
        "ok": True,
        "enabled": enabled,
        "leader": leader,
        "debounce_ok": ok_deb,
        "debounce_reason": deb_reason,
        "proposal_path": proposal_path,
        "applied_count": success_applies,
        "panel_sync": panel_out,
        "state_path": str(state_path),
        "db_record": db_record,
    }


def daemon_loop(
    repo: Path,
    ws_root: Path,
    *,
    profile: str,
    base_url: str,
) -> int:
    pol = _read_policy(repo)
    si = pol.get("self_improve") or {}
    interval_sec = int(si.get("interval_sec") or 3600)

    stop = {"flag": False}

    def _sig(_signum: int, _frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    while not stop["flag"]:
        try:
            run_once(
                repo,
                ws_root,
                actor="daemon.self_improve",
                trigger="daemon",
                scope="teamos",
                force=False,
                dry_run_local=False,
                profile=profile,
                base_url=base_url,
            )
        except Exception:
            # Never crash the daemon loop; record errors into state.
            st = _read_json(_state_path(repo))
            errs = st.get("last_errors") if isinstance(st.get("last_errors"), list) else []
            errs = list(errs)[-20:]
            errs.append({"ts": utc_now_iso(), "error": "self_improve_iteration_failed"})
            st["last_errors"] = errs
            write_json(_state_path(repo), st, dry_run=False)

        # Sleep in small chunks to be responsive to signals.
        for _ in range(max(1, int(interval_sec))):
            if stop["flag"]:
                break
            time.sleep(1)
    return 0


def _start_daemon(repo: Path, ws_root: Path, *, profile: str, base_url: str) -> dict[str, Any]:
    pidp = _pid_path(repo)
    if pidp.exists():
        try:
            pid = int(_read_text(pidp).strip())
        except Exception:
            pid = 0
        if _is_pid_running(pid):
            return {"ok": True, "already_running": True, "pid": pid, "pid_path": str(pidp)}

    logp = _log_path(repo)
    logp.parent.mkdir(parents=True, exist_ok=True)
    logf = logp.open("a", encoding="utf-8")

    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--repo-root",
        str(repo),
        "--workspace-root",
        str(ws_root),
    ]
    if profile:
        argv += ["--profile", profile]
    if base_url:
        argv += ["--base-url", base_url]
    argv += ["daemon"]

    env = os.environ.copy()
    env["TEAMOS_SELF_IMPROVE_DAEMON_CHILD"] = "1"
    p = subprocess.Popen(argv, cwd=str(repo), stdout=logf, stderr=logf, env=env, start_new_session=True)
    pidp.write_text(str(int(p.pid)) + "\n", encoding="utf-8")

    st = _read_json(_state_path(repo))
    st.setdefault("schema_version", 1)
    st["daemon"] = {"pid": int(p.pid), "started_at": utc_now_iso(), "log_path": str(logp)}
    write_json(_state_path(repo), st, dry_run=False)

    return {"ok": True, "already_running": False, "pid": int(p.pid), "pid_path": str(pidp), "log_path": str(logp)}


def _stop_daemon(repo: Path) -> dict[str, Any]:
    pidp = _pid_path(repo)
    if not pidp.exists():
        return {"ok": True, "stopped": False, "reason": "no_pid_file"}
    try:
        pid = int(_read_text(pidp).strip())
    except Exception:
        pid = 0
    if not _is_pid_running(pid):
        try:
            pidp.unlink()
        except Exception:
            pass
        return {"ok": True, "stopped": True, "pid": pid, "reason": "not_running"}
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception as e:
        return {"ok": False, "stopped": False, "pid": pid, "error": str(e)[:200]}

    # Wait briefly.
    for _ in range(30):
        if not _is_pid_running(pid):
            break
        time.sleep(0.2)
    stopped = not _is_pid_running(pid)
    if stopped:
        try:
            pidp.unlink()
        except Exception:
            pass
    st = _read_json(_state_path(repo))
    st["daemon"] = {"pid": pid, "stopped_at": utc_now_iso(), "stopped": stopped}
    write_json(_state_path(repo), st, dry_run=False)
    return {"ok": True, "stopped": stopped, "pid": pid}


def _status(repo: Path) -> dict[str, Any]:
    pidp = _pid_path(repo)
    pid = 0
    if pidp.exists():
        try:
            pid = int(_read_text(pidp).strip())
        except Exception:
            pid = 0
    running = _is_pid_running(pid)
    st = _read_json(_state_path(repo))
    return {"ok": True, "running": running, "pid": pid, "pid_path": str(pidp), "state": st}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Always-on self-improve daemon (leader-only; deterministic)")
    add_default_args(ap)
    ap.add_argument("--profile", default="", help="profile name (from ~/.teamos/config.toml)")
    ap.add_argument("--base-url", default="", help="override control plane base url")
    sp = ap.add_subparsers(dest="cmd", required=True)

    ro = sp.add_parser("run-once", help="Run one self-improve iteration now")
    ro.add_argument("--scope", default="teamos")
    ro.add_argument("--force", action="store_true")
    ro.add_argument("--dry-run-local", action="store_true", help="compute proposals only; do not write proposal/requirements")

    dm = sp.add_parser("daemon", help="Run daemon loop in foreground")

    st = sp.add_parser("start", help="Spawn daemon in background (writes pid/log/state under .team-os/state/)")

    sp_stop = sp.add_parser("stop", help="Stop background daemon (best-effort)")

    sp.add_parser("status", help="Show daemon status/state").set_defaults()

    args = ap.parse_args(argv)
    repo = resolve_repo_root(args)
    from _common import resolve_workspace_root

    ws_root = resolve_workspace_root(args)

    base = str(getattr(args, "base_url", "") or "").strip().rstrip("/") or _load_base_url(profile=str(getattr(args, "profile", "") or ""))
    profile = str(getattr(args, "profile", "") or "").strip()

    if args.cmd == "run-once":
        out = run_once(
            repo,
            ws_root,
            actor="cli.self_improve",
            trigger="manual",
            scope=str(getattr(args, "scope", "teamos") or "teamos"),
            force=bool(getattr(args, "force", False)),
            dry_run_local=bool(getattr(args, "dry_run_local", False)),
            profile=profile,
            base_url=base,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if bool(out.get("ok")) else 2

    if args.cmd == "daemon":
        return daemon_loop(repo, ws_root, profile=profile, base_url=base)

    if args.cmd == "start":
        out = _start_daemon(repo, ws_root, profile=profile, base_url=base)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if bool(out.get("ok")) else 2

    if args.cmd == "stop":
        out = _stop_daemon(repo)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if bool(out.get("ok")) else 2

    out = _status(repo)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if bool(out.get("ok")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
