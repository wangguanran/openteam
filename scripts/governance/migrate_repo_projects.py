#!/usr/bin/env python3
"""
Migrate legacy project artifacts OUT of the openteam git repo into Workspace.

Default: dry-run (prints planned moves only).
Apply:   --force (moves files; keeps best-effort backups on conflicts).

This is intentionally conservative:
- It never touches OpenTeam self scope (project_id=openteam).
- It never deletes data; it moves/copies with conflict backups.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


def _repo_root_from_git(cwd: Path) -> Path:
    p = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if p.returncode == 0:
        out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
        if out:
            return Path(out).resolve()
    return cwd.resolve()


def _utc_ts_compact() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z").replace(":", "").replace("-", "")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _slug_project_id(pid: str) -> str:
    s = str(pid or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "project"
    return s[:60]


def _compute_project_id_map(project_ids: set[str]) -> dict[str, str]:
    """
    Compute a filesystem-safe, cross-platform project_id mapping.

    Rationale:
    - macOS (default) filesystems are case-insensitive, so "DEMO" and "demo" collide.
    - Workspace project ids are enforced as lowercase slugs for portability.
    """
    groups: dict[str, list[str]] = {}
    for pid in sorted({str(x or "").strip() for x in project_ids if str(x or "").strip()}):
        base = _slug_project_id(pid)
        groups.setdefault(base, []).append(pid)

    out: dict[str, str] = {}
    used: set[str] = set()
    for base, origs in sorted(groups.items(), key=lambda kv: kv[0]):
        # Prefer an original that already equals the base (already canonical).
        preferred = base if base in origs else origs[0]

        def reserve(candidate: str) -> str:
            c = candidate
            if not _PROJECT_ID_RE.match(c):
                c = _slug_project_id(c)
            if c in used:
                i = 2
                while f"{c}-{i}" in used:
                    i += 1
                c = f"{c}-{i}"
            used.add(c)
            return c

        out[preferred] = reserve(base)

        legacy_i = 1
        for pid in origs:
            if pid == preferred:
                continue
            suffix = "legacy" if legacy_i == 1 else f"legacy{legacy_i}"
            out[pid] = reserve(f"{base}-{suffix}")
            legacy_i += 1

    return out


def _rewrite_requirements_yaml(path: Path, *, dest_project_id: str) -> None:
    try:
        data = _read_yaml(path) or {}
        data["project_id"] = dest_project_id
        reqs = list(data.get("requirements") or [])
        for r in reqs:
            refs = list(r.get("decision_log_refs") or [])
            new_refs: list[str] = []
            for ref in refs:
                s = str(ref or "").strip()
                if not s:
                    continue
                name = Path(s).name
                if name.endswith(".md"):
                    new_refs.append(f"conflicts/{name}")
                else:
                    new_refs.append(s)
            r["decision_log_refs"] = new_refs
        data["requirements"] = reqs
        _write_yaml(path, data)
    except Exception:
        # Best-effort; never fail migration on rewrite.
        return


def _rewrite_plan_yaml(path: Path, *, dest_project_id: str) -> None:
    try:
        data = _read_yaml(path) or {}
        data["project_id"] = dest_project_id
        _write_yaml(path, data)
    except Exception:
        return


def _rewrite_task_ledger_yaml(path: Path, *, dest_project_id: str, task_id: str) -> None:
    try:
        data = _read_yaml(path) or {}
        data["project_id"] = dest_project_id

        artifacts = dict(data.get("artifacts") or {})
        artifacts["ledger"] = f"state/ledger/tasks/{task_id}.yaml"
        artifacts["logs_dir"] = f"state/logs/tasks/{task_id}/"
        data["artifacts"] = artifacts

        ev = list(data.get("evidence") or [])
        for e in ev:
            if not isinstance(e, dict):
                continue
            p = str(e.get("path") or "")
            if p.startswith(".openteam/logs/tasks/") or p.startswith(".openteam\\logs\\tasks\\"):
                e["path"] = f"state/logs/tasks/{task_id}/" + Path(p).name
        data["evidence"] = ev

        _write_yaml(path, data)
    except Exception:
        return


def _ensure_workspace_scaffold(root: Path) -> None:
    (root / "projects").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "cache").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "tmp").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(parents=True, exist_ok=True)
    cfg = root / "config" / "workspace.toml"
    if not cfg.exists():
        cfg.write_text(
            "\n".join(
                [
                    "# OpenTeam Workspace config (local; not committed)",
                    "",
                    f'workspace_root = "{root}"',
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )


def _project_state_root(workspace_root: Path, project_id: str) -> Path:
    return workspace_root / "projects" / project_id / "state"


def _dest_requirements_dir(workspace_root: Path, project_id: str) -> Path:
    return _project_state_root(workspace_root, project_id) / "requirements"


def _dest_plan_dir(workspace_root: Path, project_id: str) -> Path:
    return _project_state_root(workspace_root, project_id) / "plan"


def _dest_ledger_tasks_dir(workspace_root: Path, project_id: str) -> Path:
    return _project_state_root(workspace_root, project_id) / "ledger" / "tasks"


def _dest_logs_tasks_dir(workspace_root: Path, project_id: str) -> Path:
    return _project_state_root(workspace_root, project_id) / "logs" / "tasks"


def _dest_conversations_dir(workspace_root: Path, project_id: str) -> Path:
    return _project_state_root(workspace_root, project_id) / "ledger" / "conversations" / project_id


def _infer_project_id_from_requirements_dir(req_dir: Path) -> str:
    y = req_dir / "requirements.yaml"
    if y.exists():
        data = _read_yaml(y)
        pid = str(data.get("project_id") or "").strip()
        if pid:
            return pid
    return req_dir.name


def _safe_move(src: Path, dest: Path, *, force: bool, ts: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if not force:
            raise RuntimeError(f"dest exists (use --force): {dest}")
        bak = dest.with_name(dest.name + f".bak.{ts}")
        if dest.is_dir():
            shutil.copytree(dest, bak)
            shutil.rmtree(dest)
        else:
            shutil.copy2(dest, bak)
            dest.unlink()
    shutil.move(str(src), str(dest))


@dataclass(frozen=True)
class MoveItem:
    kind: str
    project_id: str
    src: str
    dest: str


def _infer_project_id_for_task_in_repo(tasks_dir: Path, *, task_id: str) -> str:
    try:
        base = tasks_dir / f"{task_id}.yaml"
        if base.exists():
            data = _read_yaml(base)
            pid = str(data.get("project_id") or "").strip()
            if pid:
                return pid
    except Exception:
        pass
    return ""


def _infer_project_id_for_task_in_workspace(workspace_root: Path, *, task_id: str) -> str:
    try:
        projects_dir = workspace_root / "projects"
        if not projects_dir.exists():
            return ""
        for d in sorted(projects_dir.iterdir()):
            if not d.is_dir():
                continue
            y = d / "state" / "ledger" / "tasks" / f"{task_id}.yaml"
            if y.exists():
                try:
                    data = _read_yaml(y)
                    pid = str(data.get("project_id") or d.name).strip()
                    if pid:
                        return pid
                except Exception:
                    return d.name
    except Exception:
        pass
    return ""


def plan_moves(repo_root: Path, *, workspace_root: Optional[Path] = None) -> tuple[list[MoveItem], dict[str, Any]]:
    """
    Return (move_items, facts).

    Facts include:
    - tasks_by_project: {project_id: [task_id,...]}
    """
    items: list[MoveItem] = []
    facts: dict[str, Any] = {"repo_root": str(repo_root)}

    # A) requirements dirs under docs/requirements/*
    req_root = repo_root / "docs" / "requirements"
    if req_root.exists():
        for d in sorted(req_root.iterdir()):
            if not d.is_dir():
                continue
            # OpenTeam self requirements are handled separately (in-repo relocation), never moved to workspace.
            if d.name == "openteam":
                continue
            pid = _infer_project_id_from_requirements_dir(d)
            # Move the directory contents into workspace state/requirements/
            for p in sorted(d.rglob("*")):
                if p.is_dir():
                    continue
                rel = p.relative_to(d)
                items.append(MoveItem(kind="requirements", project_id=pid, src=str(p), dest=str(rel)))

    # B) plan overlay under docs/plans/*
    plan_root = repo_root / "docs" / "plans"
    if plan_root.exists():
        for d in sorted(plan_root.iterdir()):
            if not d.is_dir():
                continue
            if d.name == "openteam":
                continue
            pid = d.name
            for p in sorted(d.rglob("*")):
                if p.is_dir():
                    continue
                rel = p.relative_to(d)
                items.append(MoveItem(kind="plan", project_id=pid, src=str(p), dest=str(rel)))

    # C) conversations under .openteam/ledger/conversations/<project_id>
    conv_root = repo_root / ".openteam" / "ledger" / "conversations"
    if conv_root.exists():
        for d in sorted(conv_root.iterdir()):
            if not d.is_dir():
                continue
            pid = d.name
            if pid == "openteam":
                continue
            for p in sorted(d.rglob("*")):
                if p.is_dir():
                    continue
                rel = p.relative_to(d)
                items.append(MoveItem(kind="conversations", project_id=pid, src=str(p), dest=str(rel)))

    # D) task ledgers + logs for non-openteam tasks
    tasks_dir = repo_root / ".openteam" / "ledger" / "tasks"
    logs_dir = repo_root / ".openteam" / "logs" / "tasks"
    tasks_by_project: dict[str, list[str]] = {}
    if tasks_dir.exists():
        for y in sorted(tasks_dir.glob("*.yaml")):
            data = _read_yaml(y)
            tid = str(data.get("id") or y.stem)
            pid = str(data.get("project_id") or "").strip() or "(missing)"
            if pid == "openteam":
                continue
            tasks_by_project.setdefault(pid, []).append(tid)
            items.append(MoveItem(kind="task_ledger", project_id=pid, src=str(y), dest=str(Path(tid + ".yaml"))))
            ldir = logs_dir / tid
            if ldir.exists() and ldir.is_dir():
                for p in sorted(ldir.rglob("*")):
                    if p.is_dir():
                        continue
                    rel = p.relative_to(ldir)
                    items.append(MoveItem(kind="task_logs", project_id=pid, src=str(p), dest=str(rel)))

        # Also migrate legacy backup ledgers that were created in-repo (ignored by git),
        # because they still contain project-scoped data and violate repo purity.
        for y in sorted(tasks_dir.glob("*.yaml.bak.*")):
            data = _read_yaml(y)
            task_id = y.name.split(".yaml", 1)[0]
            pid = str(data.get("project_id") or "").strip()
            if not pid:
                pid = _infer_project_id_for_task_in_repo(tasks_dir, task_id=task_id)
            if (not pid) and workspace_root is not None:
                pid = _infer_project_id_for_task_in_workspace(workspace_root, task_id=task_id)
            pid = pid or "(missing)"
            if pid == "openteam":
                continue
            # Keep filename as-is under an explicit backups folder.
            items.append(MoveItem(kind="task_ledger_backup", project_id=pid, src=str(y), dest=str(Path("_backups") / y.name)))
    facts["tasks_by_project"] = tasks_by_project

    # E) legacy project prompts under specs/prompts/projects (if any)
    pl = repo_root / "specs" / "prompts" / "projects"
    if pl.exists():
        for d in sorted(pl.iterdir()):
            if not d.is_dir():
                continue
            pid = d.name
            for p in sorted(d.rglob("*")):
                if p.is_dir():
                    continue
                rel = p.relative_to(d)
                items.append(MoveItem(kind="prompts_legacy", project_id=pid, src=str(p), dest=str(rel)))

    # Compute project_id mapping (cross-platform safety).
    pids = {it.project_id for it in items if it.project_id and it.project_id != "openteam"}
    facts["project_id_map"] = _compute_project_id_map(pids)

    return items, facts


def apply_moves(*, repo_root: Path, workspace_root: Path, items: list[MoveItem], force: bool, project_id_map: dict[str, str]) -> dict[str, Any]:
    """
    Execute planned moves.

    Returns stats and moved files list (truncated).
    """
    _ensure_workspace_scaffold(workspace_root)
    ts = _utc_ts_compact()
    moved: list[dict[str, Any]] = []
    errors: list[str] = []
    moved_task_log_dirs: set[str] = set()

    # Group items by kind for destination mapping.
    for it in items:
        src = Path(it.src)
        pid = it.project_id
        if pid == "openteam":
            continue
        dest_pid = str((project_id_map or {}).get(pid) or pid).strip()

        try:
            if it.kind == "requirements":
                dest = _dest_requirements_dir(workspace_root, dest_pid) / Path(it.dest)
            elif it.kind == "plan":
                dest = _dest_plan_dir(workspace_root, dest_pid) / Path(it.dest)
            elif it.kind == "conversations":
                dest = _dest_conversations_dir(workspace_root, dest_pid) / Path(it.dest)
            elif it.kind == "task_ledger":
                dest = _dest_ledger_tasks_dir(workspace_root, dest_pid) / Path(it.dest)
            elif it.kind == "task_logs":
                # src is a file inside repo logs dir; dest goes under workspace logs dir/<task_id>/...
                # task_id is inferred from parent name
                task_id = src.parent.name
                moved_task_log_dirs.add(task_id)
                dest = _dest_logs_tasks_dir(workspace_root, dest_pid) / task_id / Path(it.dest)
            elif it.kind == "task_ledger_backup":
                dest = _dest_ledger_tasks_dir(workspace_root, dest_pid) / Path(it.dest)
            elif it.kind == "prompts_legacy":
                dest = _project_state_root(workspace_root, dest_pid) / "prompts" / Path(it.dest)
            else:
                raise RuntimeError(f"unknown move kind: {it.kind}")

            _safe_move(src, dest, force=force, ts=ts)
            moved.append({"kind": it.kind, "project_id": dest_pid, "src": str(src), "dest": str(dest)})

            # Best-effort rewrites for truth sources so references match the Workspace layout.
            if it.kind == "requirements" and dest.name == "requirements.yaml":
                _rewrite_requirements_yaml(dest, dest_project_id=dest_pid)
            if it.kind == "plan" and dest.name == "plan.yaml":
                _rewrite_plan_yaml(dest, dest_project_id=dest_pid)
            if it.kind == "task_ledger" and dest.suffix == ".yaml":
                _rewrite_task_ledger_yaml(dest, dest_project_id=dest_pid, task_id=dest.stem)
            if it.kind == "task_ledger_backup":
                # Best-effort rewrite to reflect Workspace layout (even though it's a backup).
                tid = ""
                try:
                    d = _read_yaml(dest)
                    tid = str(d.get("id") or "").strip()
                except Exception:
                    tid = ""
                _rewrite_task_ledger_yaml(dest, dest_project_id=dest_pid, task_id=tid or dest.name.split(".yaml", 1)[0])
        except Exception as e:
            errors.append(f"{it.kind} {it.project_id} {it.src}: {e}")

    # Post-process: remove now-empty source directories.
    # Only remove if empty to avoid accidental deletions.
    for p in [
        repo_root / "docs" / "requirements",
        repo_root / "docs" / "plans",
        repo_root / ".openteam" / "ledger" / "conversations",
        repo_root / "prompt-library" / "projects",
    ]:
        try:
            if p.exists() and p.is_dir():
                # remove empty parents bottom-up
                for d in sorted([x for x in p.rglob("*") if x.is_dir()], key=lambda x: len(str(x)), reverse=True):
                    if d.exists() and d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                if not any(p.iterdir()):
                    p.rmdir()
        except Exception:
            pass

    # Remove empty legacy task log directories for migrated tasks.
    try:
        logs_root = repo_root / ".openteam" / "logs" / "tasks"
        for tid in sorted(moved_task_log_dirs):
            d = logs_root / tid
            if d.exists() and d.is_dir() and (not any(d.iterdir())):
                d.rmdir()
    except Exception:
        pass

    return {
        "ok": not errors,
        "moved": moved[:200],
        "moved_count": len(moved),
        "errors": errors[:50],
        "errors_count": len(errors),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Move project artifacts out of the openteam repo into Workspace")
    ap.add_argument("--repo-root", default="", help="override repo root (default: git rev-parse)")
    ap.add_argument("--workspace-root", required=True, help="workspace root (outside repo)")
    ap.add_argument("--dry-run", action="store_true", help="plan only (default)")
    ap.add_argument("--force", action="store_true", help="apply moves (high risk)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else _repo_root_from_git(Path.cwd())
    workspace_root = Path(args.workspace_root).expanduser().resolve()

    planned, facts = plan_moves(repo_root, workspace_root=workspace_root)
    project_id_map = dict(facts.get("project_id_map") or {})
    plan_out = {
        "repo_root": str(repo_root),
        "workspace_root": str(workspace_root),
        "planned_count": len(planned),
        "planned": [it.__dict__ for it in planned[:200]],
        "facts": facts,
        "dry_run": bool(args.dry_run) or (not bool(args.force)),
    }

    if args.json:
        # In json mode, always output the plan; apply results are printed after.
        print(json.dumps(plan_out, ensure_ascii=False, indent=2))
    else:
        print(f"repo_root={repo_root}")
        print(f"workspace_root={workspace_root}")
        print(f"planned_moves={len(planned)}")
        by_project: dict[str, int] = {}
        for it in planned:
            by_project[it.project_id] = by_project.get(it.project_id, 0) + 1
        if by_project:
            for pid in sorted(by_project.keys()):
                mp = project_id_map.get(pid, pid)
                print(f"- project_id={pid} -> {mp} items={by_project[pid]}")
        if planned:
            print("")
            print("sample:")
            for it in planned[:30]:
                print(f"- {it.kind} {it.project_id}: {it.src} -> (workspace) {it.dest}")

    if args.dry_run or (not args.force):
        return 0

    # Apply moves.
    res = apply_moves(repo_root=repo_root, workspace_root=workspace_root, items=planned, force=True, project_id_map=project_id_map)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("")
        print(f"apply.ok={res['ok']} moved={res['moved_count']} errors={res['errors_count']}")
        if res["errors"]:
            print("errors:")
            for e in res["errors"][:20]:
                print("- " + e)

    return 0 if res["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
