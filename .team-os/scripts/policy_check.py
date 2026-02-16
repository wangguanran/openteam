#!/usr/bin/env python3
"""
Team OS policy checks (local, no remote writes).

This script codifies non-negotiable norms that should not live only in docs:
- No secrets in git
- Repo vs Workspace separation (team-os repo stays repo-pure)
- Runtime template must mount Workspace for all project truth sources
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _repo_root_from_script() -> Path:
    # team-os/.team-os/scripts/policy_check.py -> repo root is 2 parents up
    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _git_ls_files(repo_root: Path) -> list[str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        return [x.strip() for x in out.splitlines() if x.strip()]
    except Exception:
        return []


def _gitignore_contains(repo_root: Path, needle: str) -> bool:
    p = repo_root / ".gitignore"
    if not p.exists():
        return False
    try:
        return needle in p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    failures: list[str]
    warnings: list[str]
    facts: dict[str, Any]


def run_checks(*, repo_root: Path) -> CheckResult:
    failures: list[str] = []
    warnings: list[str] = []
    facts: dict[str, Any] = {"repo_root": str(repo_root)}

    # 1) gitignore coverage (best-effort)
    gi_required = [
        ".env",
        ".codex/",
        "auth.json",
        "*_token*",
        "*credentials*",
        ".secrets/",
        "sshpass*",
    ]
    missing = [x for x in gi_required if not _gitignore_contains(repo_root, x)]
    if missing:
        failures.append(f".gitignore missing patterns: {missing}")
    facts["gitignore_missing_patterns"] = missing

    # 2) no secrets-like files tracked by git
    tracked = _git_ls_files(repo_root)
    facts["git_tracked_files"] = len(tracked)
    bad = []
    for f in tracked:
        low = f.lower()
        if low == ".env" or low.startswith(".env."):
            bad.append(f)
        if low.endswith("auth.json") or "/auth.json" in low:
            bad.append(f)
        if "/.codex/" in low or low.startswith(".codex/"):
            bad.append(f)
        if "_token" in low or "credentials" in low:
            # not always a secret, but tracked secret-ish filenames are a strong smell.
            warnings.append(f"tracked suspicious filename: {f}")
    if bad:
        failures.append(f"tracked secret files found: {sorted(set(bad))}")

    # 3) external projects must not store truth source under team-os/
    # Governance: team-os repo must NOT contain any project-scoped truth-source artifacts.
    # All project truth sources live under Workspace (default: ~/.teamos/workspace).
    if (repo_root / "docs" / "requirements").exists():
        failures.append("repo contains docs/requirements (project requirements must live in Workspace; teamos self lives under docs/teamos/requirements)")
    if (repo_root / ".team-os" / "ledger" / "conversations").exists():
        warnings.append("repo contains .team-os/ledger/conversations (conversations may include sensitive content; prefer Workspace or local-only)")

    # 4) .team-os/state/projects.yaml must not act as an external project registry anymore.
    # Keep only teamos entry (backwards compatibility).
    projects_path = repo_root / ".team-os" / "state" / "projects.yaml"
    proj_doc = _read_yaml(projects_path)
    projects = list(proj_doc.get("projects") or [])
    facts["projects_yaml_entries"] = len(projects)
    extra = [str(p.get("project_id") or "").strip() for p in projects if str(p.get("project_id") or "").strip() and str(p.get("project_id") or "").strip() != "teamos"]
    if extra:
        failures.append(f"projects.yaml contains non-teamos entries (must move projects to Workspace): {extra[:10]}")

    # 5) runtime template should mount Workspace (for containers)
    compose_tpl = repo_root / ".team-os" / "templates" / "runtime" / "docker-compose.yml"
    if compose_tpl.exists():
        text = compose_tpl.read_text(encoding="utf-8", errors="replace")
        if "TEAMOS_WORKSPACE_ROOT" not in text:
            failures.append("runtime template missing TEAMOS_WORKSPACE_ROOT env (workspace support)")
        if "/teamos-workspace" not in text:
            failures.append("runtime template missing /teamos-workspace mount (workspace support)")
    else:
        warnings.append("runtime template docker-compose.yml missing (cannot verify workspace mount policy)")

    ok = not failures
    return CheckResult(ok=ok, failures=failures, warnings=warnings, facts=facts)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Team OS policy checks (local, safe)")
    ap.add_argument("--repo-root", default="", help="override Team OS repo root (default: derived from script path)")
    ap.add_argument("--json", action="store_true", help="output machine-readable JSON")
    ap.add_argument("--quiet", action="store_true", help="only print one-line summary (human mode)")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).expanduser().resolve() if str(args.repo_root).strip() else _repo_root_from_script()
    res = run_checks(repo_root=repo_root)

    if args.json:
        obj = {"ok": res.ok, "failures": res.failures, "warnings": res.warnings, "facts": res.facts}
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        if not args.quiet:
            print(f"policy_check.repo_root={repo_root}")
        if res.failures and (not args.quiet):
            print("FAILURES:")
            for x in res.failures:
                print("- " + x)
        if res.warnings and (not args.quiet):
            print("WARNINGS:")
            for x in res.warnings:
                print("- " + x)
        print(f"policy_check.ok={res.ok} failures={len(res.failures)} warnings={len(res.warnings)}")

    return 0 if res.ok else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
