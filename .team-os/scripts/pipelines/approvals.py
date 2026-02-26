#!/usr/bin/env python3
"""
Approvals engine (DB-backed; deterministic classification).

High-level:
- risk_classify(action) -> LOW|HIGH (+ category + reasons)
- request(action) -> write approval request to Postgres (or local audit fallback)
- decide(approval_id) -> update status APPROVED/DENIED (leader policy or manual)

This pipeline is used by the CLI to gate high-risk actions (repo create, force migration, remote bootstrap, etc.).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from _common import PipelineError, add_default_args, append_jsonl, read_yaml, resolve_repo_root, resolve_workspace_root, utc_now_iso, write_json
from _db import connect, get_db_url, to_jsonable
from db_migrate import apply_migrations as _apply_migrations


def _policy_path(repo_root: Path) -> Path:
    return repo_root / ".team-os" / "policies" / "approvals.yaml"


def _load_policy(repo_root: Path) -> dict[str, Any]:
    p = _policy_path(repo_root)
    if not p.exists():
        return {}
    obj = read_yaml(p)
    return obj if isinstance(obj, dict) else {}


def _canon_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def risk_classify(*, action_kind: str, action_summary: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic risk classifier.
    Unknown actions default to HIGH (fail-safe).
    """
    kind = str(action_kind or "").strip()
    summary = str(action_summary or "").strip()
    if not kind:
        return {"risk_level": "HIGH", "category": "UNKNOWN", "reasons": ["missing_action_kind"], "kind": kind}

    reasons: list[str] = []

    # Explicit kind mapping (preferred; callers should use stable kinds).
    LOW: dict[str, str] = {
        "db_migrate": "DB_MIGRATE",
        "db_status": "DB_STATUS",
        "doctor": "DOCTOR",
        "policy_check": "POLICY_CHECK",
    }
    if kind in LOW:
        reasons.append(f"kind:{kind}")
        return {"risk_level": "LOW", "category": LOW[kind], "reasons": sorted(reasons), "kind": kind, "summary": summary}

    HIGH: dict[str, str] = {
        "repo_create": "GITHUB_REPO_CREATE",
        "git_branch_delete": "GIT_BRANCH_DELETE",
        "workspace_migrate_force": "DATA_MOVE_OVERWRITE",
        "node_add_execute": "REMOTE_SSH_EXEC",
        "hub_expose_remote_access": "PUBLIC_PORT",
        "hub_restore": "DATA_RESTORE",
        "hub_push_config_with_secrets": "SECRET_DISTRIBUTION",
        "docker_install_system": "REMOTE_ROOT_INSTALL",
        "systemd_service_write": "SYSTEM_CONFIG",
        "firewall_change": "SYSTEM_CONFIG",
        "redis_remote_expose": "PUBLIC_PORT",
        "git_push_force": "FORCE_PUSH",
        "rm_rf": "DATA_DELETE",
        "open_public_port": "PUBLIC_PORT",
        "prod_deploy": "PROD_DEPLOY",
        "system_config_change": "SYSTEM_CONFIG",
        "github_org_settings_change": "GITHUB_ORG_SETTINGS",
    }
    if kind in HIGH:
        reasons.append(f"kind:{kind}")
        return {"risk_level": "HIGH", "category": HIGH[kind], "reasons": sorted(reasons), "kind": kind, "summary": summary}

    # Heuristic fallback: if payload declares risk flags.
    if bool(payload.get("force")) or bool(payload.get("execute")) or bool(payload.get("public_port")):
        reasons.append("payload:risky_flag")
        return {"risk_level": "HIGH", "category": "UNKNOWN", "reasons": sorted(reasons), "kind": kind, "summary": summary}

    # Unknown actions default HIGH (fail-safe).
    reasons.append("unknown_kind")
    return {"risk_level": "HIGH", "category": "UNKNOWN", "reasons": sorted(reasons), "kind": kind, "summary": summary}


def _role_auto() -> str:
    v = str(os.getenv("TEAMOS_CLUSTER_ROLE") or "").strip().lower()
    if v in ("leader", "assistant"):
        return v
    return "single"


def _interactive_confirm(*, summary: str) -> bool:
    sys.stderr.write("\n".join(["HIGH RISK ACTION", f"- {summary}", ""]) + "\n")
    ans = input("Approve? type YES to continue: ").strip()
    return ans == "YES"


def _audit_fallback_path(ws_root: Path) -> Path:
    # Local audit (not in git); can be synced to DB later.
    return ws_root / "shared" / "audit" / "approvals.jsonl"


def _write_fallback_event(ws_root: Path, event: dict[str, Any], *, dry_run: bool) -> None:
    p = _audit_fallback_path(ws_root)
    append_jsonl(p, event, dry_run=dry_run)


def _db_available(dsn: str) -> bool:
    return bool(str(dsn or "").strip())


def _ensure_db_schema(conn, *, repo_root: Path) -> None:
    mig_dir = repo_root / ".team-os" / "db" / "migrations"
    migrations: list[tuple[str, Path]] = []
    for p in sorted(mig_dir.glob("*.sql")):
        name = p.name
        if len(name) >= 4 and name[:4].isdigit():
            migrations.append((name[:4], p))
    if migrations:
        _apply_migrations(conn, migrations)


def _db_insert_approval(conn, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approvals (
              approval_id, task_id, action_kind, action_summary,
              risk_level, risk_reasons, category, status,
              requested_by, requested_at,
              decided_by, decided_at, decision_engine, decision_note,
              action_payload
            ) VALUES (
              %s,%s,%s,%s,
              %s,%s::jsonb,%s,%s,
              %s,%s,
              %s,%s,%s,%s,
              %s::jsonb
            )
            """,
            (
                row["approval_id"],
                row.get("task_id", ""),
                row["action_kind"],
                row["action_summary"],
                row["risk_level"],
                json.dumps(row.get("risk_reasons") or [], ensure_ascii=False),
                row.get("category", ""),
                row["status"],
                row.get("requested_by", ""),
                row.get("requested_at", utc_now_iso()),
                row.get("decided_by", ""),
                row.get("decided_at"),
                row.get("decision_engine", ""),
                row.get("decision_note", ""),
                json.dumps(row.get("action_payload") or {}, ensure_ascii=False),
            ),
        )
    conn.commit()


def _db_update_decision(conn, *, approval_id: str, status: str, decided_by: str, decided_at: str, engine: str, note: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE approvals
            SET status=%s, decided_by=%s, decided_at=%s, decision_engine=%s, decision_note=%s
            WHERE approval_id=%s
            """,
            (status, decided_by, decided_at, engine, note[:800], approval_id),
        )
    conn.commit()


def _db_get_approval(conn, *, approval_id: str) -> Optional[dict[str, Any]]:
    with conn.cursor() as cur:
        row = cur.execute("SELECT * FROM approvals WHERE approval_id=%s", (approval_id,)).fetchone()
        return dict(row) if row else None


def _db_list_approvals(conn, *, limit: int = 50) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        rows = cur.execute("SELECT * FROM approvals ORDER BY requested_at DESC LIMIT %s", (int(limit),)).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows or []:
            d = dict(r)
            # JSONify datetime values
            for k, v in list(d.items()):
                d[k] = to_jsonable(v)
            out.append(d)
    return out


def _db_insert_execution(
    conn,
    *,
    approval_id: str,
    execution_status: str,
    executor: str,
    note: str,
    detail: dict[str, Any],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approval_executions (
              execution_id, approval_id, execution_status, executor, note, detail
            ) VALUES (
              %s,%s,%s,%s,%s,%s::jsonb
            )
            """,
            (
                str(uuid.uuid4()),
                str(approval_id),
                str(execution_status),
                str(executor or ""),
                str(note or "")[:800],
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
    conn.commit()


def _policy_decide(policy: dict[str, Any], *, category: str) -> Optional[str]:
    deny = set([str(x) for x in (policy.get("always_deny_categories") or []) if str(x).strip()])
    if category in deny:
        return "DENIED"
    auto = set([str(x) for x in (policy.get("auto_approve_high_risk_categories") or []) if str(x).strip()])
    if category in auto:
        return "APPROVED"
    return None


def _emit_json(obj: dict[str, Any], *, json_mode: bool) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    print(text)


def cmd_classify(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {}
    if str(getattr(args, "payload_json", "") or "").strip():
        try:
            payload = json.loads(str(args.payload_json))
        except Exception as e:
            raise PipelineError(f"invalid --payload-json: {e}") from e
        if not isinstance(payload, dict):
            raise PipelineError("--payload-json must be a JSON object")
    out = risk_classify(action_kind=str(args.action_kind or ""), action_summary=str(args.summary or ""), payload=payload)
    _emit_json({"ok": True, "classification": out}, json_mode=bool(args.json))
    return 0


def cmd_request(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    policy = _load_policy(repo)

    action_kind = str(args.action_kind or "").strip()
    summary = str(args.summary or "").strip()
    if not action_kind:
        raise PipelineError("--action-kind is required")
    if not summary:
        raise PipelineError("--summary is required")

    payload: dict[str, Any] = {}
    if str(getattr(args, "payload_json", "") or "").strip():
        try:
            payload = json.loads(str(args.payload_json))
        except Exception as e:
            raise PipelineError(f"invalid --payload-json: {e}") from e
        if not isinstance(payload, dict):
            raise PipelineError("--payload-json must be a JSON object")

    cls = risk_classify(action_kind=action_kind, action_summary=summary, payload=payload)
    risk_level = str(cls.get("risk_level") or "HIGH")
    category = str(cls.get("category") or "")
    reasons = list(cls.get("reasons") or [])

    role = str(getattr(args, "role", "") or "").strip().lower() or "auto"
    if role == "auto":
        role = _role_auto()

    approval_id = str(uuid.uuid4())
    now = utc_now_iso()
    requested_by = str(args.requested_by or "").strip() or getpass.getuser()
    task_id = str(getattr(args, "task_id", "") or "").strip()

    record = {
        "approval_id": approval_id,
        "task_id": task_id,
        "action_kind": action_kind,
        "action_summary": summary,
        "risk_level": risk_level,
        "risk_reasons": reasons,
        "category": category,
        "status": "REQUESTED" if risk_level == "HIGH" else "APPROVED",
        "requested_by": requested_by,
        "requested_at": now,
        "decided_by": "",
        "decided_at": None,
        "decision_engine": "",
        "decision_note": "",
        "action_payload": payload,
    }

    if bool(getattr(args, "dry_run", False)):
        _emit_json({"ok": True, "dry_run": True, "record": record, "classification": cls}, json_mode=bool(args.json))
        return 0

    # Decide (when allowed) before persisting decision fields.
    decided_by = ""
    decided_at: Optional[str] = None
    decision_engine = ""
    decision_note = ""

    if risk_level != "HIGH":
        decision_engine = "risk_classifier"
        decision_note = "LOW risk (no approval required)"
    else:
        # Always-deny has priority (deterministic).
        pol_dec = _policy_decide(policy, category=category)
        if pol_dec == "DENIED":
            record["status"] = "DENIED"
            decided_by = "policy"
            decided_at = now
            decision_engine = "policy.always_deny"
            decision_note = f"category denied: {category}"
        elif role == "leader" and pol_dec == "APPROVED":
            record["status"] = "APPROVED"
            decided_by = "policy"
            decided_at = now
            decision_engine = "policy.auto_approve"
            decision_note = f"category auto-approved: {category}"
        elif role == "single":
            require_manual = bool(policy.get("require_manual_when_single", True))
            if require_manual:
                if bool(getattr(args, "yes", False)):
                    record["status"] = "APPROVED"
                    decided_by = requested_by
                    decided_at = now
                    decision_engine = "manual.flag_yes"
                    decision_note = "approved via --yes"
                elif bool(getattr(args, "interactive", False)):
                    ok = _interactive_confirm(summary=summary)
                    record["status"] = "APPROVED" if ok else "DENIED"
                    decided_by = requested_by
                    decided_at = utc_now_iso()
                    decision_engine = "manual.prompt"
                    decision_note = "approved" if ok else "denied"
                else:
                    record["status"] = "REQUESTED"
                    decision_engine = "manual.required"
                    decision_note = "manual confirmation required (re-run with --interactive or --yes)"

    record["decided_by"] = decided_by
    record["decided_at"] = decided_at
    record["decision_engine"] = decision_engine
    record["decision_note"] = decision_note

    dsn = get_db_url(override=str(getattr(args, "db_url", "") or ""))
    db_ok = _db_available(dsn)

    # Persist to DB when possible; otherwise fallback to local jsonl audit.
    if db_ok:
        conn = connect(dsn)
        try:
            _ensure_db_schema(conn, repo_root=repo)
            _db_insert_approval(conn, record)
            # If a decision happened immediately, update it in-place.
            if record["status"] in ("APPROVED", "DENIED") and record.get("decided_at"):
                _db_update_decision(
                    conn,
                    approval_id=approval_id,
                    status=record["status"],
                    decided_by=record.get("decided_by") or "",
                    decided_at=str(record.get("decided_at") or now),
                    engine=record.get("decision_engine") or "",
                    note=record.get("decision_note") or "",
                )
        finally:
            conn.close()
    else:
        event = {"ts": now, "event": "APPROVAL_REQUEST", "pending_sync": True, "record": record}
        _write_fallback_event(ws_root, event, dry_run=bool(args.dry_run))
        # Also keep a last-known snapshot (helpful for debugging).
        snap = ws_root / "shared" / "audit" / "approvals_last.json"
        write_json(snap, event, dry_run=bool(args.dry_run))

    out = {"ok": True, "approval_id": approval_id, "status": record["status"], "record": record, "classification": cls, "role": role, "db": {"enabled": db_ok}}
    _emit_json(out, json_mode=bool(args.json))
    return 0 if record["status"] == "APPROVED" or risk_level != "HIGH" else 2


def cmd_decide(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    policy = _load_policy(repo)

    approval_id = str(args.approval_id or "").strip()
    decision = str(args.decision or "").strip().upper()
    if decision not in ("APPROVE", "DENY"):
        raise PipelineError("--decision must be APPROVE or DENY")
    status = "APPROVED" if decision == "APPROVE" else "DENIED"
    decided_by = str(args.decided_by or "").strip() or getpass.getuser()
    decided_at = utc_now_iso()
    engine = str(args.engine or "").strip() or "manual"
    note = str(args.note or "").strip()

    dsn = get_db_url(override=str(getattr(args, "db_url", "") or ""))
    if _db_available(dsn):
        conn = connect(dsn)
        try:
            _ensure_db_schema(conn, repo_root=repo)
            row = _db_get_approval(conn, approval_id=approval_id)
            if not row:
                raise PipelineError(f"approval not found: {approval_id}")
            # Honor always-deny policy (cannot override).
            cat = str(row.get("category") or "")
            if _policy_decide(policy, category=cat) == "DENIED":
                raise PipelineError(f"policy forbids approving category={cat}")
            _db_update_decision(conn, approval_id=approval_id, status=status, decided_by=decided_by, decided_at=decided_at, engine=engine, note=note)
        finally:
            conn.close()
        _emit_json({"ok": True, "approval_id": approval_id, "status": status}, json_mode=bool(args.json))
        return 0

    # Fallback: append decision event.
    event = {"ts": decided_at, "event": "APPROVAL_DECISION", "pending_sync": True, "approval_id": approval_id, "status": status, "decided_by": decided_by, "engine": engine, "note": note}
    _write_fallback_event(ws_root, event, dry_run=bool(args.dry_run))
    _emit_json({"ok": True, "approval_id": approval_id, "status": status, "db": {"enabled": False}}, json_mode=bool(args.json))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    limit = int(getattr(args, "limit", 50) or 50)
    dsn = get_db_url(override=str(getattr(args, "db_url", "") or ""))
    if _db_available(dsn):
        conn = connect(dsn)
        try:
            _ensure_db_schema(conn, repo_root=repo)
            rows = _db_list_approvals(conn, limit=limit)
        finally:
            conn.close()
        _emit_json({"ok": True, "db": {"enabled": True}, "approvals": rows}, json_mode=bool(args.json))
        return 0

    # Fallback: show local jsonl path only (do not parse potentially large file in doctor contexts).
    p = _audit_fallback_path(ws_root)
    _emit_json({"ok": True, "db": {"enabled": False}, "fallback_audit_path": str(p)}, json_mode=bool(args.json))
    return 0


def cmd_record_execution(args: argparse.Namespace) -> int:
    repo = resolve_repo_root(args)
    ws_root = resolve_workspace_root(args)
    approval_id = str(args.approval_id or "").strip()
    if not approval_id:
        raise PipelineError("approval_id is required")
    status = str(args.execution_status or "").strip().upper()
    if status not in ("STARTED", "SUCCEEDED", "FAILED", "SKIPPED"):
        raise PipelineError("--execution-status must be STARTED|SUCCEEDED|FAILED|SKIPPED")
    executor = str(args.executor or "").strip() or getpass.getuser()
    note = str(args.note or "").strip()
    detail: dict[str, Any] = {}
    if str(getattr(args, "detail_json", "") or "").strip():
        try:
            detail = json.loads(str(args.detail_json))
        except Exception as e:
            raise PipelineError(f"invalid --detail-json: {e}") from e
        if not isinstance(detail, dict):
            raise PipelineError("--detail-json must be a JSON object")

    dsn = get_db_url(override=str(getattr(args, "db_url", "") or ""))
    if _db_available(dsn):
        conn = connect(dsn)
        try:
            _ensure_db_schema(conn, repo_root=repo)
            _db_insert_execution(conn, approval_id=approval_id, execution_status=status, executor=executor, note=note, detail=detail)
        finally:
            conn.close()
        _emit_json({"ok": True, "approval_id": approval_id, "execution_status": status, "db": {"enabled": True}}, json_mode=bool(args.json))
        return 0

    event = {
        "ts": utc_now_iso(),
        "event": "APPROVAL_EXECUTION",
        "pending_sync": True,
        "approval_id": approval_id,
        "execution_status": status,
        "executor": executor,
        "note": note,
        "detail": detail,
    }
    _write_fallback_event(ws_root, event, dry_run=bool(args.dry_run))
    _emit_json({"ok": True, "approval_id": approval_id, "execution_status": status, "db": {"enabled": False}}, json_mode=bool(args.json))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Approvals engine (DB-backed; deterministic)")
    add_default_args(ap)
    ap.add_argument("--db-url", default="", help="override TEAMOS_DB_URL")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    sp = ap.add_subparsers(dest="cmd", required=True)

    c = sp.add_parser("classify", help="Classify an action as LOW/HIGH risk")
    c.add_argument("--action-kind", required=True)
    c.add_argument("--summary", required=True)
    c.add_argument("--payload-json", default="")
    c.set_defaults(fn=cmd_classify)

    r = sp.add_parser("request", help="Request approval for an action (writes to DB when possible)")
    r.add_argument("--task-id", default="")
    r.add_argument("--requested-by", default="")
    r.add_argument("--action-kind", required=True)
    r.add_argument("--summary", required=True)
    r.add_argument("--payload-json", default="")
    r.add_argument("--role", default="auto", help="auto|leader|assistant|single")
    r.add_argument("--interactive", action="store_true", help="prompt YES/NO when single-machine manual approval is required")
    r.add_argument("--yes", action="store_true", help="skip prompt and approve (still recorded)")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_request)

    d = sp.add_parser("decide", help="Record an approval decision (APPROVE/DENY)")
    d.add_argument("approval_id")
    d.add_argument("--decision", required=True, help="APPROVE|DENY")
    d.add_argument("--decided-by", default="")
    d.add_argument("--engine", default="manual")
    d.add_argument("--note", default="")
    d.add_argument("--dry-run", action="store_true")
    d.set_defaults(fn=cmd_decide)

    l = sp.add_parser("list", help="List recent approvals")
    l.add_argument("--limit", type=int, default=50)
    l.set_defaults(fn=cmd_list)

    rx = sp.add_parser("record-execution", help="Record execution result for an approved action")
    rx.add_argument("approval_id")
    rx.add_argument("--execution-status", required=True, help="STARTED|SUCCEEDED|FAILED|SKIPPED")
    rx.add_argument("--executor", default="")
    rx.add_argument("--note", default="")
    rx.add_argument("--detail-json", default="")
    rx.add_argument("--dry-run", action="store_true")
    rx.set_defaults(fn=cmd_record_execution)

    args = ap.parse_args(argv)
    fn = getattr(args, "fn", None)
    if not fn:
        raise PipelineError("missing subcommand")
    return int(fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
