"""Miscellaneous subcommand handlers: prompt, metrics, policy, approvals, audit, doctor, daemon, repo, task, improvement-targets, openclaw, chat."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from openteam_cli._shared import (
    _base_url,
    _default_project_id,
    _default_scope,
    _ensure_project_scaffold,
    _find_openteam_repo_root,
    _fmt_table,
    _get_profile,
    _inject_project_agents_manual,
    _load_config,
    _require_project_id,
    _run_pipeline,
    _runtime_root_for_repo,
    _workspace_root,
    shutil_which,
)
from openteam_cli.http import _http_json
from openteam_cli.team import (
    _default_team_id_from_status,
    _read_last_team_run,
    _team_status_doc,
)


def cmd_chat(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    workstream_id = args.workstream

    prompt = "Type a message and press Enter. Commands: /req <text>, /pause, /resume, /stop, /quit"
    print(prompt)
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        if not line:
            break
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if line.strip() in ("/quit", "/exit"):
            break

        msg_type = "GENERAL"
        msg = line
        if line.startswith("/req "):
            msg_type = "NEW_REQUIREMENT"
            msg = line[len("/req ") :].strip()
        elif line.strip() == "/pause":
            msg_type = "PAUSE"
            msg = "pause"
        elif line.strip() == "/resume":
            msg_type = "RESUME"
            msg = "resume"
        elif line.strip() == "/stop":
            msg_type = "STOP"
            msg = "stop"

        payload = {
            "project_id": project_id,
            "workstream_id": workstream_id,
            "run_id": args.run,
            "message": msg,
            "message_type": msg_type,
        }
        out = _http_json("POST", base + "/v1/chat", payload, timeout_sec=120)
        print(out.get("response_text", "").rstrip())


def cmd_doctor(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    base, prof = _base_url(args)
    _run_pipeline(
        repo_root,
        "scripts/pipelines/doctor.py",
        [
            "--repo-root",
            str(repo_root),
            "--workspace-root",
            str(_workspace_root(args)),
            "--profile",
            str(prof.get("name") or ""),
            "--base-url",
            base,
        ],
    )


def cmd_policy_check(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    script = repo_root / "scripts" / "policy_check.py"
    if not script.exists():
        raise RuntimeError(f"policy_check script missing: {script}")

    cmd = [sys.executable, str(script), "--repo-root", str(repo_root)]
    if getattr(args, "json", False):
        cmd.append("--json")
    if getattr(args, "quiet", False):
        cmd.append("--quiet")
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def cmd_approvals_list(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--json",
        "list",
        "--limit",
        str(int(getattr(args, "limit", 50) or 50)),
    ]
    _run_pipeline(repo_root, "scripts/pipelines/approvals.py", argv)


def cmd_prompt_compile(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    scope = _default_scope(prof, args)
    if getattr(args, "scope", ""):
        scope = str(args.scope).strip()
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--scope", scope]
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/prompt_compile.py", argv)


def cmd_prompt_diff(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    scope = _default_scope(prof, args)
    if getattr(args, "scope", ""):
        scope = str(args.scope).strip()
    argv = ["--repo-root", str(repo_root), "--workspace-root", str(_workspace_root(args)), "--scope", scope]
    _run_pipeline(repo_root, "scripts/pipelines/prompt_diff.py", argv)


def _parse_metrics_jsonl(path: Path) -> list[str]:
    issues: list[str] = []
    if not path.exists():
        return [f"missing: {path}"]
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                issues.append(f"{path}:{i} not an object")
                continue
            for k in ("ts", "event_type", "actor"):
                if not str(obj.get(k) or "").strip():
                    issues.append(f"{path}:{i} missing field: {k}")
        except Exception as e:
            issues.append(f"{path}:{i} invalid json: {e}")
    return issues


def cmd_metrics_check(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    required_schema = repo_root / "schemas" / "telemetry_event.schema.json"
    if not required_schema.exists():
        print(f"FAIL missing_schema={required_schema}")
        raise SystemExit(2)

    tasks_root = repo_root / ".openteam" / "logs" / "tasks"
    if not tasks_root.exists():
        print(f"FAIL missing_tasks_logs_dir={tasks_root}")
        raise SystemExit(2)

    want_logs = [
        "00_intake.md",
        "01_plan.md",
        "02_todo.md",
        "03_work.md",
        "04_test.md",
        "05_release.md",
        "06_observe.md",
        "07_retro.md",
    ]

    missing_files: list[str] = []
    metrics_issues: list[str] = []
    checked_tasks = 0

    for d in sorted(tasks_root.iterdir()):
        if not d.is_dir():
            continue
        checked_tasks += 1
        for f in want_logs:
            if not (d / f).exists():
                missing_files.append(f"{d.name}/{f}")
        metrics_issues.extend(_parse_metrics_jsonl(d / "metrics.jsonl"))

    ok = (not missing_files) and (not metrics_issues)
    print(f"ok={ok} tasks_checked={checked_tasks} missing_files={len(missing_files)} metrics_issues={len(metrics_issues)}")
    if missing_files and not args.quiet:
        print("missing:")
        for x in missing_files[:50]:
            print(f"- {x}")
    if metrics_issues and not args.quiet:
        print("metrics_issues:")
        for x in metrics_issues[:50]:
            print(f"- {x}")
    if not ok:
        raise SystemExit(2)


def cmd_metrics_analyze(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    script = repo_root / "scripts" / "metrics" / "analyze_evolution.py"
    if not script.exists():
        raise RuntimeError(f"missing metrics analyzer: {script}")
    p = subprocess.run([sys.executable, str(script), "--tasks-dir", str(repo_root / ".openteam" / "logs" / "tasks")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (p.stderr or b"").decode("utf-8", errors="replace").strip()
    if p.returncode != 0:
        raise RuntimeError(f"metrics analyze failed: {err[:200]}")
    print(out)


def cmd_metrics_bootstrap(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    script = repo_root / "scripts" / "migrations" / "bootstrap_task_artifacts.py"
    if not script.exists():
        raise RuntimeError(f"missing bootstrap script: {script}")
    argv = [sys.executable, str(script), "--full"]
    if args.dry_run:
        argv.append("--dry-run")
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sys.stdout.write((p.stdout or b"").decode("utf-8", errors="replace"))
    if p.returncode != 0:
        sys.stderr.write((p.stderr or b"").decode("utf-8", errors="replace"))
        raise SystemExit(p.returncode)


def cmd_audit_deterministic_gov(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
    ]
    if getattr(args, "out", ""):
        argv += ["--out", str(args.out).strip()]
    # Pass through CLI profile (used by the audit generator to run doctor/daemon status consistently).
    if getattr(args, "profile", ""):
        argv += ["--profile", str(args.profile).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/audit_deterministic_gov.py", argv)


def cmd_audit_execution_strategy(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
    ]
    if getattr(args, "out", ""):
        argv += ["--out", str(args.out).strip()]
    if getattr(args, "profile", ""):
        argv += ["--profile", str(args.profile).strip()]
    _run_pipeline(repo_root, "scripts/pipelines/audit_execution_strategy.py", argv)


def cmd_audit_reqv3_locks(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
    ]
    if getattr(args, "out", ""):
        argv += ["--out", str(args.out).strip()]
    if getattr(args, "project_id", ""):
        argv += ["--project-id", str(args.project_id).strip()]
    if bool(getattr(args, "skip_team", False)):
        argv.append("--skip-team")
    if bool(getattr(args, "skip_db", False)):
        argv.append("--skip-db")
    _run_pipeline(repo_root, "scripts/pipelines/audit_reqv3_locks.py", argv)


def cmd_daemon_start(args: argparse.Namespace) -> None:
    """
    Legacy daemon mode has been removed.
    """
    raise RuntimeError("Legacy team daemon has been removed. Start the OpenTeam runtime or run `openteam team run --team-id <team_id> --force`.")


def cmd_daemon_stop(args: argparse.Namespace) -> None:
    print("legacy_team_daemon.removed=true")


def cmd_daemon_status(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    base, _ = _base_url(args)
    status = _team_status_doc(base_url=base)
    team_id = _default_team_id_from_status(status)
    last = _read_last_team_run(repo_root, base_url=base, team_id=team_id)
    payload = {
        "legacy_team_daemon": "removed",
        "default_team_id": team_id,
        "runtime_state_root": str(_runtime_root_for_repo(repo_root) / "state"),
        "last_team_run": last,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_repo_create(args: argparse.Namespace) -> None:
    if shutil_which("gh") is None:
        raise RuntimeError("gh CLI not found. Install gh then run: gh auth login")

    name = args.name
    org = args.org or ""
    full = f"{org}/{name}" if org else name
    vis = "--public" if bool(getattr(args, "public", False)) else "--private"
    cmd = ["gh", "repo", "create", full, vis]
    if args.clone_dir:
        cmd += ["--clone", "--", str(args.clone_dir)]

    if not args.approve:
        print("approval_required: repo_create is high risk")
        print("would_run: " + " ".join(cmd))
        print("next: re-run with --approve to execute (will prompt + record approval)")
        return

    # Approval gate writes local audit records for the single-node runtime.
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    from openteam_cli._shared import _approval_gate
    _approval_gate(
        args,
        repo_root=repo_root,
        action_kind="repo_create",
        summary=f"gh repo create {full} {vis}",
        payload={"full": full, "visibility": vis, "clone_dir": str(args.clone_dir or "")},
    )

    p = subprocess.run(cmd, check=False)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def cmd_task_new(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")
    cfg = _load_config()
    prof = _get_profile(cfg, args.profile)
    scope = _default_scope(prof, args)
    if getattr(args, "scope", ""):
        scope = str(args.scope).strip()

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        "--scope",
        scope,
        "--title",
        args.title,
        "--workstreams",
        args.workstreams or "general",
        "--risk-level",
        getattr(args, "risk_level", "") or "R1",
        "--mode",
        args.mode or "auto",
    ]
    if bool(args.dry_run):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/task_create.py", argv)

    # Hook: project bootstrap/upgrade should ensure project repo AGENTS.md contains Team-OS manual block.
    if str(scope or "").strip().startswith("project:"):
        pid = _require_project_id(str(scope).split(":", 1)[1])
        mode = str(args.mode or "").strip().lower()
        if mode in ("bootstrap", "upgrade"):
            _ensure_project_scaffold(_workspace_root(args), pid)
            _inject_project_agents_manual(args, project_id=pid, reason=f"task_new_{mode}")


def cmd_task_close(args: argparse.Namespace) -> None:
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        str(args.task_id),
    ]
    if getattr(args, "scope", ""):
        argv += ["--scope", str(args.scope).strip()]
    if bool(getattr(args, "skip_tests", False)):
        argv.append("--skip-tests")
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/task_close.py", argv)


def cmd_task_ship(args: argparse.Namespace) -> None:
    """
    Enforce close -> commit -> push discipline for one task.
    """
    repo_root = _find_openteam_repo_root()
    if not repo_root:
        raise RuntimeError("Cannot find OpenTeam repo root. Set env OPENTEAM_REPO_PATH or run from within the repo.")

    argv = [
        "--repo-root",
        str(repo_root),
        "--workspace-root",
        str(_workspace_root(args)),
        str(args.task_id),
    ]
    if getattr(args, "scope", ""):
        argv += ["--scope", str(args.scope).strip()]
    if getattr(args, "summary", ""):
        argv += ["--summary", str(args.summary).strip()]
    if getattr(args, "base", ""):
        argv += ["--base", str(args.base).strip()]
    if bool(getattr(args, "no_pr", False)):
        argv.append("--no-pr")
    if bool(getattr(args, "dry_run", False)):
        argv.append("--dry-run")
    _run_pipeline(repo_root, "scripts/pipelines/task_ship.py", argv)


def cmd_task_resume(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    payload = {"task_id": args.task_id or None, "all": bool(args.all)}
    out = _http_json("POST", base + "/v1/recovery/resume", payload, timeout_sec=120)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_improvement_targets(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    query: dict[str, str] = {}
    project_id = str(getattr(args, "project_id", "") or _default_project_id(prof, args)).strip()
    if project_id:
        query["project_id"] = project_id
    if bool(getattr(args, "enabled_only", False)):
        query["enabled_only"] = "1"
    import urllib.parse
    url = base + "/v1/improvement/targets"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    out = _http_json("GET", url)
    targets = list(out.get("targets") or [])
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"targets_total={len(targets)}")
    if not targets:
        print("(none)")
        return
    rows: list[list[str]] = []
    for t in targets:
        rows.append(
            [
                str(t.get("target_id") or ""),
                str(t.get("project_id") or ""),
                "enabled" if bool(t.get("enabled")) else "disabled",
                str(t.get("repo_locator") or "")[:48],
                str(t.get("repo_root") or "")[:56],
                str(t.get("display_name") or "")[:40],
            ]
        )
    print(_fmt_table(["target_id", "project_id", "state", "repo_locator", "repo_root", "display_name"], rows))


def cmd_improvement_target_add(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    repo_path = str(getattr(args, "repo_path", "") or "").strip()
    payload = {
        "target_id": str(getattr(args, "target_id", "") or "").strip() or None,
        "project_id": _default_project_id(prof, args),
        "display_name": str(getattr(args, "display_name", "") or "").strip() or None,
        "repo_path": str(Path(repo_path).expanduser().resolve()) if repo_path else None,
        "repo_url": str(getattr(args, "repo_url", "") or "").strip() or None,
        "repo_locator": str(getattr(args, "repo_locator", "") or "").strip() or None,
        "default_branch": str(getattr(args, "default_branch", "") or "").strip() or None,
        "enabled": not bool(getattr(args, "disable", False)),
        "auto_discovery": bool(getattr(args, "auto_discovery", False)),
        "auto_delivery": bool(getattr(args, "auto_delivery", False)),
        "ship_enabled": bool(getattr(args, "ship_enabled", False)),
        "workstream_id": str(getattr(args, "workstream", "") or "general").strip() or "general",
    }
    out = _http_json("POST", base + "/v1/improvement/targets", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    target = out.get("target") or {}
    print(f"ok={bool(out.get('ok'))}")
    print(f"target_id={target.get('target_id','')}")
    print(f"project_id={target.get('project_id','')}")
    print(f"repo_locator={target.get('repo_locator','')}")
    print(f"repo_root={target.get('repo_root','')}")


def cmd_openclaw_status(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    out = _http_json("GET", base + "/v1/openclaw/status")
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"openclaw.available={bool(out.get('available'))}")
    print(f"openclaw.enabled={bool(out.get('enabled'))}")
    print(f"openclaw.configured={bool(out.get('configured'))}")
    print(f"openclaw.bin_path={str(out.get('bin_path') or '')}")
    print(f"openclaw.config_file={str(out.get('config_file') or '')}")
    print(f"openclaw.channel={str(out.get('channel') or '')}")
    print(f"openclaw.target={str(out.get('target') or '')}")
    print(f"openclaw.gateway_mode={str(out.get('gateway_mode') or '')}")
    print(f"openclaw.gateway_url={str(out.get('gateway_url') or '')}")
    print(f"openclaw.gateway_transport={str(out.get('gateway_transport') or '')}")
    print(f"openclaw.gateway_state_dir={str(out.get('gateway_state_dir') or '')}")
    print(f"openclaw.allow_insecure_private_ws={bool(out.get('allow_insecure_private_ws'))}")
    print(f"openclaw.path_patterns={','.join([str(x) for x in (out.get('path_patterns') or [])])}")
    print(f"openclaw.event_types={','.join([str(x) for x in (out.get('event_types') or [])])}")
    health = out.get("health") or {}
    print(f"openclaw.health_ok={bool(health.get('ok'))}")
    state = out.get("state") or {}
    print(f"openclaw.cursor={state.get('cursor', 0)}")
    print(f"openclaw.last_run_at={state.get('last_run_at', '')}")
    print(f"openclaw.last_error={state.get('last_error', '')}")


def cmd_openclaw_config(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    enabled = None
    if bool(getattr(args, "enable", False)):
        enabled = True
    elif bool(getattr(args, "disable", False)):
        enabled = False
    payload = {
        "enabled": enabled,
        "channel": str(getattr(args, "channel", "") or "").strip() or None,
        "target": str(getattr(args, "target", "") or "").strip() or None,
        "gateway_mode": str(getattr(args, "gateway_mode", "") or "").strip() or None,
        "gateway_url": str(getattr(args, "gateway_url", "") or "").strip() or None,
        "gateway_token": str(getattr(args, "gateway_token", "") or "").strip() or None,
        "gateway_password": str(getattr(args, "gateway_password", "") or "").strip() or None,
        "gateway_transport": str(getattr(args, "gateway_transport", "") or "").strip() or None,
        "gateway_state_dir": str(getattr(args, "gateway_state_dir", "") or "").strip() or None,
        "allow_insecure_private_ws": True if bool(getattr(args, "allow_insecure_private_ws", False)) else (False if bool(getattr(args, "disallow_insecure_private_ws", False)) else None),
        "path_patterns": [str(x).strip() for x in (getattr(args, "path", []) or []) if str(x).strip()] or None,
        "event_types": [str(x).strip() for x in (getattr(args, "event_type", []) or []) if str(x).strip()] or None,
        "exclude_event_types": [str(x).strip() for x in (getattr(args, "exclude_event_type", []) or []) if str(x).strip()] or None,
        "message_prefix": str(getattr(args, "message_prefix", "") or "").strip() or None,
    }
    out = _http_json("POST", base + "/v1/openclaw/config", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    cfg = out.get("config") or {}
    print(f"openclaw.enabled={bool(cfg.get('enabled'))}")
    print(f"openclaw.channel={str(cfg.get('channel') or '')}")
    print(f"openclaw.target={str(cfg.get('target') or '')}")
    print(f"openclaw.gateway_mode={str(cfg.get('gateway_mode') or '')}")
    print(f"openclaw.gateway_url={str(cfg.get('gateway_url') or '')}")
    print(f"openclaw.gateway_transport={str(cfg.get('gateway_transport') or '')}")
    print(f"openclaw.gateway_state_dir={str(cfg.get('gateway_state_dir') or '')}")
    print(f"openclaw.allow_insecure_private_ws={bool(cfg.get('allow_insecure_private_ws'))}")
    print(f"openclaw.path_patterns={','.join([str(x) for x in (cfg.get('path_patterns') or [])])}")
    print(f"openclaw.event_types={','.join([str(x) for x in (cfg.get('event_types') or [])])}")


def cmd_openclaw_test(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    payload = {
        "message": str(getattr(args, "message", "") or "").strip() or "OpenTeam OpenClaw test message",
        "channel": str(getattr(args, "channel", "") or "").strip() or None,
        "target": str(getattr(args, "target", "") or "").strip() or None,
        "path": str(getattr(args, "path", "") or "").strip() or None,
        "dry_run": bool(getattr(args, "dry_run", False)),
    }
    out = _http_json("POST", base + "/v1/openclaw/report/test", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"channel={str(out.get('channel') or '')}")
    print(f"target={str(out.get('target') or '')}")
    print(out.get("message") or "")


def cmd_openclaw_sweep(args: argparse.Namespace) -> None:
    base, _prof = _base_url(args)
    payload = {
        "dry_run": bool(getattr(args, "dry_run", False)),
        "limit": int(getattr(args, "limit", 100) or 100),
    }
    out = _http_json("POST", base + "/v1/openclaw/sweep", payload)
    if bool(getattr(args, "json", False)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(f"ok={bool(out.get('ok'))}")
    print(f"scanned={out.get('scanned', 0)}")
    print(f"sent={out.get('sent', 0)}")
    print(f"skipped={out.get('skipped', 0)}")
    errs = out.get("errors") or []
    print(f"errors={len(errs)}")
