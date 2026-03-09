from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel

from app import cluster_manager
from app import crewai_agent_factory
from app import crewai_role_registry
from app import crewai_runtime
from app import crewai_self_upgrade as planning
from app import crewai_task_registry
from app import improvement_store
from app import workspace_store
from app.crewai_task_models import (
    DeliveryAuditResult,
    DeliveryDocumentationResult,
    DeliveryImplementationResult,
    DeliveryQAResult,
    DeliveryReviewResult,
)
from app.github_issues_bus import GitHubAuthError, GitHubIssuesBusError, get_issue, update_issue
from app.state_store import ensure_instance_id


class DeliveryError(RuntimeError):
    pass


class DeliveryMergeConflictError(DeliveryError):
    pass


_SAFE_TEST_PREFIXES = (
    "python -m unittest",
    "python3 -m unittest",
    "python -m pytest",
    "python3 -m pytest",
    "pytest",
    "uv run pytest",
    "uv run python -m unittest",
    "npm test",
    "npm run test",
    "pnpm test",
    "pnpm run test",
    "yarn test",
    "go test",
    "cargo test",
)

_DELIVERY_LEASE_SCOPE = "self_upgrade_delivery"


def _utc_now_iso() -> str:
    return planning._utc_now_iso()


def _slug(text: str, *, default: str = "item") -> str:
    return planning._slug(text, default=default)


def _env_truthy(name: str, default: str = "0") -> bool:
    return planning._env_truthy(name, default)


def _compat_file_mirror_enabled() -> bool:
    return _env_truthy("TEAMOS_RUNTIME_FILE_MIRROR", "0")


def _runtime_root() -> Path:
    return planning._runtime_root()


def _worktrees_root() -> Path:
    return planning._worktrees_root()


def _load_yaml(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            doc = raw if isinstance(raw, dict) else {}
            if str(doc.get("id") or "").strip():
                try:
                    improvement_store.upsert_delivery_task(doc)
                except Exception:
                    pass
            return doc
        except Exception:
            pass
    task_id = str(path.stem or "").strip()
    if task_id:
        doc = improvement_store.get_delivery_task(task_id)
        if isinstance(doc, dict):
            return doc
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    if str((payload or {}).get("id") or (payload or {}).get("task_id") or "").strip():
        try:
            improvement_store.upsert_delivery_task(dict(payload or {}))
        except Exception:
            pass
    if not _compat_file_mirror_enabled():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _fallback_ledger_path(*, project_id: str, task_id: str) -> Path:
    return (_runtime_root() / "state" / "improvement_ledger" / (str(project_id or "teamos").strip() or "teamos") / f"{str(task_id or 'task').strip()}.yaml").resolve()


def _task_scope(project_id: str) -> str:
    return "teamos" if str(project_id or "").strip() == "teamos" else f"project:{str(project_id or '').strip()}"


def _task_ledger_dir(project_id: str) -> Path:
    if str(project_id or "").strip() == "teamos":
        return planning.ledger_tasks_dir()
    workspace_store.ensure_project_scaffold(project_id)
    return workspace_store.ledger_tasks_dir(project_id)


def _logs_dir_for_doc(doc: dict[str, Any], *, ledger_path: Path, source_repo_root: Path) -> Path:
    artifacts = doc.get("artifacts") or {}
    raw = str((artifacts if isinstance(artifacts, dict) else {}).get("logs_dir") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p.resolve()
        return (source_repo_root / raw).resolve()
    task_id = str(doc.get("id") or ledger_path.stem).strip()
    return (ledger_path.parent.parent.parent / "logs" / "tasks" / task_id).resolve()


def _is_self_upgrade_task(doc: dict[str, Any]) -> bool:
    orchestration = doc.get("orchestration") or {}
    if not isinstance(orchestration, dict):
        return False
    return (
        str(orchestration.get("engine") or "").strip().lower() == "crewai"
        and str(orchestration.get("flow") or "").strip().lower() == "self_upgrade"
    )


def _current_status(doc: dict[str, Any]) -> str:
    return str(doc.get("status") or doc.get("state") or "").strip().lower()


def _source_repo_root(doc: dict[str, Any]) -> Path:
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    exec_state = doc.get("self_upgrade_execution") or {}
    if not isinstance(exec_state, dict):
        exec_state = {}
    for raw in (
        exec_state.get("source_repo_root"),
        repo.get("source_workdir"),
        repo.get("workdir"),
    ):
        s = str(raw or "").strip()
        if s:
            return Path(s).expanduser().resolve()
    raise DeliveryError(f"task {doc.get('id') or '(unknown)'} missing repo.workdir")


def _worktree_repo_root(doc: dict[str, Any]) -> Optional[Path]:
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        return None
    s = str(repo.get("workdir") or "").strip()
    if not s:
        return None
    p = Path(s).expanduser().resolve()
    return p if p.exists() else None


def _execution_state(doc: dict[str, Any]) -> dict[str, Any]:
    raw = doc.get("self_upgrade_execution")
    return dict(raw) if isinstance(raw, dict) else {}


def _delivery_lease_key(*, project_id: str, task_id: str) -> str:
    return f"{_DELIVERY_LEASE_SCOPE}:{str(project_id or 'teamos').strip() or 'teamos'}:{str(task_id or '').strip()}"


def _delivery_lease_settings() -> dict[str, int]:
    ttl = 600
    renew = 300
    try:
        cfg = cluster_manager.load_cluster_config()
        cluster_cfg = (cfg.get("cluster") or {}) if isinstance(cfg.get("cluster"), dict) else {}
        task_cfg = (cluster_cfg.get("task_lease") or {}) if isinstance(cluster_cfg.get("task_lease"), dict) else {}
        ttl = max(30, int(task_cfg.get("lease_ttl_sec") or ttl))
        renew = max(15, int(task_cfg.get("renew_interval_sec") or renew))
    except Exception:
        pass
    heartbeat = max(15, min(renew, max(15, ttl // 3)))
    return {"ttl_sec": ttl, "renew_interval_sec": renew, "heartbeat_interval_sec": heartbeat}


def _delivery_lease_meta(*, actor: str, ledger_path: Path, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "actor": str(actor or "").strip(),
        "pid": int(os.getpid()),
        "ledger_path": str(ledger_path),
        "status": str(task.get("status") or ""),
        "title": str(task.get("title") or ""),
    }


class _DeliveryLeaseGuard:
    def __init__(
        self,
        *,
        db: Any,
        lease_key: str,
        holder_instance_id: str,
        lease_ttl_sec: int,
        heartbeat_interval_sec: int,
    ) -> None:
        self._db = db
        self._lease_key = str(lease_key)
        self._holder_instance_id = str(holder_instance_id)
        self._lease_ttl_sec = max(30, int(lease_ttl_sec or 30))
        self._heartbeat_interval_sec = max(5, int(heartbeat_interval_sec or 5))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_success_monotonic = time.monotonic()
        self._lost_reason = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=f"delivery-lease-{self._holder_instance_id[:8]}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._heartbeat_interval_sec):
            try:
                renewed = self._db.renew_task_lease(
                    lease_key=self._lease_key,
                    holder_instance_id=self._holder_instance_id,
                    lease_ttl_sec=self._lease_ttl_sec,
                )
                if renewed is None:
                    self._lost_reason = "lease_not_held"
                    return
                self._last_success_monotonic = time.monotonic()
            except Exception as exc:
                if (time.monotonic() - self._last_success_monotonic) >= float(self._lease_ttl_sec):
                    self._lost_reason = f"lease_heartbeat_failed: {str(exc)[:200]}"
                    return

    def assert_held(self, *, task_id: str) -> None:
        if self._lost_reason:
            raise DeliveryError(f"task lease lost for {task_id}: {self._lost_reason}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def _claim_delivery_task_lease(*, db: Any, actor: str, task: dict[str, Any]) -> Optional[dict[str, Any]]:
    task_id = str(task.get("task_id") or "").strip()
    project_id = str(task.get("project_id") or "teamos").strip() or "teamos"
    if not task_id:
        return None
    instance_id = ensure_instance_id()
    settings = _delivery_lease_settings()
    lease_key = _delivery_lease_key(project_id=project_id, task_id=task_id)
    ledger_path = Path(str(task.get("ledger_path") or _fallback_ledger_path(project_id=project_id, task_id=task_id))).expanduser().resolve()
    row = db.claim_task_lease(
        lease_scope=_DELIVERY_LEASE_SCOPE,
        lease_key=lease_key,
        project_id=project_id,
        task_id=task_id,
        holder_instance_id=instance_id,
        holder_actor=str(actor or "").strip(),
        lease_ttl_sec=int(settings["ttl_sec"]),
        holder_meta=_delivery_lease_meta(actor=actor, ledger_path=ledger_path, task=task),
    )
    if row is None:
        return None
    return {
        "lease_key": lease_key,
        "instance_id": instance_id,
        "ttl_sec": int(settings["ttl_sec"]),
        "renew_interval_sec": int(settings["renew_interval_sec"]),
        "heartbeat_interval_sec": int(settings["heartbeat_interval_sec"]),
        "row": row,
    }


def _release_delivery_task_lease(*, db: Any, lease: Optional[dict[str, Any]]) -> None:
    if not lease:
        return
    try:
        db.release_task_lease(
            lease_key=str(lease.get("lease_key") or ""),
            holder_instance_id=str(lease.get("instance_id") or ""),
        )
    except Exception:
        pass


def _task_lane(doc: dict[str, Any]) -> str:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
    lane = str(su.get("lane") or "").strip().lower()
    return lane or "bug"


def _task_work_item(doc: dict[str, Any]) -> dict[str, Any]:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        return {}
    work_item = su.get("work_item") or {}
    return dict(work_item) if isinstance(work_item, dict) else {}


def _task_worktree_title(doc: dict[str, Any], task_id: str) -> str:
    work_item = _task_work_item(doc)
    return str(work_item.get("title") or doc.get("title") or task_id).strip() or task_id


def _normalized_task_worktree_root(doc: dict[str, Any], *, task_id: str, source_repo_root: Path, raw_hint: str) -> Path:
    return Path(
        planning._normalize_worktree_hint(
            repo_root=source_repo_root,
            lane=_task_lane(doc),
            title=_task_worktree_title(doc, task_id),
            raw_hint=str(raw_hint or "").strip(),
        )
    ).resolve()


def _absolute_path(raw: Any) -> Optional[Path]:
    s = str(raw or "").strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if not p.is_absolute():
        return None
    return p.resolve()


def _prune_empty_parents(start: Path, *, stop_at: Path) -> None:
    current = start.resolve()
    limit = stop_at.resolve()
    while current != limit and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _move_worktree_root(*, source_repo_root: Path, legacy_root: Path, target_root: Path) -> Path:
    if legacy_root.resolve() == target_root.resolve():
        return target_root
    target_root.parent.mkdir(parents=True, exist_ok=True)
    if target_root.exists():
        if any(target_root.iterdir()):
            raise DeliveryError(f"worktree target exists and is not empty: {target_root}")
        target_root.rmdir()
    if _is_git_checkout(legacy_root):
        out = _run(
            ["git", "-C", str(source_repo_root), "worktree", "move", str(legacy_root), str(target_root)],
            cwd=source_repo_root,
            timeout_sec=180,
        )
        if int(out.get("returncode", 1)) != 0:
            detail = str(out.get("stderr") or out.get("stdout") or "").strip()[:500]
            raise DeliveryError(f"git worktree move failed: {detail}")
    else:
        shutil.move(str(legacy_root), str(target_root))
    _prune_empty_parents(legacy_root.parent, stop_at=source_repo_root)
    return target_root


def _allowed_paths(doc: dict[str, Any]) -> list[str]:
    execution_policy = doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    raw = execution_policy.get("allowed_paths") or []
    return [str(x).strip().strip("/") for x in raw if str(x).strip()]


def _tests_allowlist(doc: dict[str, Any]) -> list[str]:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
    work_item = su.get("work_item") or {}
    if not isinstance(work_item, dict):
        work_item = {}
    raw = work_item.get("tests") or su.get("tests") or []
    return [str(x).strip() for x in raw if str(x).strip()]


def _acceptance_items(doc: dict[str, Any]) -> list[str]:
    su = doc.get("self_upgrade") or {}
    if not isinstance(su, dict):
        su = {}
    work_item = su.get("work_item") or {}
    if not isinstance(work_item, dict):
        work_item = {}
    raw = work_item.get("acceptance") or su.get("acceptance") or []
    return [str(x).strip() for x in raw if str(x).strip()]


def _candidate_validation_commands(*groups: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            cmd = str(raw or "").strip()
            if not cmd or cmd in seen:
                continue
            seen.add(cmd)
            out.append(cmd)
    return out


def _validation_evidence(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    execution = _execution_state(doc)
    raw = execution.get("validation_evidence") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for key, items in raw.items():
        stage = str(key or "").strip()
        if not stage or not isinstance(items, list):
            continue
        clean: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command") or "").strip()
            if not command:
                continue
            clean.append(
                {
                    "command": command,
                    "ok": bool(item.get("ok")),
                    "returncode": int(item.get("returncode", 0) or 0),
                    "stdout_tail": str(item.get("stdout_tail") or "")[-2000:],
                    "stderr_tail": str(item.get("stderr_tail") or "")[-2000:],
                    "captured_at": str(item.get("captured_at") or ""),
                    "source_stage": str(item.get("source_stage") or stage),
                }
            )
        if clean:
            out[stage] = clean
    return out


def _validation_evidence_payload(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    evidence = _validation_evidence(doc)
    return {stage: items for stage, items in evidence.items() if items}


def _clear_validation_evidence(doc: dict[str, Any]) -> dict[str, Any]:
    execution = _execution_state(doc)
    execution["validation_evidence"] = {}
    execution["last_validation_at"] = ""
    execution["last_validation_stage"] = ""
    doc["self_upgrade_execution"] = execution
    return doc


def _persist_validation_evidence(ledger_path: Path, doc: dict[str, Any], *, stage: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    execution = _execution_state(doc)
    all_evidence = _validation_evidence(doc)
    all_evidence[str(stage or "").strip() or "unknown"] = list(evidence)
    execution["validation_evidence"] = all_evidence
    execution["last_validation_stage"] = str(stage or "").strip()
    execution["last_validation_at"] = _utc_now_iso() if evidence else str(execution.get("last_validation_at") or "")
    doc["self_upgrade_execution"] = execution
    _write_yaml(ledger_path, doc)
    return doc


def _run_validation_evidence(*, repo_root: Path, commands: list[str], allowlist: list[str], source_stage: str) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for command in _candidate_validation_commands(commands):
        item: dict[str, Any] = {
            "command": command,
            "ok": False,
            "returncode": 1,
            "stdout_tail": "",
            "stderr_tail": "",
            "captured_at": _utc_now_iso(),
            "source_stage": str(source_stage or "").strip() or "unknown",
        }
        if not _safe_test_command(command, allowlist=allowlist):
            item["stderr_tail"] = "command_not_allowed"
            evidence.append(item)
            continue
        try:
            parts = shlex.split(command)
        except Exception as e:
            item["stderr_tail"] = f"command_parse_failed: {e}"
            evidence.append(item)
            continue
        out = _run(parts, cwd=repo_root, timeout_sec=600)
        item["returncode"] = int(out.get("returncode", 1))
        item["ok"] = item["returncode"] == 0
        item["stdout_tail"] = str(out.get("stdout") or "")[-2000:]
        item["stderr_tail"] = str(out.get("stderr") or "")[-2000:]
        evidence.append(item)
    return evidence


def _validation_evidence_lines(evidence: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in evidence:
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        lines.append(
            f"validation: command={command} ok={bool(item.get('ok'))} returncode={int(item.get('returncode', 1) or 1)}"
        )
        stdout_tail = str(item.get("stdout_tail") or "").strip()
        stderr_tail = str(item.get("stderr_tail") or "").strip()
        if stdout_tail:
            lines.append(f"stdout_tail: {stdout_tail}")
        if stderr_tail:
            lines.append(f"stderr_tail: {stderr_tail}")
    return lines


def _merge_qa_with_validation_evidence(*, result: DeliveryQAResult, evidence: list[dict[str, Any]]) -> DeliveryQAResult:
    commands = _candidate_validation_commands(result.commands, [str(item.get("command") or "") for item in evidence])
    failures = [str(x).strip() for x in (result.failures or []) if str(x).strip()]
    failed_items = [item for item in evidence if not bool(item.get("ok"))]
    for item in failed_items:
        detail = str(item.get("stderr_tail") or item.get("stdout_tail") or "validation command failed").strip()
        text = f"{str(item.get('command') or '').strip()} failed (exit {int(item.get('returncode', 1) or 1)}): {detail}".strip()
        if text not in failures:
            failures.append(text)
    approved = bool(result.approved) and not failed_items
    summary = str(result.summary or "").strip()
    if failed_items and not summary:
        summary = "QA validation evidence failed."
    return DeliveryQAResult(
        approved=approved,
        summary=summary,
        commands=commands,
        failures=failures,
    )


def _issue_url(doc: dict[str, Any]) -> str:
    links = doc.get("links") or {}
    if not isinstance(links, dict):
        return ""
    return str(links.get("issue") or "").strip()


def _documentation_policy(doc: dict[str, Any]) -> dict[str, Any]:
    raw = doc.get("documentation_policy")
    return dict(raw) if isinstance(raw, dict) else {}


def _merge_allowed_paths(*groups: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            rel = _normalize_repo_relative_path(raw)
            if not rel or rel in seen:
                continue
            seen.add(rel)
            out.append(rel)
    return out


def _documentation_allowed_paths(doc: dict[str, Any]) -> list[str]:
    policy = _documentation_policy(doc)
    return [str(x).strip() for x in (policy.get("allowed_paths") or []) if str(x).strip()]


def _review_allowed_paths(doc: dict[str, Any]) -> list[str]:
    allowed_paths = _allowed_paths(doc)
    if bool(_documentation_policy(doc).get("required")):
        return _merge_allowed_paths(allowed_paths, _documentation_allowed_paths(doc))
    return allowed_paths


def _release_allowed_paths(doc: dict[str, Any]) -> list[str]:
    return _review_allowed_paths(doc)


def _reset_documentation_policy(doc: dict[str, Any], *, pending: bool, feedback: Optional[list[str]] = None) -> dict[str, Any]:
    policy = _documentation_policy(doc)
    if not policy:
        return doc
    if bool(policy.get("required")):
        policy.update(
            {
                "status": "pending" if pending else "done",
                "updated_at": _utc_now_iso(),
                "completed_at": "" if pending else str(policy.get("completed_at") or ""),
                "summary": "" if pending else str(policy.get("summary") or ""),
                "changed_files": [] if pending else [str(x).strip() for x in (policy.get("changed_files") or []) if str(x).strip()],
                "followups": [str(x).strip() for x in (feedback or []) if str(x).strip()] if pending else [str(x).strip() for x in (policy.get("followups") or []) if str(x).strip()],
            }
        )
    else:
        policy.update({"status": "not_required", "updated_at": _utc_now_iso()})
    doc["documentation_policy"] = policy
    return doc


def _issue_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    issue_url = _issue_url(doc)
    issue_number = 0
    match = re.search(r"/issues/(\d+)(?:$|[?#])", issue_url)
    if match:
        try:
            issue_number = int(match.group(1))
        except Exception:
            issue_number = 0
    if issue_number <= 0 or not str(repo.get("locator") or "").strip():
        return {"number": 0, "url": issue_url, "title": "", "body": "", "state": "", "labels": []}
    try:
        issue = get_issue(str(repo.get("locator") or "").strip(), issue_number)
        return {
            "number": int(issue.number),
            "url": str(issue.url or issue_url),
            "title": str(issue.title or ""),
            "body": str(issue.body or ""),
            "state": str(issue.state or ""),
            "labels": list(issue.labels or []),
        }
    except (GitHubAuthError, GitHubIssuesBusError):
        return {"number": issue_number, "url": issue_url, "title": "", "body": "", "state": "", "labels": []}


def _append_markdown(logs_dir: Path, filename: str, heading: str, lines: list[str]) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / filename
    with path.open("a", encoding="utf-8") as f:
        f.write("\n")
        f.write(f"## {heading} ({_utc_now_iso()})\n\n")
        for line in lines:
            text = str(line or "").rstrip()
            if not text:
                f.write("\n")
            elif text.startswith("- ") or text.startswith("1. "):
                f.write(text + "\n")
            else:
                f.write(f"- {text}\n")
        f.write("\n")


def _append_metric(logs_dir: Path, *, event_type: str, actor: str, task_id: str, project_id: str, workstream_id: str, message: str, payload: dict[str, Any], severity: str = "INFO") -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "metrics.jsonl"
    item = {
        "ts": _utc_now_iso(),
        "event_type": event_type,
        "actor": actor,
        "task_id": task_id,
        "project_id": project_id,
        "workstream_id": workstream_id,
        "severity": severity,
        "message": message,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300) -> dict[str, Any]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    return {
        "command": cmd,
        "returncode": int(p.returncode),
        "stdout": str(p.stdout or ""),
        "stderr": str(p.stderr or ""),
    }


def _is_git_checkout(path: Path) -> bool:
    dotgit = path / ".git"
    return dotgit.exists()


def _normalize_repo_relative_path(path: str) -> str:
    rel = str(path or "").strip().replace("\\", "/").lstrip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _resolve_repo_path(repo_root: Path, rel_path: str) -> Path:
    rel = _normalize_repo_relative_path(rel_path)
    if not rel or rel == ".":
        return repo_root.resolve()
    candidate = (repo_root / rel).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except Exception as exc:
        raise DeliveryError(f"path escapes repo root: {rel}") from exc
    return candidate


def _is_allowed_path(rel_path: str, allowed_paths: list[str]) -> bool:
    rel = _normalize_repo_relative_path(rel_path)
    if not rel:
        return False
    if not allowed_paths:
        return False
    for allowed in allowed_paths:
        base = _normalize_repo_relative_path(allowed)
        if not base:
            continue
        if rel == base or rel.startswith(base.rstrip("/") + "/"):
            return True
    return False


def _git_status_text(repo_root: Path, *, max_chars: int = 4000) -> str:
    out = _run(["git", "-C", str(repo_root), "status", "--short"], cwd=repo_root, timeout_sec=60)
    text = (out.get("stdout") or out.get("stderr") or "").strip()
    return str(text)[:max_chars]


def _changed_files(repo_root: Path) -> list[str]:
    out = _run(["git", "-C", str(repo_root), "status", "--porcelain"], cwd=repo_root, timeout_sec=60)
    rows = []
    for raw in str(out.get("stdout") or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        rows.append(_normalize_repo_relative_path(path.split(" -> ")[-1]))
    return [x for x in rows if x]


def _git_diff_text(repo_root: Path, *, allowed_paths: list[str], max_chars: int = 12000) -> str:
    cmd = ["git", "-C", str(repo_root), "diff", "--"]
    cmd.extend(allowed_paths or ["."])
    out = _run(cmd, cwd=repo_root, timeout_sec=90)
    text = str(out.get("stdout") or out.get("stderr") or "")
    return text[:max_chars]


def _safe_test_command(command: str, *, allowlist: list[str]) -> bool:
    cmd = str(command or "").strip()
    if not cmd:
        return False
    if allowlist and cmd in allowlist:
        return True
    return any(cmd.startswith(prefix) for prefix in _SAFE_TEST_PREFIXES)


def _close_issue_if_possible(doc: dict[str, Any]) -> str:
    issue_url = _issue_url(doc)
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    repo_locator = str(repo.get("locator") or "").strip()
    if not issue_url or not repo_locator:
        return ""
    m = re.search(r"/issues/(\d+)(?:$|[?#])", issue_url)
    if not m:
        return ""
    try:
        issue_number = int(m.group(1))
    except Exception:
        return ""
    try:
        issue = update_issue(repo_locator, issue_number, state="closed")
        return str(issue.url or issue_url)
    except (GitHubAuthError, GitHubIssuesBusError):
        return issue_url


def _looks_like_merge_conflict(detail: str) -> bool:
    text = str(detail or "").strip().lower()
    if not text:
        return False
    markers = (
        "non-fast-forward",
        "failed to push some refs",
        "fetch first",
        "[rejected]",
        "unmerged files",
        "fix conflicts",
        "merge conflict",
        "merge conflicts",
        "conflict (content)",
        "could not apply",
        "rebase in progress",
    )
    return any(marker in text for marker in markers)


def _update_task_state(
    ledger_path: Path,
    doc: dict[str, Any],
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    owner_role: str = "",
    extra_execution: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    if status:
        doc["status"] = status
    checkpoint = doc.get("checkpoint") or {}
    if not isinstance(checkpoint, dict):
        checkpoint = {}
    if stage:
        checkpoint["stage"] = stage
    checkpoint["last_event_ts"] = now
    doc["checkpoint"] = checkpoint
    execution = _execution_state(doc)
    if stage:
        execution["stage"] = stage
    execution["last_run_at"] = now
    history = list(execution.get("history") or [])
    if stage:
        history.append({"ts": now, "stage": stage, "status": str(status or doc.get('status') or "")})
    execution["history"] = history[-50:]
    if isinstance(extra_execution, dict):
        execution.update(extra_execution)
    doc["self_upgrade_execution"] = execution
    doc["updated_at"] = now
    if owner_role:
        doc["owners"] = [owner_role]
        doc["owner_role"] = owner_role
        roles = []
        for raw in list(doc.get("roles_involved") or []):
            val = str(raw or "").strip()
            if val and val not in roles:
                roles.append(val)
        if owner_role not in roles:
            roles.append(owner_role)
        doc["roles_involved"] = roles
    _write_yaml(ledger_path, doc)
    try:
        sync_out = planning.sync_task_issue_from_doc(doc)
        if sync_out.get("ok") and str(sync_out.get("url") or "").strip():
            links = doc.get("links") or {}
            if not isinstance(links, dict):
                links = {}
            links["issue"] = str(sync_out.get("url") or "").strip()
            doc["links"] = links
        _write_yaml(ledger_path, doc)
    except Exception:
        pass
    return doc


def _read_repo_path_blob(repo_root: Path, rel: str, *, limit: int = 12000) -> str:
    path = (repo_root / rel).resolve()
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _ensure_task_worktree(ledger_path: Path, doc: dict[str, Any]) -> tuple[dict[str, Any], Path, Path]:
    task_id = str(doc.get("id") or ledger_path.stem).strip()
    source_repo_root = _source_repo_root(doc)
    if not source_repo_root.exists():
        raise DeliveryError(f"source repo does not exist: {source_repo_root}")

    execution = _execution_state(doc)
    repo = doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    execution_policy = doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    desired_raw = str(execution.get("worktree_path") or execution_policy.get("worktree_hint") or "").strip()
    worktree_root = _normalized_task_worktree_root(
        doc,
        task_id=task_id,
        source_repo_root=source_repo_root,
        raw_hint=desired_raw,
    )
    branch_name = str(execution.get("branch_name") or f"codex/self-upgrade/{_slug(task_id.lower(), default='task')}").strip()
    base_branch_out = _run(["git", "-C", str(source_repo_root), "rev-parse", "--abbrev-ref", "HEAD"], cwd=source_repo_root, timeout_sec=30)
    base_branch = str(base_branch_out.get("stdout") or "").strip() or "main"
    legacy_roots: list[Path] = []
    for raw in (
        execution.get("worktree_path"),
        execution_policy.get("worktree_hint"),
        repo.get("workdir"),
    ):
        candidate = _absolute_path(raw)
        if candidate is None:
            continue
        if candidate == source_repo_root or candidate == worktree_root:
            continue
        if candidate not in legacy_roots:
            legacy_roots.append(candidate)

    if _is_git_checkout(worktree_root):
        repo["workdir"] = str(worktree_root)
    else:
        legacy_root = next((candidate for candidate in legacy_roots if candidate.exists()), None)
        if legacy_root is not None:
            worktree_root = _move_worktree_root(source_repo_root=source_repo_root, legacy_root=legacy_root, target_root=worktree_root)
            repo["workdir"] = str(worktree_root)
        else:
            if worktree_root.exists() and any(worktree_root.iterdir()):
                raise DeliveryError(f"worktree target exists and is not empty: {worktree_root}")
            worktree_root.parent.mkdir(parents=True, exist_ok=True)
            out = _run(
                ["git", "-C", str(source_repo_root), "worktree", "add", "-B", branch_name, str(worktree_root), "HEAD"],
                cwd=source_repo_root,
                timeout_sec=120,
            )
            if int(out.get("returncode", 1)) != 0:
                detail = str(out.get("stderr") or out.get("stdout") or "").strip()[:500]
                raise DeliveryError(f"git worktree add failed: {detail}")
            execution["base_branch"] = str(execution.get("base_branch") or base_branch)
            repo["workdir"] = str(worktree_root)

    repo["source_workdir"] = str(source_repo_root)
    repo["branch"] = branch_name
    doc["repo"] = repo
    work_item = _task_work_item(doc)
    if work_item:
        work_item["worktree_hint"] = str(worktree_root)
        su = doc.get("self_upgrade") or {}
        if isinstance(su, dict):
            su["work_item"] = work_item
            doc["self_upgrade"] = su
    execution_policy["worktree_hint"] = str(worktree_root)
    doc["execution_policy"] = execution_policy
    execution.update(
        {
            "worktree_path": str(worktree_root),
            "branch_name": branch_name,
            "source_repo_root": str(source_repo_root),
            "base_branch": str(execution.get("base_branch") or base_branch or "main"),
            "worktree_ready_at": str(execution.get("worktree_ready_at") or _utc_now_iso()),
        }
    )
    doc["self_upgrade_execution"] = execution
    _write_yaml(ledger_path, doc)
    return doc, worktree_root, source_repo_root


def _build_repo_tools(*, repo_root: Path, allowed_paths: list[str], tests_allowlist: list[str]):
    from crewai.tools import tool

    @tool("List Allowed Paths")
    def list_allowed_paths() -> str:
        """Return the repository paths this task is allowed to modify."""
        if not allowed_paths:
            return "No writable paths were provided for this task."
        return "\n".join(f"- {p}" for p in allowed_paths)

    @tool("List Directory")
    def list_directory(relative_path: str = ".") -> str:
        """List files under a repository directory. Use paths relative to the repo root."""
        rel = _normalize_repo_relative_path(relative_path or ".") or "."
        try:
            base = _resolve_repo_path(repo_root, rel)
        except DeliveryError as e:
            return f"directory_blocked: {e}"
        if not base.exists() or not base.is_dir():
            return f"directory_not_found: {rel}"
        rows: list[str] = []
        for child in sorted(base.iterdir()):
            try:
                shown = str(child.relative_to(repo_root))
            except Exception:
                shown = child.name
            rows.append(shown + ("/" if child.is_dir() else ""))
            if len(rows) >= 200:
                break
        return "\n".join(rows) or "(empty)"

    @tool("Search Repository")
    def search_repository(pattern: str) -> str:
        """Search repository text and return matching lines. Use simple text or regex patterns."""
        pat = str(pattern or "").strip()
        if not pat:
            return "pattern is required"
        targets = allowed_paths or ["."]
        cmd = ["rg", "-n", "--hidden", "--glob", "!.git", pat, *targets]
        try:
            out = _run(cmd, cwd=repo_root, timeout_sec=30)
        except Exception:
            out = {"returncode": 1, "stdout": "", "stderr": "rg_failed"}
        text = str(out.get("stdout") or out.get("stderr") or "").strip()
        return text[:12000] or "(no matches)"

    @tool("Read Repository File")
    def read_repository_file(relative_path: str) -> str:
        """Read a UTF-8 repository file and return its contents. Use a path relative to the repo root."""
        rel = _normalize_repo_relative_path(relative_path)
        if not rel:
            return "relative_path is required"
        try:
            path = _resolve_repo_path(repo_root, rel)
        except DeliveryError as e:
            return f"read_blocked: {e}"
        if not path.exists() or not path.is_file():
            return f"file_not_found: {rel}"
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:20000]
        except Exception as e:
            return f"read_failed: {e}"

    @tool("Write Repository File")
    def write_repository_file(relative_path: str, content: str) -> str:
        """Write UTF-8 text to an allowed repository file. Only use allowed paths for this task."""
        rel = _normalize_repo_relative_path(relative_path)
        if not rel:
            return "relative_path is required"
        if not _is_allowed_path(rel, allowed_paths):
            return f"write_blocked_outside_allowed_paths: {rel}"
        try:
            path = _resolve_repo_path(repo_root, rel)
        except DeliveryError as e:
            return f"write_blocked: {e}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content or ""), encoding="utf-8")
        return f"wrote: {rel}"

    @tool("Git Status")
    def git_status() -> str:
        """Return the current git status for the task worktree."""
        return _git_status_text(repo_root)

    @tool("Git Diff")
    def git_diff() -> str:
        """Return the current git diff limited to this task's allowed paths."""
        return _git_diff_text(repo_root, allowed_paths=allowed_paths)

    @tool("Run Validation Command")
    def run_validation_command(command: str) -> str:
        """Run a safe validation command such as pytest or python -m unittest and return JSON output."""
        cmd = str(command or "").strip()
        if not _safe_test_command(cmd, allowlist=tests_allowlist):
            return json.dumps({"ok": False, "error": "command_not_allowed", "command": cmd}, ensure_ascii=False)
        try:
            parts = shlex.split(cmd)
        except Exception as e:
            return json.dumps({"ok": False, "error": f"command_parse_failed: {e}", "command": cmd}, ensure_ascii=False)
        out = _run(parts, cwd=repo_root, timeout_sec=600)
        return json.dumps(
            {
                "ok": int(out.get("returncode", 1)) == 0,
                "command": cmd,
                "returncode": int(out.get("returncode", 1)),
                "stdout": str(out.get("stdout") or "")[-4000:],
                "stderr": str(out.get("stderr") or "")[-4000:],
            },
            ensure_ascii=False,
        )

    read_tools = [list_allowed_paths, list_directory, search_repository, read_repository_file, git_status, git_diff]
    write_tools = list(read_tools) + [write_repository_file, run_validation_command]
    qa_tools = [list_allowed_paths, read_repository_file, git_status, git_diff, run_validation_command]
    return {"read": read_tools, "write": write_tools, "qa": qa_tools}


def _coerce_model_output(raw_output: Any, model_cls: type[BaseModel], *, error_text: str) -> BaseModel:
    obj: Any = None
    if hasattr(raw_output, "to_dict"):
        try:
            obj = raw_output.to_dict()
        except Exception:
            obj = None
    if not obj and hasattr(raw_output, "json_dict"):
        obj = getattr(raw_output, "json_dict", None)
    if obj:
        return model_cls.model_validate(obj)
    text = str(raw_output or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise DeliveryError(error_text)
    return model_cls.model_validate(json.loads(match.group(0)))


def _kickoff_task_output(*, agent: Any, name: str, description: str, expected_output: str, model_cls: type[BaseModel], verbose: bool) -> BaseModel:
    from crewai import Crew, Process, Task

    task = Task(
        name=name,
        description=description,
        expected_output=expected_output,
        agent=agent,
        output_json=model_cls,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=verbose)
    out = crew.kickoff()
    return _coerce_model_output(out, model_cls, error_text=f"CrewAI returned no structured output for task={name}")


def _run_coding_stage(*, task_doc: dict[str, Any], worktree_root: Path, feedback: list[str], verbose: bool) -> DeliveryImplementationResult:
    crewai_runtime.require_crewai_importable()

    task_id = str(task_doc.get("id") or "").strip()
    allowed_paths = _allowed_paths(task_doc)
    tests_allowlist = _tests_allowlist(task_doc)
    acceptance = _acceptance_items(task_doc)
    repo = task_doc.get("repo") or {}
    if not isinstance(repo, dict):
        repo = {}
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=allowed_paths, tests_allowlist=tests_allowlist)
    llm = planning._crewai_llm()
    blob = json.dumps(
        {
            "task_id": task_id,
            "title": task_doc.get("title") or "",
            "summary": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("summary") or "",
            "rationale": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("rationale") or "",
            "issue_url": _issue_url(task_doc),
            "repo_locator": repo.get("locator") or "",
            "allowed_paths": allowed_paths,
            "tests": tests_allowlist,
            "acceptance": acceptance,
            "feedback": feedback,
            "current_status": _git_status_text(worktree_root),
        },
        ensure_ascii=False,
        indent=2,
    )
    lane = planning._task_lane(task_doc) if hasattr(planning, "_task_lane") else str((((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("lane")) or "bug")
    owner_role = str(task_doc.get("owner_role") or planning._coding_owner_role(lane))
    agent = crewai_agent_factory.build_crewai_agent(
        role_id=owner_role,
        template_role_id=planning._coding_owner_role(lane),
        llm=llm,
        verbose=verbose,
        tools_by_profile=tools,
    )
    out = crewai_task_registry.kickoff_registered_task(
        kickoff_fn=_kickoff_task_output,
        agent=agent,
        spec=crewai_task_registry.DELIVERY_CODING_TASK_SPEC,
        payload=blob,
        verbose=verbose,
    )
    return DeliveryImplementationResult.model_validate(out.model_dump())


def _run_review_stage(*, task_doc: dict[str, Any], worktree_root: Path, verbose: bool) -> DeliveryReviewResult:
    crewai_runtime.require_crewai_importable()

    allowed_paths = _review_allowed_paths(task_doc)
    tests_allowlist = _tests_allowlist(task_doc)
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=allowed_paths, tests_allowlist=tests_allowlist)
    llm = planning._crewai_llm()
    documentation_policy = _documentation_policy(task_doc)
    blob = json.dumps(
        {
            "task_id": task_doc.get("id") or "",
            "title": task_doc.get("title") or "",
            "issue_url": _issue_url(task_doc),
            "allowed_paths": allowed_paths,
            "code_paths": _allowed_paths(task_doc),
            "documentation_policy": documentation_policy,
            "acceptance": _acceptance_items(task_doc),
            "git_status": _git_status_text(worktree_root),
            "validation_evidence": _validation_evidence_payload(task_doc),
        },
        ensure_ascii=False,
        indent=2,
    )
    review_role = str((((task_doc.get("execution_policy") or {}) if isinstance(task_doc.get("execution_policy"), dict) else {}).get("review_role")) or planning.ROLE_REVIEW_AGENT)
    agent = crewai_agent_factory.build_crewai_agent(
        role_id=review_role,
        template_role_id=planning.ROLE_REVIEW_AGENT,
        llm=llm,
        verbose=verbose,
        tools_by_profile=tools,
    )
    out = crewai_task_registry.kickoff_registered_task(
        kickoff_fn=_kickoff_task_output,
        agent=agent,
        spec=crewai_task_registry.DELIVERY_REVIEW_TASK_SPEC,
        payload=blob,
        verbose=verbose,
    )
    return _normalize_review_result(task_doc=task_doc, result=DeliveryReviewResult.model_validate(out.model_dump()))


def _run_qa_stage(*, task_doc: dict[str, Any], worktree_root: Path, verbose: bool) -> DeliveryQAResult:
    crewai_runtime.require_crewai_importable()

    allowed_paths = _allowed_paths(task_doc)
    tests_allowlist = _tests_allowlist(task_doc)
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=allowed_paths, tests_allowlist=tests_allowlist)
    llm = planning._crewai_llm()
    blob = json.dumps(
        {
            "task_id": task_doc.get("id") or "",
            "title": task_doc.get("title") or "",
            "tests": tests_allowlist,
            "acceptance": _acceptance_items(task_doc),
            "git_status": _git_status_text(worktree_root),
            "validation_evidence": _validation_evidence_payload(task_doc),
        },
        ensure_ascii=False,
        indent=2,
    )
    qa_role = str((((task_doc.get("execution_policy") or {}) if isinstance(task_doc.get("execution_policy"), dict) else {}).get("qa_role")) or planning.ROLE_QA_AGENT)
    agent = crewai_agent_factory.build_crewai_agent(
        role_id=qa_role,
        template_role_id=planning.ROLE_QA_AGENT,
        llm=llm,
        verbose=verbose,
        tools_by_profile=tools,
    )
    out = crewai_task_registry.kickoff_registered_task(
        kickoff_fn=_kickoff_task_output,
        agent=agent,
        spec=crewai_task_registry.DELIVERY_QA_TASK_SPEC,
        payload=blob,
        verbose=verbose,
    )
    return DeliveryQAResult.model_validate(out.model_dump())


def _normalize_audit_result(*, task_doc: dict[str, Any], result: DeliveryAuditResult) -> DeliveryAuditResult:
    lane = str(_task_lane(task_doc) or "bug").strip().lower() or "bug"
    classification = str(result.classification or lane).strip().lower()
    if classification not in ("bug", "feature", "process", "quality"):
        classification = lane
    closure = str(result.closure or ("ready" if result.approved else "needs_clarification")).strip().lower()
    if closure not in ("ready", "needs_clarification", "split_required", "duplicate", "misclassified", "rejected", "pending"):
        closure = "ready" if result.approved else "needs_clarification"
    approved = bool(result.approved)
    worth_doing = bool(result.worth_doing)
    feedback = [str(x).strip() for x in (result.feedback or []) if str(x).strip()]
    if classification != lane and closure == "ready":
        closure = "misclassified"
        approved = False
        feedback.append(f"当前 issue 更像 {_task_lane(task_doc)} 之外的 `{classification}`，需要先重新分类。")
    if not worth_doing and closure == "ready":
        closure = "rejected"
        approved = False
        feedback.append("审计认为该问题当前不值得进入开发。")
    if closure != "ready":
        approved = False
    if not feedback and not approved:
        feedback.append("Issue 审计未通过，需要补充描述或重新分类。")
    return DeliveryAuditResult(
        approved=approved,
        classification=classification,
        closure=closure,
        worth_doing=worth_doing,
        docs_required=bool(result.docs_required),
        module=str(result.module or "").strip(),
        summary=str(result.summary or "").strip(),
        feedback=feedback,
    )


def _normalize_review_result(*, task_doc: dict[str, Any], result: DeliveryReviewResult) -> DeliveryReviewResult:
    docs_required = bool(_documentation_policy(task_doc).get("required"))
    feedback = [str(x).strip() for x in (result.feedback or []) if str(x).strip()]
    code_feedback = [str(x).strip() for x in (result.code_feedback or []) if str(x).strip()]
    docs_feedback = [str(x).strip() for x in (result.docs_feedback or []) if str(x).strip()]
    code_approved = bool(result.approved) if result.code_approved is None else bool(result.code_approved)
    if docs_required:
        docs_approved = bool(result.approved) if result.docs_approved is None else bool(result.docs_approved)
    else:
        docs_approved = True
        docs_feedback = []
    if not code_approved and not code_feedback:
        code_feedback = list(feedback or ["review rejected the code changes"])
    if docs_required and not docs_approved and not docs_feedback:
        docs_feedback = list(feedback or ["review rejected the documentation changes"])
    merged_feedback = list(feedback)
    for item in code_feedback + docs_feedback:
        if item not in merged_feedback:
            merged_feedback.append(item)
    return DeliveryReviewResult(
        approved=bool(code_approved and docs_approved),
        code_approved=code_approved,
        docs_approved=docs_approved,
        summary=str(result.summary or "").strip(),
        feedback=merged_feedback,
        code_feedback=code_feedback,
        docs_feedback=docs_feedback,
    )


def _run_issue_audit_stage(*, task_doc: dict[str, Any], worktree_root: Path, verbose: bool) -> DeliveryAuditResult:
    crewai_runtime.require_crewai_importable()

    allowed_paths = _allowed_paths(task_doc)
    tests_allowlist = _tests_allowlist(task_doc)
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=allowed_paths, tests_allowlist=tests_allowlist)
    llm = planning._crewai_llm()
    issue_snapshot = _issue_snapshot(task_doc)
    doc_policy = _documentation_policy(task_doc)
    blob = json.dumps(
        {
            "task_id": task_doc.get("id") or "",
            "title": task_doc.get("title") or "",
            "current_lane": _task_lane(task_doc),
            "module": str((((task_doc.get("execution_policy") or {}) if isinstance(task_doc.get("execution_policy"), dict) else {}).get("module")) or ""),
            "summary": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("summary") or "",
            "rationale": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("rationale") or "",
            "allowed_paths": allowed_paths,
            "tests": tests_allowlist,
            "acceptance": _acceptance_items(task_doc),
            "documentation_policy": doc_policy,
            "issue": issue_snapshot,
            "git_status": _git_status_text(worktree_root),
        },
        ensure_ascii=False,
        indent=2,
    )
    agent = crewai_agent_factory.build_crewai_agent(
        role_id=planning.ROLE_ISSUE_AUDIT_AGENT,
        llm=llm,
        verbose=verbose,
        tools_by_profile=tools,
    )
    out = crewai_task_registry.kickoff_registered_task(
        kickoff_fn=_kickoff_task_output,
        agent=agent,
        spec=crewai_task_registry.DELIVERY_AUDIT_TASK_SPEC,
        payload=blob,
        verbose=verbose,
    )
    return _normalize_audit_result(task_doc=task_doc, result=DeliveryAuditResult.model_validate(out.model_dump()))


def _run_documentation_stage(*, task_doc: dict[str, Any], worktree_root: Path, verbose: bool) -> DeliveryDocumentationResult:
    crewai_runtime.require_crewai_importable()

    policy = _documentation_policy(task_doc)
    docs_paths = [str(x).strip() for x in (policy.get("allowed_paths") or []) if str(x).strip()]
    llm = planning._crewai_llm()
    tools = _build_repo_tools(repo_root=worktree_root, allowed_paths=docs_paths, tests_allowlist=[])
    blob = json.dumps(
        {
            "task_id": task_doc.get("id") or "",
            "title": task_doc.get("title") or "",
            "summary": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("summary") or "",
            "rationale": ((task_doc.get("self_upgrade") or {}) if isinstance(task_doc.get("self_upgrade"), dict) else {}).get("rationale") or "",
            "issue_url": _issue_url(task_doc),
            "documentation_policy": policy,
            "git_status": _git_status_text(worktree_root),
            "validation_evidence": _validation_evidence_payload(task_doc),
        },
        ensure_ascii=False,
        indent=2,
    )
    documentation_role = str(policy.get("documentation_role") or planning.ROLE_DOCUMENTATION_AGENT)
    agent = crewai_agent_factory.build_crewai_agent(
        role_id=documentation_role,
        template_role_id=planning.ROLE_DOCUMENTATION_AGENT,
        llm=llm,
        verbose=verbose,
        tools_by_profile=tools,
    )
    out = crewai_task_registry.kickoff_registered_task(
        kickoff_fn=_kickoff_task_output,
        agent=agent,
        spec=crewai_task_registry.DELIVERY_DOCUMENTATION_TASK_SPEC,
        payload=blob,
        verbose=verbose,
    )
    return DeliveryDocumentationResult.model_validate(out.model_dump())


def _release_task(*, task_doc: dict[str, Any], ledger_path: Path, worktree_root: Path) -> dict[str, Any]:
    task_id = str(task_doc.get("id") or ledger_path.stem).strip()
    allowed_paths = _release_allowed_paths(task_doc)
    execution_policy = task_doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    execution = _execution_state(task_doc)
    commit_message = str(execution_policy.get("commit_message_template") or f"{task_id}: {task_doc.get('title') or 'self-upgrade task'}").strip()
    base_branch = str(execution.get("base_branch") or "main").strip() or "main"
    branch_name = str(execution.get("branch_name") or _run(["git", "-C", str(worktree_root), "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_root, timeout_sec=30).get("stdout") or "").strip()
    add_cmd = ["git", "-C", str(worktree_root), "add", "-A", "--"]
    add_cmd.extend(allowed_paths or ["."])
    add_out = _run(add_cmd, cwd=worktree_root, timeout_sec=60)
    if int(add_out.get("returncode", 1)) != 0:
        detail = str(add_out.get("stderr") or add_out.get("stdout") or "").strip()[:500]
        if _looks_like_merge_conflict(detail):
            raise DeliveryMergeConflictError(f"git add failed: {detail}")
        raise DeliveryError(f"git add failed: {detail}")
    staged = _run(["git", "-C", str(worktree_root), "diff", "--cached", "--name-only"], cwd=worktree_root, timeout_sec=30)
    staged_files = [str(x).strip() for x in str(staged.get("stdout") or "").splitlines() if str(x).strip()]
    if not staged_files:
        raise DeliveryError("release blocked: no staged changes for this task")
    commit_out = _run(["git", "-C", str(worktree_root), "commit", "-m", commit_message], cwd=worktree_root, timeout_sec=120)
    if int(commit_out.get("returncode", 1)) != 0:
        detail = str(commit_out.get("stderr") or commit_out.get("stdout") or "").strip()[:500]
        if _looks_like_merge_conflict(detail):
            raise DeliveryMergeConflictError(f"git commit failed: {detail}")
        raise DeliveryError(f"git commit failed: {detail}")
    sha_out = _run(["git", "-C", str(worktree_root), "rev-parse", "HEAD"], cwd=worktree_root, timeout_sec=30)
    commit_sha = str(sha_out.get("stdout") or "").strip()
    origin_out = _run(["git", "-C", str(worktree_root), "remote", "get-url", "origin"], cwd=worktree_root, timeout_sec=30)
    origin = str(origin_out.get("stdout") or "").strip()
    if not origin:
        raise DeliveryError("release blocked: git remote 'origin' is missing")
    push_out = _run(["git", "-C", str(worktree_root), "push", "-u", "origin", branch_name], cwd=worktree_root, timeout_sec=180)
    if int(push_out.get("returncode", 1)) != 0:
        detail = str(push_out.get("stderr") or push_out.get("stdout") or "").strip()[:500]
        if _looks_like_merge_conflict(detail):
            raise DeliveryMergeConflictError(f"git push failed: {detail}")
        raise DeliveryError(f"git push failed: {detail}")
    pr_url = ""
    gh_auth = _run(["gh", "auth", "status", "-h", "github.com"], cwd=worktree_root, timeout_sec=30)
    if int(gh_auth.get("returncode", 1)) == 0 and branch_name and branch_name != base_branch:
        view_out = _run(["gh", "pr", "view", "--json", "url", "--jq", ".url"], cwd=worktree_root, timeout_sec=30)
        pr_url = str(view_out.get("stdout") or "").strip()
        if not pr_url:
            body = "\n".join(
                [
                    f"Task: {task_id}",
                    f"Issue: {_issue_url(task_doc) or '(none)'}",
                    "",
                    "Acceptance:",
                    *[f"- {item}" for item in _acceptance_items(task_doc)],
                ]
            )
            pr_out = _run(["gh", "pr", "create", "--title", commit_message, "--body", body, "--base", base_branch, "--head", branch_name], cwd=worktree_root, timeout_sec=120)
            if int(pr_out.get("returncode", 1)) == 0:
                pr_url = str(pr_out.get("stdout") or "").strip().splitlines()[-1].strip() if str(pr_out.get("stdout") or "").strip() else ""
    closed_issue_url = _close_issue_if_possible(task_doc)
    return {
        "branch": branch_name,
        "base_branch": base_branch,
        "commit_sha": commit_sha,
        "pull_request_url": pr_url,
        "issue_url": closed_issue_url or _issue_url(task_doc),
        "staged_files": staged_files,
    }


def _register_delivery_agents(*, db: Any, task_doc: dict[str, Any]) -> dict[str, str]:
    project_id = str(task_doc.get("project_id") or "teamos").strip() or "teamos"
    workstream_id = str(task_doc.get("workstream_id") or "general").strip() or "general"
    task_id = str(task_doc.get("id") or "").strip()
    execution_policy = task_doc.get("execution_policy") or {}
    if not isinstance(execution_policy, dict):
        execution_policy = {}
    lane = _task_lane(task_doc)
    owner_role = str(task_doc.get("owner_role") or execution_policy.get("owner_role") or planning._coding_owner_role(lane)).strip() or planning._coding_owner_role(lane)
    review_role = str(execution_policy.get("review_role") or planning.ROLE_REVIEW_AGENT).strip() or planning.ROLE_REVIEW_AGENT
    qa_role = str(execution_policy.get("qa_role") or planning.ROLE_QA_AGENT).strip() or planning.ROLE_QA_AGENT
    documentation_role = str(execution_policy.get("documentation_role") or planning.ROLE_DOCUMENTATION_AGENT).strip() or planning.ROLE_DOCUMENTATION_AGENT
    return crewai_role_registry.register_team_blueprint(
        db=db,
        blueprint=crewai_role_registry.delivery_team_blueprint(
            owner_role=owner_role,
            review_role=review_role,
            qa_role=qa_role,
            documentation_role=documentation_role,
        ),
        project_id=project_id,
        workstream_id=workstream_id,
        task_id=task_id,
    )


def _set_agent_state(db: Any, agent_ids: dict[str, str], role_id: str, *, state: str, action: str) -> None:
    agent_id = agent_ids.get(role_id)
    if not agent_id:
        return
    try:
        db.update_assignment(agent_id=agent_id, state=state, current_action=action)
    except Exception:
        pass


def _finish_delivery_agents(db: Any, agent_ids: dict[str, str], *, state: str, action: str) -> None:
    for agent_id in agent_ids.values():
        try:
            db.update_assignment(agent_id=agent_id, state=state, current_action=action)
        except Exception:
            pass


def _emit_event(db: Any, *, event_type: str, actor: str, task_doc: dict[str, Any], payload: dict[str, Any]) -> None:
    try:
        db.add_event(
            event_type=event_type,
            actor=actor,
            project_id=str(task_doc.get("project_id") or "teamos"),
            workstream_id=str(task_doc.get("workstream_id") or "general"),
            payload=payload,
        )
    except Exception:
        pass


def _resume_feedback(doc: dict[str, Any]) -> list[str]:
    execution = _execution_state(doc)
    feedback = [str(x).strip() for x in (execution.get("last_feedback") or []) if str(x).strip()]
    status = _current_status(doc)
    last_error = str(execution.get("last_error") or "").strip()
    if status == "merge_conflict" and last_error:
        note = f"Resolve merge conflict before release: {last_error}"
        if note not in feedback:
            feedback.append(note)
    return feedback


def execute_task_delivery(
    *,
    db: Any,
    actor: str,
    ledger_path: Path,
    doc: dict[str, Any],
    dry_run: bool = False,
    force: bool = False,
    lease: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    task_id = str(doc.get("id") or ledger_path.stem).strip()
    source_repo_root = _source_repo_root(doc)
    logs_dir = _logs_dir_for_doc(doc, ledger_path=ledger_path, source_repo_root=source_repo_root)
    status = _current_status(doc)
    if status in ("closed",) and not force:
        return {"ok": True, "task_id": task_id, "skipped": True, "reason": "task_already_closed"}
    if status in ("blocked", "needs_clarification") and not force:
        return {"ok": True, "task_id": task_id, "skipped": True, "reason": "task_blocked"}

    project_id = str(doc.get("project_id") or "teamos").strip() or "teamos"
    workstream_id = str(doc.get("workstream_id") or "general").strip() or "general"
    lane = _task_lane(doc)
    owner_role = str(doc.get("owner_role") or planning._coding_owner_role(lane)).strip() or planning._coding_owner_role(lane)
    review_role = str((((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("review_role")) or planning.ROLE_REVIEW_AGENT)
    qa_role = str((((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("qa_role")) or planning.ROLE_QA_AGENT)
    documentation_role = str((((doc.get("execution_policy") or {}) if isinstance(doc.get("execution_policy"), dict) else {}).get("documentation_role")) or planning.ROLE_DOCUMENTATION_AGENT)
    verbose = _env_truthy("TEAMOS_SELF_UPGRADE_VERBOSE", "0")
    max_attempts = max(1, int(os.getenv("TEAMOS_SELF_UPGRADE_DELIVERY_MAX_ATTEMPTS", "2") or "2"))
    ship_enabled = _env_truthy("TEAMOS_SELF_UPGRADE_SHIP_ENABLED", "1")
    lease_guard: Optional[_DeliveryLeaseGuard] = None
    agent_ids: dict[str, str] = {}
    if lease:
        lease_guard = _DeliveryLeaseGuard(
            db=db,
            lease_key=str(lease.get("lease_key") or ""),
            holder_instance_id=str(lease.get("instance_id") or ""),
            lease_ttl_sec=int(lease.get("ttl_sec") or 600),
            heartbeat_interval_sec=int(lease.get("heartbeat_interval_sec") or 60),
        )
        lease_guard.start()

    try:
        doc, worktree_root, _ = _ensure_task_worktree(ledger_path, doc)
        agent_ids = _register_delivery_agents(db=db, task_doc=doc)
        prior_status = status
        _append_markdown(logs_dir, "03_work.md", "Delivery Started", [f"task_id: {task_id}", f"worktree: {worktree_root}", f"owner_role: {owner_role}"])
        _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_STARTED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery started", payload={"worktree": str(worktree_root)})
        _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_STARTED", actor=actor, task_doc=doc, payload={"task_id": task_id, "ledger_path": str(ledger_path), "worktree": str(worktree_root)})
        audit_doc = dict(doc.get("self_upgrade_audit") or {}) if isinstance(doc.get("self_upgrade_audit"), dict) else {}
        if force or str(audit_doc.get("status") or "").strip().lower() != "approved":
            doc = _update_task_state(
                ledger_path,
                doc,
                status="doing",
                stage="audit",
                owner_role=owner_role,
                extra_execution={"active_role": planning.ROLE_ISSUE_AUDIT_AGENT},
            )
            _set_agent_state(db, agent_ids, planning.ROLE_ISSUE_AUDIT_AGENT, state="RUNNING", action="auditing issue before scheduling")
            audit_result = _run_issue_audit_stage(task_doc=doc, worktree_root=worktree_root, verbose=verbose)
            if lease_guard:
                lease_guard.assert_held(task_id=task_id)
            doc = _load_yaml(ledger_path)
            audit_doc = dict(doc.get("self_upgrade_audit") or {}) if isinstance(doc.get("self_upgrade_audit"), dict) else {}
            audit_doc.update(
                {
                    "status": "approved" if audit_result.approved else audit_result.closure,
                    "classification": audit_result.classification,
                    "module": str(audit_result.module or audit_doc.get("module") or ""),
                    "worth_doing": bool(audit_result.worth_doing),
                    "closure": audit_result.closure,
                    "docs_required": bool(audit_result.docs_required),
                    "summary": str(audit_result.summary or "").strip(),
                    "feedback": [str(x).strip() for x in (audit_result.feedback or []) if str(x).strip()],
                    "audit_role": planning.ROLE_ISSUE_AUDIT_AGENT,
                    "updated_at": _utc_now_iso(),
                    "approved_at": _utc_now_iso() if audit_result.approved else str(audit_doc.get("approved_at") or ""),
                    "issue_title_snapshot": str((_issue_snapshot(doc).get("title") or "")),
                }
            )
            doc["self_upgrade_audit"] = audit_doc
            doc_policy = _documentation_policy(doc)
            doc_policy.update(
                {
                    "required": bool(audit_result.docs_required or doc_policy.get("required")),
                    "status": str(doc_policy.get("status") or ("pending" if bool(audit_result.docs_required or doc_policy.get("required")) else "not_required")),
                    "documentation_role": str(doc_policy.get("documentation_role") or documentation_role or planning.ROLE_DOCUMENTATION_AGENT),
                    "updated_at": _utc_now_iso(),
                }
            )
            if not bool(doc_policy.get("required")):
                doc_policy["status"] = "not_required"
            doc["documentation_policy"] = doc_policy
            if audit_result.approved:
                doc = _update_task_state(
                    ledger_path,
                    doc,
                    status="doing",
                    stage="audit",
                    owner_role=owner_role,
                    extra_execution={"active_role": "Scheduler-Agent", "last_error": "", "last_feedback": []},
                )
                _set_agent_state(db, agent_ids, planning.ROLE_ISSUE_AUDIT_AGENT, state="DONE", action="issue audit approved")
                _append_markdown(logs_dir, "02_plan.md", "Issue Audit", [audit_result.summary or "Issue 审计通过。", *[f"feedback: {x}" for x in audit_result.feedback]])
                _append_metric(logs_dir, event_type="SELF_UPGRADE_ISSUE_AUDIT_APPROVED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="issue audit approved", payload=audit_result.model_dump())
                _emit_event(db, event_type="SELF_UPGRADE_TASK_ISSUE_AUDIT_APPROVED", actor=actor, task_doc=doc, payload={"task_id": task_id, **audit_result.model_dump()})
            else:
                blocked_status = "needs_clarification" if audit_result.closure in ("needs_clarification", "split_required", "duplicate", "misclassified", "pending") else "blocked"
                doc = _update_task_state(
                    ledger_path,
                    doc,
                    status=blocked_status,
                    stage="needs_clarification" if blocked_status == "needs_clarification" else "audit",
                    owner_role=owner_role,
                    extra_execution={"active_role": planning.ROLE_ISSUE_AUDIT_AGENT, "last_error": str(audit_result.summary or audit_result.closure or "issue audit failed"), "last_feedback": audit_result.feedback},
                )
                _set_agent_state(db, agent_ids, planning.ROLE_ISSUE_AUDIT_AGENT, state="FAILED", action=f"issue audit {audit_result.closure}")
                _finish_delivery_agents(db, agent_ids, state="FAILED", action="delivery waiting for issue clarification")
                _append_markdown(logs_dir, "02_plan.md", "Issue Audit Blocked", [audit_result.summary or audit_result.closure, *[f"feedback: {x}" for x in audit_result.feedback]])
                _append_metric(
                    logs_dir,
                    event_type="SELF_UPGRADE_ISSUE_AUDIT_BLOCKED",
                    actor=actor,
                    task_id=task_id,
                    project_id=project_id,
                    workstream_id=workstream_id,
                    message=f"issue audit blocked delivery: {audit_result.closure}",
                    payload=audit_result.model_dump(),
                    severity="WARN",
                )
                _emit_event(
                    db,
                    event_type="SELF_UPGRADE_TASK_ISSUE_AUDIT_BLOCKED",
                    actor=actor,
                    task_doc=doc,
                    payload={"task_id": task_id, **audit_result.model_dump()},
                )
                return {"ok": False, "task_id": task_id, "status": blocked_status, "feedback": audit_result.feedback, "audit": audit_result.model_dump(), "worktree": str(worktree_root), "project_id": project_id}

        _update_task_state(
            ledger_path,
            doc,
            status="doing",
            stage="coding",
            owner_role=owner_role,
            extra_execution={"last_error": "" if status != "merge_conflict" else str((_execution_state(doc).get("last_error") or "")).strip(), "last_feedback": _resume_feedback(doc)},
        )
        if prior_status == "merge_conflict":
            _append_markdown(logs_dir, "03_work.md", "Merge Conflict Recovery", ["Scheduler-Agent reassigned the task back to coding after a release-time merge conflict."])
            _append_metric(
                logs_dir,
                event_type="SELF_UPGRADE_DELIVERY_MERGE_CONFLICT_RECOVERY_STARTED",
                actor=actor,
                task_id=task_id,
                project_id=project_id,
                workstream_id=workstream_id,
                message="merge conflict recovery resumed in coding stage",
                payload={"task_id": task_id, "worktree": str(worktree_root)},
            )
            _emit_event(
                db,
                event_type="SELF_UPGRADE_TASK_DELIVERY_MERGE_CONFLICT_RECOVERY_STARTED",
                actor=actor,
                task_doc=doc,
                payload={"task_id": task_id, "worktree": str(worktree_root)},
            )

        feedback: list[str] = _resume_feedback(doc)
        last_audit = DeliveryAuditResult(
            approved=True,
            classification=str((audit_doc or {}).get("classification") or lane),
            closure=str((audit_doc or {}).get("closure") or "ready"),
            worth_doing=bool((audit_doc or {}).get("worth_doing", True)),
            docs_required=bool((audit_doc or {}).get("docs_required", _documentation_policy(doc).get("required"))),
            module=str((audit_doc or {}).get("module") or ""),
            summary=str((audit_doc or {}).get("summary") or ""),
            feedback=[str(x).strip() for x in ((audit_doc or {}).get("feedback") or []) if str(x).strip()],
        )
        last_review = DeliveryReviewResult(approved=False, code_approved=False, docs_approved=not bool(_documentation_policy(doc).get("required")))
        last_qa = DeliveryQAResult(approved=False)
        last_docs = DeliveryDocumentationResult(approved=not bool(_documentation_policy(doc).get("required")), updated=False, summary="")
        attempt = 0
        needs_coding = True
        docs_retry_exhausted = False
        while attempt < max_attempts:
            doc = _load_yaml(ledger_path)
            if needs_coding:
                attempt += 1
                doc = _clear_validation_evidence(doc)
                doc = _reset_documentation_policy(doc, pending=True, feedback=feedback)
                doc = _update_task_state(
                    ledger_path,
                    doc,
                    status="doing",
                    stage="coding",
                    owner_role=owner_role,
                    extra_execution={"attempt_count": attempt, "last_feedback": feedback, "last_error": ""},
                )
                _set_agent_state(db, agent_ids, "Scheduler-Agent", state="RUNNING", action=f"dispatching attempt {attempt}")
                _set_agent_state(db, agent_ids, owner_role, state="RUNNING", action=f"implementing attempt {attempt}")
                impl = _run_coding_stage(task_doc=doc, worktree_root=worktree_root, feedback=feedback, verbose=verbose)
                if lease_guard:
                    lease_guard.assert_held(task_id=task_id)
                changed_files = _changed_files(worktree_root)
                if changed_files and not impl.changed_files:
                    impl.changed_files = changed_files[:50]
                coding_evidence = _run_validation_evidence(
                    repo_root=worktree_root,
                    commands=_candidate_validation_commands(impl.tests_to_run, _tests_allowlist(doc)),
                    allowlist=_tests_allowlist(doc),
                    source_stage="coding",
                )
                doc = _load_yaml(ledger_path)
                doc = _persist_validation_evidence(ledger_path, doc, stage="coding", evidence=coding_evidence)
                _append_markdown(logs_dir, "03_work.md", f"Coding Attempt {attempt}", [impl.summary or "(no summary)", *[f"changed_file: {p}" for p in impl.changed_files], *[f"unresolved: {u}" for u in impl.unresolved]])
                _append_metric(logs_dir, event_type="SELF_UPGRADE_CODING_ATTEMPT", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message=f"coding attempt {attempt}", payload=impl.model_dump())
                if coding_evidence:
                    _append_markdown(logs_dir, "04_test.md", f"Coding Validation Evidence {attempt}", _validation_evidence_lines(coding_evidence))
                    _append_metric(
                        logs_dir,
                        event_type="SELF_UPGRADE_CODING_VALIDATION_EVIDENCE",
                        actor=actor,
                        task_id=task_id,
                        project_id=project_id,
                        workstream_id=workstream_id,
                        message=f"coding validation evidence {attempt}",
                        payload={"evidence": coding_evidence},
                    )
                _set_agent_state(db, agent_ids, owner_role, state="DONE", action=f"coding attempt {attempt} finished")
                needs_coding = False

            docs_round = 0
            while True:
                doc = _load_yaml(ledger_path)
                documentation_policy = _documentation_policy(doc)
                docs_required = bool(documentation_policy.get("required"))
                if docs_required:
                    docs_round += 1
                    doc = _reset_documentation_policy(doc, pending=True, feedback=feedback)
                    doc = _update_task_state(
                        ledger_path,
                        doc,
                        status="doing",
                        stage="docs",
                        owner_role=owner_role,
                        extra_execution={"active_role": documentation_role, "last_feedback": feedback, "last_error": ""},
                    )
                    _set_agent_state(db, agent_ids, documentation_role, state="RUNNING", action=f"updating documentation for attempt {attempt}.{docs_round}")
                    last_docs = _run_documentation_stage(task_doc=doc, worktree_root=worktree_root, verbose=verbose)
                    if lease_guard:
                        lease_guard.assert_held(task_id=task_id)
                    docs_paths = [str(x).strip() for x in (documentation_policy.get("allowed_paths") or []) if str(x).strip()]
                    if not last_docs.changed_files:
                        changed_doc_files = [path for path in _changed_files(worktree_root) if _is_allowed_path(path, docs_paths)]
                        if changed_doc_files:
                            last_docs.changed_files = changed_doc_files[:50]
                    doc = _load_yaml(ledger_path)
                    documentation_policy = _documentation_policy(doc)
                    documentation_policy.update(
                        {
                            "status": "done" if last_docs.approved else "blocked",
                            "required": True,
                            "updated_at": _utc_now_iso(),
                            "completed_at": _utc_now_iso() if last_docs.approved else str(documentation_policy.get("completed_at") or ""),
                            "summary": str(last_docs.summary or "").strip(),
                            "changed_files": [str(x).strip() for x in (last_docs.changed_files or []) if str(x).strip()],
                            "followups": [str(x).strip() for x in (last_docs.followups or []) if str(x).strip()],
                        }
                    )
                    doc["documentation_policy"] = documentation_policy
                    doc = _update_task_state(
                        ledger_path,
                        doc,
                        status="doing" if last_docs.approved else "blocked",
                        stage="docs",
                        owner_role=owner_role,
                        extra_execution={"active_role": documentation_role, "last_feedback": list(last_docs.followups or []), "last_error": "" if last_docs.approved else str(last_docs.summary or "documentation update blocked")},
                    )
                    _append_markdown(logs_dir, "05_release.md", f"Documentation Attempt {attempt}.{docs_round}", [last_docs.summary or "(no summary)", *[f"changed_file: {x}" for x in last_docs.changed_files], *[f"followup: {x}" for x in last_docs.followups]])
                    _append_metric(logs_dir, event_type="SELF_UPGRADE_DOCUMENTATION_RESULT", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message=f"documentation attempt {attempt}.{docs_round}", payload=last_docs.model_dump())
                    _set_agent_state(db, agent_ids, documentation_role, state="DONE" if last_docs.approved else "FAILED", action=f"documentation attempt {attempt}.{docs_round} {'approved' if last_docs.approved else 'blocked'}")
                    if not last_docs.approved:
                        feedback = list(last_docs.followups or ([last_docs.summary] if last_docs.summary else ["documentation update blocked"]))
                        docs_retry_exhausted = True
                        break
                else:
                    last_docs = DeliveryDocumentationResult(approved=True, updated=False, summary="documentation not required")
                    if documentation_policy:
                        documentation_policy.update({"status": "not_required", "updated_at": _utc_now_iso()})
                        doc["documentation_policy"] = documentation_policy
                        doc = _update_task_state(ledger_path, doc, status="doing", stage="docs", owner_role=owner_role, extra_execution={"active_role": documentation_role})
                    _set_agent_state(db, agent_ids, documentation_role, state="DONE", action="documentation not required")

                doc = _load_yaml(ledger_path)
                doc = _update_task_state(ledger_path, doc, status="doing", stage="review", owner_role=owner_role, extra_execution={"active_role": review_role})
                _set_agent_state(db, agent_ids, review_role, state="RUNNING", action=f"reviewing attempt {attempt}.{max(1, docs_round)}")
                last_review = _normalize_review_result(
                    task_doc=doc,
                    result=_run_review_stage(task_doc=doc, worktree_root=worktree_root, verbose=verbose),
                )
                if lease_guard:
                    lease_guard.assert_held(task_id=task_id)
                _append_markdown(
                    logs_dir,
                    "04_test.md",
                    f"Review Attempt {attempt}.{max(1, docs_round)}",
                    [
                        last_review.summary or "(no summary)",
                        f"code_approved: {bool(last_review.code_approved)}",
                        f"docs_approved: {bool(last_review.docs_approved)}",
                        *[f"code_feedback: {x}" for x in last_review.code_feedback],
                        *[f"docs_feedback: {x}" for x in last_review.docs_feedback],
                        *[f"feedback: {x}" for x in last_review.feedback],
                    ],
                )
                _append_metric(logs_dir, event_type="SELF_UPGRADE_REVIEW_RESULT", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message=f"review attempt {attempt}.{max(1, docs_round)}", payload=last_review.model_dump())
                _set_agent_state(db, agent_ids, review_role, state="DONE" if last_review.approved else "FAILED", action=f"review attempt {attempt}.{max(1, docs_round)} {'approved' if last_review.approved else 'rejected'}")
                if not bool(last_review.code_approved):
                    feedback = list(last_review.code_feedback or last_review.feedback or ([last_review.summary] if last_review.summary else ["review rejected the code changes"]))
                    doc = _load_yaml(ledger_path)
                    doc = _reset_documentation_policy(doc, pending=True, feedback=feedback)
                    doc = _update_task_state(ledger_path, doc, status="doing", stage="coding", owner_role=owner_role, extra_execution={"last_feedback": feedback, "last_error": "review_code_rejected"})
                    needs_coding = True
                    break
                if docs_required and not bool(last_review.docs_approved):
                    feedback = list(last_review.docs_feedback or last_review.feedback or ([last_review.summary] if last_review.summary else ["review rejected the documentation changes"]))
                    doc = _load_yaml(ledger_path)
                    doc = _reset_documentation_policy(doc, pending=True, feedback=feedback)
                    doc = _update_task_state(ledger_path, doc, status="doing", stage="docs", owner_role=owner_role, extra_execution={"last_feedback": feedback, "last_error": "review_docs_rejected"})
                    if docs_round >= max_attempts:
                        docs_retry_exhausted = True
                        break
                    continue

                doc = _load_yaml(ledger_path)
                doc = _update_task_state(ledger_path, doc, status="test", stage="qa", owner_role=owner_role, extra_execution={"active_role": qa_role})
                _set_agent_state(db, agent_ids, qa_role, state="RUNNING", action=f"running QA attempt {attempt}")
                last_qa = _run_qa_stage(task_doc=doc, worktree_root=worktree_root, verbose=verbose)
                if lease_guard:
                    lease_guard.assert_held(task_id=task_id)
                qa_evidence = _run_validation_evidence(
                    repo_root=worktree_root,
                    commands=_candidate_validation_commands(last_qa.commands, _tests_allowlist(doc)),
                    allowlist=_tests_allowlist(doc),
                    source_stage="qa",
                )
                last_qa = _merge_qa_with_validation_evidence(result=last_qa, evidence=qa_evidence)
                doc = _load_yaml(ledger_path)
                doc = _persist_validation_evidence(ledger_path, doc, stage="qa", evidence=qa_evidence)
                _append_markdown(
                    logs_dir,
                    "04_test.md",
                    f"QA Attempt {attempt}",
                    [
                        last_qa.summary or "(no summary)",
                        *[f"command: {x}" for x in last_qa.commands],
                        *[f"failure: {x}" for x in last_qa.failures],
                        *_validation_evidence_lines(qa_evidence),
                    ],
                )
                _append_metric(logs_dir, event_type="SELF_UPGRADE_QA_RESULT", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message=f"qa attempt {attempt}", payload=last_qa.model_dump())
                _set_agent_state(db, agent_ids, qa_role, state="DONE" if last_qa.approved else "FAILED", action=f"qa attempt {attempt} {'approved' if last_qa.approved else 'rejected'}")
                if not last_qa.approved:
                    feedback = list(last_qa.failures or ([last_qa.summary] if last_qa.summary else ["qa rejected the task"]))
                    doc = _load_yaml(ledger_path)
                    doc = _reset_documentation_policy(doc, pending=True, feedback=feedback)
                    doc = _update_task_state(ledger_path, doc, status="doing", stage="coding", owner_role=owner_role, extra_execution={"last_feedback": feedback, "last_error": "qa_rejected"})
                    needs_coding = True
                    break

                doc = _load_yaml(ledger_path)
                doc = _update_task_state(ledger_path, doc, status="release", stage="release", owner_role=owner_role, extra_execution={"active_role": "Release-Agent"})
                _set_agent_state(db, agent_ids, "Release-Agent", state="RUNNING", action="shipping validated task")
                if lease_guard:
                    lease_guard.assert_held(task_id=task_id)
                if dry_run or not ship_enabled:
                    release_result = {"branch": _execution_state(doc).get("branch_name") or "", "base_branch": _execution_state(doc).get("base_branch") or "main", "commit_sha": "", "pull_request_url": "", "issue_url": _issue_url(doc), "staged_files": _changed_files(worktree_root)}
                else:
                    try:
                        release_result = _release_task(task_doc=doc, ledger_path=ledger_path, worktree_root=worktree_root)
                    except DeliveryMergeConflictError as e:
                        conflict_error = str(e)[:500]
                        feedback = [conflict_error]
                        doc = _load_yaml(ledger_path)
                        doc = _reset_documentation_policy(doc, pending=True, feedback=feedback)
                        execution = _execution_state(doc)
                        merge_conflict_count = int(execution.get("merge_conflict_count") or 0) + 1
                        doc = _update_task_state(
                            ledger_path,
                            doc,
                            status="merge_conflict",
                            stage="merge_conflict",
                            owner_role=owner_role,
                            extra_execution={
                                "active_role": "Scheduler-Agent",
                                "last_feedback": feedback,
                                "last_error": conflict_error,
                                "last_merge_conflict_at": _utc_now_iso(),
                                "merge_conflict_count": merge_conflict_count,
                            },
                        )
                        _set_agent_state(db, agent_ids, "Release-Agent", state="FAILED", action="merge conflict detected during release")
                        _set_agent_state(db, agent_ids, "Scheduler-Agent", state="RUNNING", action="re-dispatching merge conflict back to coding")
                        _append_markdown(logs_dir, "05_release.md", f"Merge Conflict Attempt {attempt}", [conflict_error])
                        _append_metric(
                            logs_dir,
                            event_type="SELF_UPGRADE_DELIVERY_MERGE_CONFLICT",
                            actor=actor,
                            task_id=task_id,
                            project_id=project_id,
                            workstream_id=workstream_id,
                            message=f"merge conflict during release attempt {attempt}",
                            payload={"error": conflict_error, "attempt": attempt, "owner_role": owner_role},
                            severity="WARN",
                        )
                        _emit_event(
                            db,
                            event_type="SELF_UPGRADE_TASK_DELIVERY_MERGE_CONFLICT",
                            actor=actor,
                            task_doc=doc,
                            payload={"task_id": task_id, "error": conflict_error, "attempt": attempt, "owner_role": owner_role},
                        )
                        if attempt >= max_attempts:
                            docs_retry_exhausted = True
                            break
                        doc = _load_yaml(ledger_path)
                        doc = _reset_documentation_policy(doc, pending=True, feedback=feedback)
                        doc = _update_task_state(
                            ledger_path,
                            doc,
                            status="doing",
                            stage="coding",
                            owner_role=owner_role,
                            extra_execution={
                                "active_role": owner_role,
                                "last_feedback": feedback,
                                "last_error": conflict_error,
                            },
                        )
                        needs_coding = True
                        break
                doc = _load_yaml(ledger_path)
                doc = _update_task_state(
                    ledger_path,
                    doc,
                    status="closed",
                    stage="closed",
                    owner_role=owner_role,
                    extra_execution={
                        "active_role": "Release-Agent",
                        "commit_sha": str(release_result.get("commit_sha") or ""),
                        "pull_request_url": str(release_result.get("pull_request_url") or ""),
                        "closed_at": _utc_now_iso(),
                        "issue_url": str(release_result.get("issue_url") or ""),
                        "last_error": "",
                        "last_feedback": [],
                    },
                )
                _set_agent_state(db, agent_ids, "Release-Agent", state="DONE", action="task released")
                _finish_delivery_agents(db, agent_ids, state="DONE", action="delivery finished")
                _append_markdown(logs_dir, "05_release.md", "Release", [f"branch: {release_result.get('branch') or ''}", f"commit_sha: {release_result.get('commit_sha') or ''}", f"pull_request_url: {release_result.get('pull_request_url') or ''}"])
                _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_FINISHED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery finished", payload=release_result)
                _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_FINISHED", actor=actor, task_doc=doc, payload={"task_id": task_id, "release": release_result})
                return {"ok": True, "task_id": task_id, "status": "closed", "attempt_count": attempt, "release": release_result, "worktree": str(worktree_root), "project_id": project_id}

            if docs_retry_exhausted:
                break

        doc = _load_yaml(ledger_path)
        blocked_reason = "; ".join(feedback[:5]) if feedback else "delivery attempts exhausted"
        doc = _update_task_state(ledger_path, doc, status="blocked", stage="blocked", owner_role=owner_role, extra_execution={"last_feedback": feedback, "last_error": blocked_reason})
        _finish_delivery_agents(db, agent_ids, state="FAILED", action="delivery blocked")
        _append_markdown(logs_dir, "07_retro.md", "Delivery Blocked", [blocked_reason, *[f"feedback: {x}" for x in feedback]])
        _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_BLOCKED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery blocked", payload={"feedback": feedback, "audit": last_audit.model_dump(), "review": last_review.model_dump(), "qa": last_qa.model_dump(), "documentation": last_docs.model_dump()}, severity="ERROR")
        _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_BLOCKED", actor=actor, task_doc=doc, payload={"task_id": task_id, "feedback": feedback})
        return {"ok": False, "task_id": task_id, "status": "blocked", "feedback": feedback, "worktree": str(worktree_root), "project_id": project_id}
    except Exception as e:
        doc = _load_yaml(ledger_path)
        doc = _update_task_state(ledger_path, doc, status="blocked", stage="blocked", owner_role=owner_role, extra_execution={"last_error": str(e)[:500]})
        _finish_delivery_agents(db, agent_ids, state="FAILED", action="delivery failed")
        _append_markdown(logs_dir, "07_retro.md", "Delivery Failed", [str(e)[:800]])
        _append_metric(logs_dir, event_type="SELF_UPGRADE_DELIVERY_FAILED", actor=actor, task_id=task_id, project_id=project_id, workstream_id=workstream_id, message="delivery failed", payload={"error": str(e)[:800]}, severity="ERROR")
        _emit_event(db, event_type="SELF_UPGRADE_TASK_DELIVERY_FAILED", actor=actor, task_doc=doc, payload={"task_id": task_id, "error": str(e)[:500]})
        raise
    finally:
        if lease_guard is not None:
            lease_guard.stop()


def list_delivery_tasks(*, project_id: str = "", target_id: str = "", status: str = "") -> list[dict[str, Any]]:
    project_ids: list[str]
    pid = str(project_id or "").strip()
    if pid:
        project_ids = [pid]
    else:
        project_ids = ["teamos"] + [p for p in workspace_store.list_projects() if p != "teamos"]
    status_filter = str(status or "").strip().lower()
    out: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for current_pid in project_ids:
        for doc in improvement_store.list_delivery_tasks(project_id=current_pid, target_id=str(target_id or "").strip(), status=status_filter):
            if not _is_self_upgrade_task(doc):
                continue
            execution = _execution_state(doc)
            task_id = str(doc.get("id") or doc.get("task_id") or "").strip()
            if not task_id:
                continue
            seen_task_ids.add(task_id)
            artifacts = doc.get("artifacts") or {}
            if not isinstance(artifacts, dict):
                artifacts = {}
            ledger_path = str(artifacts.get("ledger_path") or _fallback_ledger_path(project_id=str(doc.get("project_id") or current_pid), task_id=task_id))
            out.append(
                {
                    "task_id": task_id,
                    "title": str(doc.get("title") or ""),
                    "project_id": str(doc.get("project_id") or current_pid),
                    "workstream_id": str(doc.get("workstream_id") or "general"),
                    "status": _current_status(doc),
                    "owner_role": str(doc.get("owner_role") or ""),
                    "stage": str(execution.get("stage") or ""),
                    "attempt_count": int(execution.get("attempt_count") or 0),
                    "worktree_path": str(execution.get("worktree_path") or ""),
                    "pull_request_url": str(execution.get("pull_request_url") or ""),
                    "ledger_path": ledger_path,
                    "issue_url": _issue_url(doc),
                }
            )
    for current_pid in project_ids:
        task_dir = _task_ledger_dir(current_pid)
        if not task_dir.exists():
            continue
        for path in sorted(task_dir.glob("*.yaml")):
            doc = _load_yaml(path)
            if not _is_self_upgrade_task(doc):
                continue
            task_id = str(doc.get("id") or path.stem)
            if task_id in seen_task_ids:
                continue
            st = _current_status(doc)
            if status_filter and st != status_filter:
                continue
            execution = _execution_state(doc)
            out.append(
                {
                    "task_id": task_id,
                    "title": str(doc.get("title") or ""),
                    "project_id": str(doc.get("project_id") or current_pid),
                    "workstream_id": str(doc.get("workstream_id") or "general"),
                    "status": st,
                    "owner_role": str(doc.get("owner_role") or ""),
                    "stage": str(execution.get("stage") or ""),
                    "attempt_count": int(execution.get("attempt_count") or 0),
                    "worktree_path": str(execution.get("worktree_path") or ""),
                    "pull_request_url": str(execution.get("pull_request_url") or ""),
                    "ledger_path": str(path),
                    "issue_url": _issue_url(doc),
                }
            )
    return sorted(out, key=lambda x: (str(x.get("status") or ""), str(x.get("task_id") or "")))


def migrate_legacy_worktrees(*, project_id: str = "", task_id: str = "") -> dict[str, Any]:
    touched: list[dict[str, str]] = []
    moved = 0
    updated = 0
    wanted_task_id = str(task_id or "").strip()
    for task in list_delivery_tasks(project_id=project_id):
        if wanted_task_id and str(task.get("task_id") or "") != wanted_task_id:
            continue
        ledger_path = Path(
            str(task.get("ledger_path") or _fallback_ledger_path(project_id=str(task.get("project_id") or "teamos"), task_id=str(task.get("task_id") or "")))
        ).expanduser().resolve()
        doc = _load_yaml(ledger_path)
        if not _is_self_upgrade_task(doc):
            continue
        source_repo_root = _source_repo_root(doc)
        repo = doc.get("repo") or {}
        if not isinstance(repo, dict):
            repo = {}
        execution = _execution_state(doc)
        execution_policy = doc.get("execution_policy") or {}
        if not isinstance(execution_policy, dict):
            execution_policy = {}
        target_root = _normalized_task_worktree_root(
            doc,
            task_id=str(doc.get("id") or ledger_path.stem),
            source_repo_root=source_repo_root,
            raw_hint=str(execution.get("worktree_path") or execution_policy.get("worktree_hint") or repo.get("workdir") or ""),
        )
        changed = False
        for raw in (
            execution.get("worktree_path"),
            execution_policy.get("worktree_hint"),
            repo.get("workdir"),
        ):
            legacy_root = _absolute_path(raw)
            if legacy_root is None or legacy_root == source_repo_root or legacy_root == target_root:
                continue
            if legacy_root.exists():
                _move_worktree_root(source_repo_root=source_repo_root, legacy_root=legacy_root, target_root=target_root)
                moved += 1
            changed = True
            break
        work_item = _task_work_item(doc)
        if work_item and str(work_item.get("worktree_hint") or "") != str(target_root):
            work_item["worktree_hint"] = str(target_root)
            su = doc.get("self_upgrade") or {}
            if isinstance(su, dict):
                su["work_item"] = work_item
                doc["self_upgrade"] = su
            changed = True
        if str(execution.get("worktree_path") or "") != str(target_root):
            execution["worktree_path"] = str(target_root)
            changed = True
        if str(execution_policy.get("worktree_hint") or "") != str(target_root):
            execution_policy["worktree_hint"] = str(target_root)
            changed = True
        current_repo_workdir = _absolute_path(repo.get("workdir"))
        if current_repo_workdir is not None and current_repo_workdir != source_repo_root and str(current_repo_workdir) != str(target_root):
            repo["workdir"] = str(target_root)
            changed = True
        if str(repo.get("source_workdir") or "") != str(source_repo_root):
            repo["source_workdir"] = str(source_repo_root)
            changed = True
        if changed:
            doc["repo"] = repo
            doc["execution_policy"] = execution_policy
            doc["self_upgrade_execution"] = execution
            _write_yaml(ledger_path, doc)
            updated += 1
            touched.append({"task_id": str(doc.get("id") or ledger_path.stem), "ledger_path": str(ledger_path), "worktree_path": str(target_root)})
    return {"ok": True, "updated": updated, "moved": moved, "tasks": touched}


def delivery_summary(*, project_id: str = "", target_id: str = "") -> dict[str, Any]:
    tasks = list_delivery_tasks(project_id=project_id, target_id=target_id)
    return {
        "total": len(tasks),
        "queued": len([t for t in tasks if str(t.get("status") or "") == "todo"]),
        "coding": len([t for t in tasks if str(t.get("status") or "") == "doing"]),
        "qa": len([t for t in tasks if str(t.get("status") or "") == "test"]),
        "release": len([t for t in tasks if str(t.get("status") or "") == "release"]),
        "needs_clarification": len([t for t in tasks if str(t.get("status") or "") == "needs_clarification"]),
        "merge_conflict": len([t for t in tasks if str(t.get("status") or "") == "merge_conflict"]),
        "blocked": len([t for t in tasks if str(t.get("status") or "") == "blocked"]),
        "closed": len([t for t in tasks if str(t.get("status") or "") == "closed"]),
    }


def run_delivery_sweep(*, db: Any, actor: str, project_id: str = "", target_id: str = "", task_id: str = "", dry_run: bool = False, force: bool = False, max_tasks: Optional[int] = None) -> dict[str, Any]:
    candidates = list_delivery_tasks(project_id=project_id, target_id=target_id)
    wanted_task_id = str(task_id or "").strip()
    out: list[dict[str, Any]] = []
    scanned = 0
    processed = 0
    limit = max(1, int(max_tasks or os.getenv("TEAMOS_SELF_UPGRADE_DELIVERY_MAX_TASKS_PER_SWEEP", "1") or "1"))
    for task in candidates:
        if wanted_task_id and str(task.get("task_id") or "") != wanted_task_id:
            continue
        if (not wanted_task_id) and str(task.get("status") or "") not in ("todo", "doing", "test", "release", "merge_conflict"):
            continue
        scanned += 1
        ledger_path = Path(str(task.get("ledger_path") or "")).expanduser().resolve()
        lease = _claim_delivery_task_lease(db=db, actor=actor, task=task)
        if lease is None:
            current = None
            lease_key = _delivery_lease_key(project_id=str(task.get("project_id") or "teamos"), task_id=str(task.get("task_id") or ""))
            try:
                current = db.get_task_lease(lease_key=lease_key)
            except Exception:
                current = None
            lease_payload = current.__dict__ if current is not None else {}
            out.append(
                {
                    "ok": True,
                    "task_id": str(task.get("task_id") or ""),
                    "project_id": str(task.get("project_id") or ""),
                    "skipped": True,
                    "reason": "lease_held_by_other",
                    "lease": lease_payload,
                }
            )
            if wanted_task_id:
                break
            continue
        try:
            doc = _load_yaml(ledger_path)
            result = execute_task_delivery(db=db, actor=actor, ledger_path=ledger_path, doc=doc, dry_run=dry_run, force=force, lease=lease)
            out.append(result)
            if not result.get("skipped"):
                processed += 1
        finally:
            _release_delivery_task_lease(db=db, lease=lease)
        if not wanted_task_id and processed >= limit:
            break
    overall_ok = all(bool(item.get("ok")) or bool(item.get("skipped")) for item in out)
    return {
        "ok": overall_ok,
        "scanned": scanned,
        "processed": processed,
        "tasks": out,
        "summary": delivery_summary(project_id=project_id, target_id=target_id),
    }
