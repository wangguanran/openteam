#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, default_runtime_root, resolve_repo_root, resolve_workspace_root, runtime_state_root
from _db import connect, get_db_url
from workspace_doctor import check_workspace


def _http_json(url: str, *, timeout_sec: int = 5) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise PipelineError(f"HTTP {e.code} {e.reason}: {body[:300]}") from e
    except Exception as e:
        raise PipelineError(f"HTTP request failed: {e}") from e


def _load_base_url(*, profile: str = "") -> str:
    cfg = Path.home() / ".openteam" / "config.toml"
    if not cfg.exists():
        return "http://127.0.0.1:8787"
    try:
        try:
            import tomllib as tomli  # type: ignore
        except Exception:
            import tomli  # type: ignore

        doc = tomli.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return "http://127.0.0.1:8787"

    cur = str(profile or doc.get("current_profile") or "").strip()
    profiles = doc.get("profiles") or {}
    if not cur:
        cur = "local" if "local" in profiles else (sorted(list(profiles.keys()))[0] if profiles else "")
    p = (profiles or {}).get(cur) or {}
    base = str(p.get("base_url") or "").strip().rstrip("/")
    return base or "http://127.0.0.1:8787"


def _codex_status() -> tuple[bool, str]:
    if shutil.which("codex") is None:
        return (False, "MISS (install codex CLI, then: codex login)")
    p = subprocess.run(["codex", "login", "status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    msg = (p.stdout or p.stderr or "").strip().splitlines()
    head = msg[0][:200] if msg else ""
    if p.returncode == 0:
        return (True, head or "OK")
    return (False, head or "FAIL (run: codex login --device-auth)")


def _gh_status() -> tuple[bool, str]:
    if shutil.which("gh") is None:
        tok = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
        return (bool(tok), "OK (env token present)" if tok else "MISS (install gh CLI, then: gh auth login)")
    p = subprocess.run(["gh", "auth", "status", "-h", "github.com"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode == 0:
        return (True, "OK logged_in=true")
    head = ((p.stdout or "") + "\n" + (p.stderr or "")).strip().splitlines()
    return (False, "FAIL (run: gh auth login) " + (head[0][:200] if head else ""))


def _db_check(repo_root: Path) -> dict[str, Any]:
    """
    Postgres connectivity + migrations check.
    - When OPENTEAM_DB_URL is unset: SKIP (ok=true)
    - When set: require psycopg + connection + schema_migrations present.
    """
    dsn = get_db_url()
    if not dsn:
        return {"ok": True, "status": "SKIP", "reason": "OPENTEAM_DB_URL not set"}

    try:
        conn = connect(dsn)
    except Exception as e:
        return {
            "ok": False,
            "status": "FAIL",
            "reason": "db_driver_or_connect_failed",
            "error": str(e)[:300],
            "hint": 'Install: python3 -m pip install --user "psycopg[binary]"',
        }

    try:
        with conn.cursor() as cur:
            try:
                rows = cur.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version ASC").fetchall()
            except Exception as e:
                return {
                    "ok": False,
                    "status": "FAIL",
                    "reason": "migrations_missing",
                    "error": str(e)[:200],
                    "hint": "Run: openteam db migrate",
                }
        vers = []
        for r in rows or []:
            try:
                vers.append(str(r.get("version") or "").strip())
            except Exception:
                continue
        vers = [v for v in vers if v]
        return {"ok": True, "status": "OK", "dsn": "set", "migrations": vers[-20:]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _default_team_check(repo_root: Path) -> dict[str, Any]:
    """
    Best-effort runtime-state check for the default configured team.
    Persistent state now lives in the runtime DB / control-plane status, not local JSON files.
    """
    runtime_override = "" if str(os.getenv("OPENTEAM_RUNTIME_ROOT") or "").strip() else str(default_runtime_root())
    state_root = runtime_state_root(override=runtime_override)
    return {
        "ok": True,
        "runtime_state_root": str(state_root),
        "state_backend": "postgres" if str(os.getenv("OPENTEAM_DB_URL") or "").strip() else "sqlite_or_runtime_db",
        "last_run": {},
    }


def _llm_config_check() -> dict[str, Any]:
    base = str(
        os.getenv("OPENTEAM_LLM_BASE_URL")
        or ""
    ).strip()
    key = str(os.getenv("OPENTEAM_LLM_API_KEY") or "").strip()
    model = str(os.getenv("OPENTEAM_LLM_MODEL") or "openai/gpt-5.4").strip()
    needs_codex = "codex" in model.lower()
    codex_ok, codex_msg = _codex_status()
    codex_oauth_ready = bool(needs_codex and codex_ok)
    api_key_ready = bool(base and key)
    ok = bool(api_key_ready or codex_oauth_ready)
    auth_strategy = ""
    if codex_oauth_ready:
        auth_strategy = "codex_oauth"
    elif api_key_ready:
        auth_strategy = "api_key"
    masked = ""
    if key:
        masked = ("*" * len(key)) if len(key) <= 8 else f"{key[:4]}***{key[-4:]}"
    out = {
        "ok": ok,
        "model": model,
        "auth_strategy": auth_strategy,
        "codex_login_status": codex_msg,
        "codex_oauth_ready": codex_oauth_ready,
        "base_url_set": bool(base),
        "api_key_set": bool(key),
        "base_url": base,
        "api_key_masked": masked,
        "required": [
            "Codex OAuth login via `codex login` for codex models",
            "or OPENTEAM_LLM_BASE_URL + OPENTEAM_LLM_API_KEY",
        ],
    }
    if not ok:
        out["hint"] = "run `codex login` for codex models, or export OPENTEAM_LLM_BASE_URL=... and OPENTEAM_LLM_API_KEY=..."
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenTeam doctor (deterministic local checks)")
    add_default_args(ap)
    ap.add_argument("--profile", default="", help="profile name (from ~/.openteam/config.toml)")
    ap.add_argument("--base-url", default="", help="override control plane base url")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    ws = resolve_workspace_root(args)

    ok = True
    report: dict[str, Any] = {"repo_root": str(repo), "workspace_root": str(ws)}

    # Repo purity (wrapper prints; also capture structured result by running checker directly).
    purity = {"ok": True, "violations": 0}
    try:
        import sys

        sys.path.insert(0, str(repo / "scripts" / "governance"))
        import check_repo_purity  # type: ignore

        out = check_repo_purity.check_repo_purity(repo)  # type: ignore[attr-defined]
        purity = {"ok": bool(out.get("ok")), "violations": len(out.get("violations") or [])}
    except Exception as e:
        purity = {"ok": False, "violations": -1, "error": str(e)[:200]}
    if not purity.get("ok"):
        ok = False
    report["repo_purity"] = purity

    # Workspace doctor
    w = check_workspace(repo_root=repo, workspace_root=ws)
    if not bool(w.get("ok")):
        ok = False
    report["workspace"] = w

    # OAuth + GitHub
    codex_ok, codex_msg = _codex_status()
    gh_ok, gh_msg = _gh_status()
    if not codex_ok:
        ok = False
    if not gh_ok:
        # gh is optional (env token ok), but warn by failing only if neither present.
        ok = False
    report["codex"] = {"ok": codex_ok, "message": codex_msg}
    report["gh"] = {"ok": gh_ok, "message": gh_msg}

    # LLM config is mandatory for startup/runtime readiness.
    llm = _llm_config_check()
    report["llm_config"] = llm
    if not bool(llm.get("ok")):
        ok = False

    # Postgres DB (shared hub). Optional unless OPENTEAM_DB_URL is set.
    db = _db_check(repo)
    report["postgres_db"] = db
    if not bool(db.get("ok")):
        ok = False

    # Runtime-managed default team status (informational).
    report["default_team"] = _default_team_check(repo)

    # Control plane health + API coverage (best-effort; should pass when runtime matches repo template).
    base = str(args.base_url or "").strip().rstrip("/") or _load_base_url(profile=str(args.profile or ""))
    control_plane: dict[str, Any] = {"base_url": base}
    report["control_plane"] = control_plane
    try:
        hz = _http_json(base + "/healthz", timeout_sec=5)
        st = _http_json(base + "/v1/status", timeout_sec=5)
        control_plane.update({"ok": True, "healthz": hz.get("status", ""), "instance_id": st.get("instance_id", "")})
        teams_value = st.get("teams") if isinstance(st.get("teams"), dict) else {}
        default_team_id = str(st.get("default_team_id") or "").strip()
        if not default_team_id and isinstance(teams_value, dict) and teams_value:
            default_team_id = sorted(str(key) for key in teams_value.keys() if str(key).strip())[0]
        default_team_value = teams_value.get(default_team_id) if isinstance(teams_value, dict) else {}
        if isinstance(default_team_value, dict):
            report["default_team"]["team_id"] = default_team_id
            report["default_team"]["last_run"] = dict((default_team_value or {}).get("last_run") or {})
            report["default_team"]["control_plane_summary"] = default_team_value or {}
        trs_value = st.get("task_run_sync")
        if isinstance(trs_value, dict):
            trs = dict(trs_value)
            control_plane["task_run_sync"] = trs
            if not bool(trs.get("ok")):
                ok = False
        else:
            # Runtime is expected to expose task/run consistency in /v1/status.
            control_plane["task_run_sync"] = {"ok": False, "missing": True}
            ok = False
        spec = _http_json(base + "/openapi.json", timeout_sec=5)
        paths = spec.get("paths") or {}
        required = [
            "/v1/status",
            "/v1/agents",
            "/v1/runs",
            "/v1/runs/start",
            "/v1/tasks",
            "/v1/focus",
            "/v1/chat",
            "/v1/requirements",
            "/v1/hub/status",
            "/v1/hub/migrations",
            "/v1/hub/locks",
            "/v1/hub/approvals",
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
            "/v1/teams",
        ]
        missing = [p for p in required if p not in paths]
        control_plane["api_coverage"] = {"ok": not missing, "missing_paths": missing[:50]}
        if missing:
            ok = False
    except Exception as e:
        control_plane.update({"ok": False, "error": str(e)[:200]})
        ok = False

    if args.json:
        print(json.dumps({"ok": ok, "report": report}, ensure_ascii=False, indent=2))
    else:
        print(f"repo_purity.ok={str(bool(purity.get('ok'))).lower()} violations={purity.get('violations')}")
        print(f"profile={str(args.profile or '').strip() or 'default'} base_url={base}")
        cp_value = report.get("control_plane")
        cp: dict[str, Any] = cp_value if isinstance(cp_value, dict) else {}
        if cp.get("ok"):
            print(f"control_plane: OK instance_id={cp.get('instance_id','')}")
            cov_value = cp.get("api_coverage")
            cov: dict[str, Any] = cov_value if isinstance(cov_value, dict) else {}
            if cov.get("ok"):
                print("control_plane_api: OK")
            else:
                miss = cov.get("missing_paths") or []
                print(f"control_plane_api: FAIL missing_paths={len(miss)} sample={(miss[:3])}")
            trs_value = cp.get("task_run_sync")
            trs: dict[str, Any] = trs_value if isinstance(trs_value, dict) else {}
            if trs.get("ok"):
                print("task_run_sync: OK")
            else:
                miss_runs = trs.get("missing_run_for_tasks") or []
                orphans = trs.get("orphan_active_runs") or []
                missing_field = bool(trs.get("missing"))
                if missing_field:
                    print("task_run_sync: FAIL missing_in_status=true")
                else:
                    print(
                        "task_run_sync: FAIL "
                        f"missing_run_for_tasks={len(miss_runs)} orphan_active_runs={len(orphans)}"
                    )
        else:
            print(f"control_plane: FAIL {cp.get('error','')}")
        print(f"codex: {'OK' if codex_ok else 'FAIL'} {codex_msg}")
        print(f"gh: {'OK' if gh_ok else 'FAIL'} {gh_msg}")
        dbs_value = report.get("postgres_db")
        dbs: dict[str, Any] = dbs_value if isinstance(dbs_value, dict) else {}
        print(f"db: {str(dbs.get('status') or '').strip() or ('OK' if dbs.get('ok') else 'FAIL')} {dbs.get('reason','')}")
        default_team_value = report.get("default_team")
        default_team: dict[str, Any] = default_team_value if isinstance(default_team_value, dict) else {}
        if default_team:
            last_value = default_team.get("last_run")
            last: dict[str, Any] = last_value if isinstance(last_value, dict) else {}
            print(
                "default_team: "
                f"team_id={str(default_team.get('team_id') or '').strip() or 'unknown'} "
                f"backend={str(default_team.get('state_backend') or '').strip() or 'unknown'} "
                f"status={str(last.get('status') or '').strip() or 'UNKNOWN'} "
                f"ts={str(last.get('ts') or '').strip()}"
            )
        print(f"workspace_root={ws}")
        print(f"workspace: {'OK' if w.get('ok') else 'FAIL'}")
        print(f"repo: {'OK' if purity.get('ok') else 'FAIL'}")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
