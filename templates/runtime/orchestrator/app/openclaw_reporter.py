from copy import deepcopy
import fnmatch
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

from .state_store import runtime_state_root, team_os_root


_DEFAULT_EVENT_TYPES = [
    "SELF_UPGRADE_*",
    "RUN_FAILED",
    "RUN_FINISHED",
    "TASK_NEW",
    "REQUIREMENT_SUBMITTED",
    "REQUIREMENT_ADD_FAILED",
]
_IGNORED_EVENT_TYPES = ("OPENCLAW_",)


class OpenClawReporterError(Exception):
    pass


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _config_path() -> Path:
    return runtime_state_root() / "openclaw_reporter.yaml"


def _state_path() -> Path:
    return runtime_state_root() / "openclaw_reporter_state.json"


def _gateway_state_dir_default() -> str:
    return str((runtime_state_root() / "openclaw-client").resolve())


def _env_truthy(name: str, default: str = "0") -> bool:
    raw = str(os.getenv(name, default) or default).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _env_optional_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_text(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return str(default or "").strip()
    text = str(raw).strip()
    return text if text else str(default or "").strip()


def _split_csv(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _default_config() -> dict[str, Any]:
    inferred = _infer_gateway_defaults()
    target = str(os.getenv("TEAMOS_OPENCLAW_TARGET", "") or "").strip()
    enabled = _env_truthy("TEAMOS_OPENCLAW_ENABLED", "1" if target else "0")
    paths = _split_csv(os.getenv("TEAMOS_OPENCLAW_PATH_PATTERNS", "*")) or ["*"]
    event_types = _split_csv(os.getenv("TEAMOS_OPENCLAW_EVENT_TYPES", "")) or list(_DEFAULT_EVENT_TYPES)
    excluded = _split_csv(os.getenv("TEAMOS_OPENCLAW_EXCLUDE_EVENT_TYPES", ""))
    return {
        "enabled": enabled,
        "channel": _env_text("TEAMOS_OPENCLAW_CHANNEL", "telegram") or "telegram",
        "target": target,
        "path_patterns": paths,
        "event_types": event_types,
        "exclude_event_types": excluded,
        "bin": _env_text("TEAMOS_OPENCLAW_BIN", "openclaw") or "openclaw",
        "message_prefix": _env_text("TEAMOS_OPENCLAW_MESSAGE_PREFIX", "[TeamOS 上报]") or "[TeamOS 上报]",
        "gateway_mode": _env_text("TEAMOS_OPENCLAW_GATEWAY_MODE", inferred.get("gateway_mode") or ""),
        "gateway_url": _env_text("TEAMOS_OPENCLAW_GATEWAY_URL", inferred.get("gateway_url") or ""),
        "gateway_token": _env_text("TEAMOS_OPENCLAW_GATEWAY_TOKEN", inferred.get("gateway_token") or ""),
        "gateway_password": _env_text("TEAMOS_OPENCLAW_GATEWAY_PASSWORD", inferred.get("gateway_password") or ""),
        "gateway_transport": _env_text("TEAMOS_OPENCLAW_GATEWAY_TRANSPORT", inferred.get("gateway_transport") or "direct") or "direct",
        "allow_insecure_private_ws": _env_optional_truthy(
            "TEAMOS_OPENCLAW_ALLOW_INSECURE_PRIVATE_WS",
            str(inferred.get("allow_insecure_private_ws") or "").strip() in ("1", "true", "yes", "on"),
        ),
        "gateway_state_dir": _env_text("TEAMOS_OPENCLAW_STATE_DIR", _gateway_state_dir_default()),
        "updated_at": "",
    }


def _normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    base = _default_config()
    merged = dict(base)
    merged.update(raw or {})
    merged["enabled"] = bool(merged.get("enabled"))
    merged["channel"] = str(merged.get("channel") or base["channel"]).strip() or base["channel"]
    merged["target"] = str(merged.get("target") or "").strip()
    merged["bin"] = str(merged.get("bin") or base["bin"]).strip() or base["bin"]
    merged["message_prefix"] = str(merged.get("message_prefix") or base["message_prefix"]).strip() or base["message_prefix"]
    merged["path_patterns"] = _split_csv(merged.get("path_patterns")) or ["*"]
    merged["event_types"] = _split_csv(merged.get("event_types")) or list(_DEFAULT_EVENT_TYPES)
    merged["exclude_event_types"] = _split_csv(merged.get("exclude_event_types"))
    merged["gateway_mode"] = str(merged.get("gateway_mode") or ("remote" if str(merged.get("gateway_url") or "").strip() else "")).strip().lower()
    merged["gateway_url"] = str(merged.get("gateway_url") or "").strip()
    merged["gateway_token"] = str(merged.get("gateway_token") or "").strip()
    merged["gateway_password"] = str(merged.get("gateway_password") or "").strip()
    merged["gateway_transport"] = str(merged.get("gateway_transport") or "direct").strip().lower() or "direct"
    merged["allow_insecure_private_ws"] = bool(merged.get("allow_insecure_private_ws"))
    merged["gateway_state_dir"] = str(merged.get("gateway_state_dir") or _gateway_state_dir_default()).strip() or _gateway_state_dir_default()
    merged["updated_at"] = str(merged.get("updated_at") or "")
    return merged


def load_config() -> dict[str, Any]:
    return _normalize_config(_read_yaml(_config_path()))


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_config()
    next_cfg = dict(current)
    for key, value in (patch or {}).items():
        if value is None:
            continue
        next_cfg[key] = value
    next_cfg["updated_at"] = _utc_now_iso()
    next_cfg = _normalize_config(next_cfg)
    _write_yaml(_config_path(), next_cfg)
    return next_cfg


def load_state() -> dict[str, Any]:
    raw = _read_json(_state_path())
    state = {
        "cursor": int(raw.get("cursor") or 0),
        "last_run_at": str(raw.get("last_run_at") or ""),
        "last_event_id": int(raw.get("last_event_id") or 0),
        "last_delivery": raw.get("last_delivery") or {},
        "last_error": str(raw.get("last_error") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
    }
    return state


def save_state(patch: dict[str, Any]) -> dict[str, Any]:
    current = load_state()
    current.update(patch or {})
    current["updated_at"] = _utc_now_iso()
    _write_json(_state_path(), current)
    return current


def _which_openclaw(bin_name: str) -> str:
    path = shutil.which(str(bin_name or "openclaw"))
    return str(path or "")


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _openclaw_config_dir() -> Path:
    explicit = str(os.getenv("OPENCLAW_CONFIG_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    home = Path.home().resolve()
    return home / ".openclaw"


def _read_host_openclaw_config() -> dict[str, Any]:
    config_file = _openclaw_config_dir() / "openclaw.json"
    if not config_file.exists():
        return {}
    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _infer_gateway_defaults() -> dict[str, str]:
    if not _running_in_container():
        return {}
    raw = _read_host_openclaw_config()
    gateway = raw.get("gateway") if isinstance(raw.get("gateway"), dict) else {}
    if not gateway:
        return {}
    port = int(gateway.get("port") or 18789)
    host = "host.docker.internal"
    try:
        import socket

        resolved = socket.gethostbyname(host)
        if resolved:
            host = resolved
    except Exception:
        pass
    auth = gateway.get("auth") if isinstance(gateway.get("auth"), dict) else {}
    out = {
        "gateway_mode": "remote",
        "gateway_url": f"ws://{host}:{port}",
        "gateway_transport": "direct",
        "allow_insecure_private_ws": "1",
        "gateway_token": str(auth.get("token") or "").strip(),
        "gateway_password": str(auth.get("password") or "").strip(),
    }
    return {k: v for k, v in out.items() if v}


def _gateway_command_env(config: dict[str, Any]) -> tuple[dict[str, str], Optional[str]]:
    env = os.environ.copy()
    gateway_url = str(config.get("gateway_url") or "").strip()
    if not gateway_url:
        return env, None
    payload = deepcopy(_read_host_openclaw_config())
    if not isinstance(payload, dict):
        payload = {}
    gateway_block = payload.get("gateway")
    if not isinstance(gateway_block, dict):
        gateway_block = {}
        payload["gateway"] = gateway_block
    remote_block = gateway_block.get("remote")
    if not isinstance(remote_block, dict):
        remote_block = {}
        gateway_block["remote"] = remote_block
    gateway_block["mode"] = str(config.get("gateway_mode") or "remote").strip() or "remote"
    remote_block["url"] = gateway_url
    remote_block["transport"] = str(config.get("gateway_transport") or "direct").strip() or "direct"
    token = str(config.get("gateway_token") or "").strip()
    password = str(config.get("gateway_password") or "").strip()
    if token:
        remote_block["token"] = token
    if password:
        remote_block["password"] = password
    state_dir = Path(str(config.get("gateway_state_dir") or _gateway_state_dir_default())).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="teamos-openclaw-", suffix=".json", dir=str(state_dir))
    os.close(fd)
    Path(temp_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    env["OPENCLAW_CONFIG_PATH"] = temp_path
    env["OPENCLAW_STATE_DIR"] = str(state_dir)
    env.pop("OPENCLAW_CONFIG_DIR", None)
    if bool(config.get("allow_insecure_private_ws")) and gateway_url.startswith("ws://"):
        env["OPENCLAW_ALLOW_INSECURE_PRIVATE_WS"] = "1"
    return env, temp_path


def _cleanup_gateway_command_env(temp_path: Optional[str]) -> None:
    if not temp_path:
        return
    try:
        Path(temp_path).unlink(missing_ok=True)
    except Exception:
        pass


def detect_openclaw(*, probe_health: bool = True) -> dict[str, Any]:
    cfg = load_config()
    bin_path = _which_openclaw(cfg.get("bin") or "openclaw")
    config_dir = _openclaw_config_dir()
    config_file = config_dir / "openclaw.json"
    gateway_url = str(cfg.get("gateway_url") or "").strip()
    out = {
        "enabled": bool(cfg.get("enabled")),
        "configured": bool(cfg.get("target")),
        "bin": str(cfg.get("bin") or "openclaw"),
        "bin_path": bin_path,
        "bin_exists": bool(bin_path),
        "config_dir": str(config_dir),
        "config_file": str(config_file),
        "config_exists": config_file.exists(),
        "channel": str(cfg.get("channel") or "telegram"),
        "target": str(cfg.get("target") or ""),
        "gateway_mode": str(cfg.get("gateway_mode") or ""),
        "gateway_url": gateway_url,
        "gateway_transport": str(cfg.get("gateway_transport") or ""),
        "allow_insecure_private_ws": bool(cfg.get("allow_insecure_private_ws")),
        "gateway_state_dir": str(cfg.get("gateway_state_dir") or ""),
        "path_patterns": list(cfg.get("path_patterns") or []),
        "event_types": list(cfg.get("event_types") or []),
        "exclude_event_types": list(cfg.get("exclude_event_types") or []),
        "available": bool(bin_path) and (config_file.exists() or bool(gateway_url)),
    }
    if probe_health:
        try:
            health_out = health(timeout_ms=4000)
            out["health"] = health_out
            out["healthy"] = bool(health_out.get("ok"))
        except Exception as exc:
            out["health"] = {"ok": False, "error": str(exc)[:300]}
            out["healthy"] = False
    else:
        out["health"] = {}
        out["healthy"] = False
    return out


def health(*, timeout_ms: int = 10000) -> dict[str, Any]:
    cfg = load_config()
    bin_path = _which_openclaw(cfg.get("bin") or "openclaw")
    if not bin_path:
        raise OpenClawReporterError("openclaw binary not found")
    env, temp_path = _gateway_command_env(cfg)
    try:
        proc = subprocess.run(
            [bin_path, "health", "--json", "--timeout", str(int(timeout_ms))],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=max(5, int(timeout_ms / 1000) + 2),
            env=env,
        )
    finally:
        _cleanup_gateway_command_env(temp_path)
    stdout = str(proc.stdout or "").strip()
    if proc.returncode != 0:
        raise OpenClawReporterError((str(proc.stderr or stdout or "openclaw health failed")).strip()[:500])
    try:
        raw = json.loads(stdout)
    except Exception as exc:
        raise OpenClawReporterError(f"invalid openclaw health json: {exc}") from exc
    return raw if isinstance(raw, dict) else {"ok": False, "raw": raw}


def _matches_pattern(value: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    if not value:
        return any(p in ("*", "**") for p in patterns)
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _matches_event_type(event_type: str, config: dict[str, Any]) -> bool:
    et = str(event_type or "").strip()
    if not et:
        return False
    if any(et.startswith(prefix) for prefix in _IGNORED_EVENT_TYPES):
        return False
    excluded = list(config.get("exclude_event_types") or [])
    if excluded and any(fnmatch.fnmatch(et, pat) for pat in excluded):
        return False
    wanted = list(config.get("event_types") or [])
    if not wanted:
        return True
    return any(fnmatch.fnmatch(et, pat) for pat in wanted)


def _coerce_str_list(raw: Any) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        seq = raw
    elif isinstance(raw, tuple):
        seq = list(raw)
    else:
        seq = [raw]
    for item in seq:
        val = str(item or "").strip()
        if val:
            out.append(val)
    return out


def _derive_paths(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in (
        "paths",
        "allowed_paths",
        "changed_files",
        "staged_files",
        "documentation_paths",
    ):
        out.extend(_coerce_str_list(payload.get(key)))
    for key in ("path", "module_path"):
        out.extend(_coerce_str_list(payload.get(key)))
    release = payload.get("release")
    if isinstance(release, dict):
        out.extend(_coerce_str_list(release.get("staged_files")))
    cleaned: list[str] = []
    for raw in out:
        text = raw.replace("\\", "/").strip()
        if not text:
            continue
        while text.startswith("./"):
            text = text[2:]
        if text.startswith(str(team_os_root()).replace("\\", "/")):
            try:
                text = str(Path(text).resolve().relative_to(team_os_root().resolve())).replace("\\", "/")
            except Exception:
                pass
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


def _format_message(event: dict[str, Any], *, config: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    lines = [str(config.get("message_prefix") or "[TeamOS 上报]")]
    event_type = str(event.get("event_type") or "").strip()
    if event_type:
        lines.append(f"事件: {event_type}")
    title = str(payload.get("title") or payload.get("summary") or payload.get("objective") or "").strip()
    if title:
        lines.append(f"摘要: {title[:160]}")
    project_id = str(event.get("project_id") or payload.get("project_id") or "teamos").strip() or "teamos"
    workstream_id = str(event.get("workstream_id") or payload.get("workstream_id") or "general").strip() or "general"
    lines.append(f"项目: {project_id}/{workstream_id}")
    for key, label in (("task_id", "任务"), ("proposal_id", "提案"), ("run_id", "运行"), ("issue_number", "Issue")):
        value = str(payload.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    module = str(payload.get("module") or payload.get("lane") or "").strip()
    if module:
        lines.append(f"模块: {module}")
    paths = _derive_paths(payload)
    if paths:
        lines.append(f"路径: {', '.join(paths[:5])}")
    for key, label in (("issue_url", "Issue 链接"), ("discussion_issue_url", "讨论链接"), ("pull_request_url", "PR 链接")):
        value = str(payload.get(key) or "").strip()
        if not value and isinstance(payload.get("release"), dict):
            value = str((payload.get("release") or {}).get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    error = str(payload.get("error") or payload.get("reason") or "").strip()
    if error:
        lines.append(f"错误: {error[:300]}")
    lines.append(f"时间: {str(event.get('ts') or _utc_now_iso())}")
    return "\n".join(lines)


def _paths_match(paths: list[str], config: dict[str, Any]) -> bool:
    patterns = list(config.get("path_patterns") or [])
    if not patterns:
        return True
    if any(p in ("*", "**") for p in patterns):
        return True
    if not paths:
        return False
    return any(_matches_pattern(path, patterns) for path in paths)


def _run_send(*, channel: str, target: str, message: str, bin_name: str, dry_run: bool) -> dict[str, Any]:
    config = load_config()
    bin_path = _which_openclaw(bin_name)
    if not bin_path:
        raise OpenClawReporterError("openclaw binary not found")
    cmd = [bin_path, "message", "send", "--channel", channel, "--target", target, "--message", message, "--json"]
    if dry_run:
        cmd.append("--dry-run")
    env, temp_path = _gateway_command_env(config)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
            env=env,
        )
    finally:
        _cleanup_gateway_command_env(temp_path)
    stdout = str(proc.stdout or "").strip()
    stderr = str(proc.stderr or "").strip()
    if proc.returncode != 0:
        raise OpenClawReporterError((stderr or stdout or "openclaw message send failed")[:500])
    try:
        payload = json.loads(stdout) if stdout else {"ok": True}
    except Exception:
        payload = {"ok": True, "raw": stdout}
    if not isinstance(payload, dict):
        payload = {"ok": True, "raw": payload}
    return payload


def report_event(event: Any, *, dry_run: bool = False, config: Optional[dict[str, Any]] = None, detection: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    config = _normalize_config(config or load_config())
    evt = {
        "id": int(getattr(event, "id", 0) or (event.get("id") if isinstance(event, dict) else 0) or 0),
        "ts": str(getattr(event, "ts", "") or (event.get("ts") if isinstance(event, dict) else "") or ""),
        "event_type": str(getattr(event, "event_type", "") or (event.get("event_type") if isinstance(event, dict) else "") or ""),
        "actor": str(getattr(event, "actor", "") or (event.get("actor") if isinstance(event, dict) else "") or ""),
        "project_id": str(getattr(event, "project_id", "") or (event.get("project_id") if isinstance(event, dict) else "") or ""),
        "workstream_id": str(getattr(event, "workstream_id", "") or (event.get("workstream_id") if isinstance(event, dict) else "") or ""),
        "payload": getattr(event, "payload", None) if not isinstance(event, dict) else event.get("payload"),
    }
    if not isinstance(evt["payload"], dict):
        evt["payload"] = {}
    detection = detection or detect_openclaw()
    if not bool(config.get("enabled")):
        return {"ok": True, "sent": False, "reason": "disabled", "event_id": evt["id"], "detection": detection}
    if not str(config.get("target") or "").strip():
        return {"ok": False, "sent": False, "reason": "missing_target", "event_id": evt["id"], "detection": detection}
    if not detection.get("available"):
        return {"ok": False, "sent": False, "reason": "openclaw_unavailable", "event_id": evt["id"], "detection": detection}
    if not _matches_event_type(evt["event_type"], config):
        return {"ok": True, "sent": False, "reason": "event_filtered", "event_id": evt["id"]}
    paths = _derive_paths(evt["payload"])
    if not _paths_match(paths, config):
        return {"ok": True, "sent": False, "reason": "path_filtered", "event_id": evt["id"], "paths": paths}
    message = _format_message(evt, config=config)
    send_out = _run_send(
        channel=str(config.get("channel") or "telegram"),
        target=str(config.get("target") or "").strip(),
        message=message,
        bin_name=str(config.get("bin") or "openclaw"),
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "sent": True,
        "event_id": evt["id"],
        "paths": paths,
        "channel": str(config.get("channel") or "telegram"),
        "target": str(config.get("target") or ""),
        "message": message,
        "delivery": send_out,
    }


def report_manual(*, message: str, channel: str = "", target: str = "", path: str = "", event_type: str = "OPENCLAW_TEST", dry_run: bool = False) -> dict[str, Any]:
    config = load_config()
    manual_cfg = dict(config)
    if channel:
        manual_cfg["channel"] = str(channel).strip()
    if target:
        manual_cfg["target"] = str(target).strip()
    if not str(manual_cfg.get("target") or "").strip():
        raise OpenClawReporterError("missing OpenClaw target")
    evt = {
        "id": 0,
        "ts": _utc_now_iso(),
        "event_type": str(event_type or "OPENCLAW_TEST").strip() or "OPENCLAW_TEST",
        "actor": "control-plane.manual",
        "project_id": "teamos",
        "workstream_id": "general",
        "payload": {
            "summary": str(message or "").strip() or "Team OS OpenClaw test message",
            "path": str(path or "").strip(),
        },
    }
    detection = detect_openclaw()
    if not detection.get("available"):
        raise OpenClawReporterError("openclaw is not available")
    rendered = _format_message(evt, config=manual_cfg)
    delivery = _run_send(
        channel=str(manual_cfg.get("channel") or "telegram"),
        target=str(manual_cfg.get("target") or "").strip(),
        message=rendered,
        bin_name=str(manual_cfg.get("bin") or "openclaw"),
        dry_run=dry_run,
    )
    return {"ok": True, "message": rendered, "delivery": delivery, "channel": manual_cfg.get("channel"), "target": manual_cfg.get("target")}


def sweep_events(*, db: Any, dry_run: bool = False, limit: int = 100) -> dict[str, Any]:
    config = load_config()
    detection = detect_openclaw()
    state = load_state()
    cursor = int(state.get("cursor") or 0)
    rows = list(db.list_events(after_id=cursor, limit=max(1, int(limit))))
    scanned = len(rows)
    sent = 0
    skipped = 0
    last_event_id = cursor
    errors: list[dict[str, Any]] = []
    last_delivery: dict[str, Any] = {}
    for row in rows:
        last_event_id = max(last_event_id, int(getattr(row, "id", 0) or 0))
        try:
            result = report_event(row, dry_run=dry_run, config=config, detection=detection)
            if bool(result.get("sent")):
                sent += 1
                last_delivery = result
            else:
                skipped += 1
        except Exception as exc:
            errors.append({"event_id": int(getattr(row, "id", 0) or 0), "error": str(exc)[:300]})
    save_state(
        {
            "cursor": last_event_id,
            "last_run_at": _utc_now_iso(),
            "last_event_id": last_event_id,
            "last_delivery": last_delivery,
            "last_error": errors[0]["error"] if errors else "",
        }
    )
    return {
        "ok": not errors,
        "enabled": bool(config.get("enabled")),
        "scanned": scanned,
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "cursor": last_event_id,
    }
