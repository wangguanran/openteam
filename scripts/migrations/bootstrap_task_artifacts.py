#!/usr/bin/env python3
import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openteam_common import utc_now_iso as _utc_now_iso

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _ts_compact() -> str:
    return _utc_now_iso().replace(":", "").replace("-", "")


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _render_template(tpl: str, *, task_id: str, title: str, date: str) -> str:
    return (
        tpl.replace("{{TASK_ID}}", task_id)
        .replace("{{TITLE}}", title)
        .replace("{{DATE}}", date)
    )


def _ensure_task_logs(repo: Path, *, task_id: str, title: str, date: str, full: bool, dry_run: bool) -> dict[str, Any]:
    logs_dir = repo / ".openteam" / "logs" / "tasks" / task_id
    logs_dir.mkdir(parents=True, exist_ok=True)
    tpls = repo / "templates" / "tasks"
    created: list[str] = []
    want = [
        ("00_intake.md", "task_log_00_intake.md"),
        ("01_plan.md", "task_log_01_plan.md"),
        ("02_todo.md", "task_log_02_todo.md"),
    ]
    if full:
        want += [
            ("03_work.md", "task_log_03_work.md"),
            ("04_test.md", "task_log_04_test.md"),
            ("05_release.md", "task_log_05_release.md"),
            ("06_observe.md", "task_log_06_observe.md"),
            ("07_retro.md", "task_log_07_retro.md"),
        ]
    for out_name, tpl_name in want:
        out = logs_dir / out_name
        if out.exists():
            continue
        tpl_path = tpls / tpl_name
        if not tpl_path.exists():
            continue
        if not dry_run:
            out.write_text(_render_template(tpl_path.read_text(encoding="utf-8"), task_id=task_id, title=title, date=date), encoding="utf-8")
        created.append(str(out))

    # metrics.jsonl
    metrics = logs_dir / "metrics.jsonl"
    if not metrics.exists():
        evt = {
            "ts": _utc_now_iso(),
            "event_type": "TASK_METRICS_BOOTSTRAPPED",
            "actor": "migration",
            "task_id": task_id,
            "project_id": "",
            "workstream_id": "",
            "severity": "INFO",
            "message": "created missing metrics.jsonl",
            "payload": {"logs_dir": str(logs_dir)},
        }
        if not dry_run:
            metrics.write_text(json.dumps(evt, ensure_ascii=False) + "\n", encoding="utf-8")
        created.append(str(metrics))

    return {"logs_dir": str(logs_dir), "created": created}


def _ensure_ledger_fields(ledger: dict[str, Any]) -> bool:
    changed = False
    if "project_id" not in ledger:
        tid = str(ledger.get("id") or "")
        if tid.startswith("DEMO"):
            ledger["project_id"] = "DEMO" if tid.startswith("DEMO-") else "demo"
        elif tid.startswith("OPENTEAM"):
            ledger["project_id"] = "openteam"
        else:
            ledger["project_id"] = "openteam"
        changed = True
    if "workstream_id" not in ledger:
        ledger["workstream_id"] = "general"
        changed = True
    if "repo" not in ledger:
        ledger["repo"] = {"locator": "", "workdir": "", "branch": "", "mode": str(ledger.get("mode") or "auto")}
        changed = True
    if "checkpoint" not in ledger:
        ledger["checkpoint"] = {"stage": str(ledger.get("status") or ledger.get("state") or ""), "last_event_ts": _utc_now_iso()}
        changed = True
    if "recovery" not in ledger:
        ledger["recovery"] = {"last_scan_at": "", "last_resume_at": "", "notes": ""}
        changed = True
    return changed


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Bootstrap missing OpenTeam task artifacts (logs 00~07 + metrics + ledger fields)")
    ap.add_argument("--full", action="store_true", help="also ensure 03~07 logs (recommended)")
    ap.add_argument("--dry-run", action="store_true", help="print planned actions only")
    ap.add_argument("--limit", type=int, default=0, help="limit number of tasks (0=all)")
    args = ap.parse_args(argv)

    repo = _repo_root()
    tasks_dir = repo / ".openteam" / "ledger" / "tasks"
    if not tasks_dir.exists():
        print("no_tasks_dir")
        return 0

    ts = _ts_compact()
    changed_ledgers = 0
    created_files = 0
    tasks = sorted(tasks_dir.glob("*.yaml"))
    if args.limit and args.limit > 0:
        tasks = tasks[: int(args.limit)]

    for p in tasks:
        data = _read_yaml(p)
        tid = str(data.get("id") or p.stem)
        title = str(data.get("title") or "")
        created = _ensure_task_logs(
            repo,
            task_id=tid,
            title=title,
            date=_utc_now_iso().split("T", 1)[0],
            full=bool(args.full),
            dry_run=bool(args.dry_run),
        )["created"]
        if created:
            created_files += len(created)
            if args.dry_run:
                print(f"create_files task={tid} n={len(created)}")

        if _ensure_ledger_fields(data):
            changed_ledgers += 1
            if args.dry_run:
                print(f"update_ledger task={tid} path={p}")
            else:
                bak = p.with_suffix(p.suffix + f".bak.{ts}")
                if not bak.exists():
                    shutil.copy2(p, bak)
                _write_yaml(p, data)

    print(json.dumps({"changed_ledgers": changed_ledgers, "created_files": created_files, "dry_run": bool(args.dry_run)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
