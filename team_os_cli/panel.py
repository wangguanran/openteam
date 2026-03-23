"""Panel subcommand handlers."""
from __future__ import annotations

import argparse
import json
import urllib.parse
from typing import Any

from team_os_cli._shared import _base_url, _default_project_id
from team_os_cli.http import _http_json


def cmd_panel_show(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)

    cfg = _http_json("GET", base + "/v1/panel/github/config", timeout_sec=10)
    health = _http_json("GET", base + "/v1/panel/github/health?project_id=" + urllib.parse.quote(project_id), timeout_sec=10)

    print(f"profile={prof['name']} project_id={project_id}")
    print(f"mapping_path={cfg.get('mapping_path','')}")
    print(f"mapping_sha256={cfg.get('mapping_sha256','')}")
    print()

    proj_cfg = None
    for p in (cfg.get("projects") or []):
        if str(p.get("project_id")) == project_id:
            proj_cfg = p
            break
    if not proj_cfg:
        print("panel: NOT_CONFIGURED (no mapping entry for this project_id)")
    else:
        print("panel: github_projects")
        print(f"  owner_type={proj_cfg.get('owner_type','')}")
        print(f"  owner={proj_cfg.get('owner','')}")
        if proj_cfg.get("repo"):
            print(f"  repo={proj_cfg.get('repo','')}")
        print(f"  project_number={proj_cfg.get('project_number','')}")
        if proj_cfg.get("project_url"):
            print(f"  url={proj_cfg.get('project_url')}")
        print()

    last = (health.get("last_sync") or {}) if isinstance(health.get("last_sync"), dict) else {}
    summ = (health.get("summary") or {}) if isinstance(health.get("summary"), dict) else {}
    auto = (health.get("auto_sync") or {}) if isinstance(health.get("auto_sync"), dict) else {}
    if summ:
        print(f"sync_runs_total={summ.get('runs_total')} failures_total={summ.get('failures_total')}")
    if auto:
        print(f"auto_sync.enabled={auto.get('enabled')} interval_sec={auto.get('interval_sec')} debounce_sec={auto.get('debounce_sec')}")
    if "writes_enabled" in health:
        print(f"writes_enabled={health.get('writes_enabled')}")
    if "needs_full_resync" in health:
        print(f"needs_full_resync={health.get('needs_full_resync')}")

    if last:
        print("last_sync:")
        print(f"  ts_end={last.get('ts_end','')}")
        print(f"  ok={last.get('ok')}")
        print(f"  mode={last.get('mode','')}")
        print(f"  dry_run={last.get('dry_run')}")
        print(f"  stats={json.dumps(last.get('stats') or {}, ensure_ascii=False)}")
        if last.get("error"):
            print(f"  error={str(last.get('error'))[:200]}")
    else:
        print("last_sync: (none)")


def cmd_panel_health(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    out = _http_json("GET", base + "/v1/panel/github/health?project_id=" + urllib.parse.quote(project_id), timeout_sec=10)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_panel_sync(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    payload = {
        "project_id": project_id,
        "mode": "full" if args.full else "incremental",
        "dry_run": bool(args.dry_run),
    }
    out = _http_json("POST", base + "/v1/panel/github/sync", payload, timeout_sec=300)
    stats = out.get("stats") or {}
    print(f"project_id={out.get('project_id','')} mode={out.get('mode','')} dry_run={out.get('dry_run')}")
    if out.get("project_url"):
        print(f"project_url={out.get('project_url')}")
    print(f"stats={json.dumps(stats, ensure_ascii=False)}")
    if out.get("errors"):
        print("errors:")
        for e in out["errors"][:20]:
            print(f"- {str(e)[:300]}")
    if out.get("actions"):
        print("actions:")
        for a in out["actions"][:50]:
            print(f"- {a.get('action')} {a.get('kind')} {a.get('key')} {a.get('status')}".rstrip())


def cmd_panel_open(args: argparse.Namespace) -> None:
    base, prof = _base_url(args)
    project_id = _default_project_id(prof, args)
    cfg = _http_json("GET", base + "/v1/panel/github/config", timeout_sec=10)
    proj_cfg = None
    for p in (cfg.get("projects") or []):
        if str(p.get("project_id")) == project_id:
            proj_cfg = p
            break
    url = (proj_cfg or {}).get("project_url") or ""
    if not url:
        print("panel_url: (missing) - set projects.<project_id>.project_url in mapping.yaml")
    else:
        print(url)
