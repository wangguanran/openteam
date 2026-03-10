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


def _repo_root_from_script() -> Path:
    # team-os/scripts/policy_check.py -> repo root is 1 parent up
    return Path(__file__).resolve().parents[1]


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


def _missing_phrases(path: Path, needles: list[str]) -> list[str]:
    if not path.exists():
        return [f"(missing file) {x}" for x in needles]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [f"(unreadable file) {x}" for x in needles]
    return [x for x in needles if x not in text]


def _run_repo_purity(repo_root: Path) -> dict[str, Any]:
    gov = repo_root / "scripts" / "governance"
    script = gov / "check_repo_purity.py"
    if not script.exists():
        return {
            "ok": False,
            "violations": [
                {"kind": "CHECKER_MISSING", "path": str(script), "detail": "scripts/governance/check_repo_purity.py missing"}
            ],
        }
    if str(gov) not in sys.path:
        sys.path.insert(0, str(gov))
    try:
        import check_repo_purity  # type: ignore

        out = check_repo_purity.check_repo_purity(repo_root)  # type: ignore[attr-defined]
        if isinstance(out, dict):
            return out
    except Exception as e:
        return {
            "ok": False,
            "violations": [{"kind": "CHECK_FAILED", "path": str(script), "detail": str(e)[:200]}],
        }
    return {
        "ok": False,
        "violations": [{"kind": "CHECK_FAILED", "path": str(script), "detail": "unexpected checker output"}],
    }


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

    # 3) canonical repo purity check (hard gate for runtime-root contract).
    purity = _run_repo_purity(repo_root)
    purity_violations = list(purity.get("violations") or [])
    facts["repo_purity_ok"] = bool(purity.get("ok"))
    facts["repo_purity_violations"] = len(purity_violations)
    if purity_violations:
        facts["repo_purity_violation_sample"] = purity_violations[:20]
    if not bool(purity.get("ok")):
        sample = [f"{str(v.get('kind') or '')}:{str(v.get('path') or '')}" for v in purity_violations[:10]]
        failures.append(f"repo purity violations detected ({len(purity_violations)}): {sample}")

    # 4) runtime template should mount Workspace (for containers)
    compose_tpl = repo_root / "scaffolds" / "runtime" / "docker-compose.yml"
    if compose_tpl.exists():
        text = compose_tpl.read_text(encoding="utf-8", errors="replace")
        if "TEAMOS_WORKSPACE_ROOT" not in text:
            failures.append("runtime template missing TEAMOS_WORKSPACE_ROOT env (workspace support)")
        if "/teamos-workspace" not in text:
            failures.append("runtime template missing /teamos-workspace mount (workspace support)")
    else:
        warnings.append("runtime template docker-compose.yml missing (cannot verify workspace mount policy)")

    # 5) AGENTS/governance docs must codify the canonical task workflow.
    # This prevents drift back to ad-hoc changes that bypass the task gate.
    doc_checks: list[tuple[Path, list[str]]] = [
        (repo_root / "AGENTS.md", ["./teamos task new --scope teamos", "./teamos task close"]),
        (repo_root / "docs" / "GOVERNANCE.md", ["./teamos task close"]),
        (repo_root / "docs" / "EXECUTION_RUNBOOK.md", ["./teamos task new", "./teamos task close"]),
    ]
    for p, needles in doc_checks:
        missing_phrases = _missing_phrases(p, needles)
        if missing_phrases:
            failures.append(f"{p} missing required phrases: {missing_phrases}")

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
