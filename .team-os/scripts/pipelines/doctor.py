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
from typing import Any, Optional

from _common import PipelineError, add_default_args, resolve_repo_root, resolve_workspace_root
from repo_purity_check import main as _repo_purity_main
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
    cfg = Path.home() / ".teamos" / "config.toml"
    if not cfg.exists():
        return "http://127.0.0.1:8787"
    try:
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Team OS doctor (deterministic local checks)")
    add_default_args(ap)
    ap.add_argument("--profile", default="", help="profile name (from ~/.teamos/config.toml)")
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

        sys.path.insert(0, str(repo / ".team-os" / "scripts" / "governance"))
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

    # Control plane health + API coverage (best-effort; should pass when runtime matches repo template).
    base = str(args.base_url or "").strip().rstrip("/") or _load_base_url(profile=str(args.profile or ""))
    report["control_plane"] = {"base_url": base}
    try:
        hz = _http_json(base + "/healthz", timeout_sec=5)
        st = _http_json(base + "/v1/status", timeout_sec=5)
        report["control_plane"].update({"ok": True, "healthz": hz.get("status", ""), "instance_id": st.get("instance_id", "")})
        spec = _http_json(base + "/openapi.json", timeout_sec=5)
        paths = spec.get("paths") or {}
        required = [
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
        missing = [p for p in required if p not in paths]
        report["control_plane"]["api_coverage"] = {"ok": not missing, "missing_paths": missing[:50]}
        if missing:
            ok = False
    except Exception as e:
        report["control_plane"].update({"ok": False, "error": str(e)[:200]})
        ok = False

    if args.json:
        print(json.dumps({"ok": ok, "report": report}, ensure_ascii=False, indent=2))
    else:
        print(f"repo_purity.ok={str(bool(purity.get('ok'))).lower()} violations={purity.get('violations')}")
        print(f"profile={str(args.profile or '').strip() or 'default'} base_url={base}")
        cp = report.get("control_plane") or {}
        if cp.get("ok"):
            print(f"control_plane: OK instance_id={cp.get('instance_id','')}")
            cov = (cp.get("api_coverage") or {}) if isinstance(cp.get("api_coverage"), dict) else {}
            if cov.get("ok"):
                print("control_plane_api: OK")
            else:
                miss = cov.get("missing_paths") or []
                print(f"control_plane_api: FAIL missing_paths={len(miss)} sample={(miss[:3])}")
        else:
            print(f"control_plane: FAIL {cp.get('error','')}")
        print(f"codex: {'OK' if codex_ok else 'FAIL'} {codex_msg}")
        print(f"gh: {'OK' if gh_ok else 'FAIL'} {gh_msg}")
        print(f"workspace_root={ws}")
        print(f"workspace: {'OK' if w.get('ok') else 'FAIL'}")
        print(f"repo: {'OK' if purity.get('ok') else 'FAIL'}")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

