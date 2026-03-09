#!/usr/bin/env python3
"""
Deterministic pipeline utilities (no network, no LLM).

Design goals:
- Minimal dependencies (stdlib + pyyaml already used in this repo).
- Deterministic output: stable ordering + explicit timestamps.
- Defensive governance: prevent writing project truth sources into the team-os repo.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import yaml


class PipelineError(Exception):
    pass


_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ts_compact_utc() -> str:
    return utc_now_iso().replace(":", "").replace("-", "")


def _looks_like_teamos_repo(root: Path) -> bool:
    markers = (
        (root / "TEAMOS.md").exists(),
        (root / "templates" / "runtime" / "orchestrator").exists(),
        (root / "schemas").exists(),
    )
    return (root / "scripts" / "pipelines").exists() and any(markers)


def repo_root() -> Path:
    """
    Resolve the team-os repo root.
    Priority:
    1) env TEAM_OS_REPO_PATH
    2) relative to this file location
    3) git rev-parse
    """
    env = str(os.getenv("TEAM_OS_REPO_PATH") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if _looks_like_teamos_repo(p):
            return p

    p = Path(__file__).resolve()
    try:
        cand = p.parents[2]
        if _looks_like_teamos_repo(cand):
            return cand
    except Exception:
        pass

    p2 = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if p2.returncode == 0:
        top = (p2.stdout or b"").decode("utf-8", errors="replace").strip()
        if top:
            rr = Path(top).expanduser().resolve()
            if _looks_like_teamos_repo(rr):
                return rr

    raise PipelineError("Cannot locate team-os repo root (set TEAM_OS_REPO_PATH or run from within the repo)")


def runtime_root(*, override: str = "") -> Path:
    """
    Resolve runtime root outside repo.
    Priority:
    1) explicit override
    2) env TEAMOS_RUNTIME_ROOT
    3) <repo_root>/../team-os-runtime
    """
    v = str(override or "").strip()
    if not v:
        v = str(os.getenv("TEAMOS_RUNTIME_ROOT") or "").strip()
    if not v:
        v = str(repo_root().parent / "team-os-runtime")
    return Path(v).expanduser().resolve()


def runtime_state_root(*, override: str = "") -> Path:
    return runtime_root(override=override) / "state"


def runtime_workspace_root(*, override: str = "") -> Path:
    return runtime_root(override=override) / "workspace"


def runtime_hub_root(*, override: str = "") -> Path:
    return runtime_root(override=override) / "hub"


def workspace_root(*, override: str = "") -> Path:
    v = str(override or "").strip()
    if not v:
        v = str(os.getenv("TEAMOS_WORKSPACE_ROOT") or "").strip()
    if not v:
        v = str(runtime_workspace_root())
    return Path(v).expanduser().resolve()


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def safe_project_id(project_id: str) -> str:
    pid = str(project_id or "").strip()
    if not pid:
        raise PipelineError("project_id is required")
    if pid != pid.lower():
        raise PipelineError(f"invalid project_id={pid!r} (must be lowercase)")
    if not _PROJECT_ID_RE.match(pid):
        raise PipelineError(f"invalid project_id={pid!r} (allowed: [a-z0-9][a-z0-9_-]{{0,63}})")
    if any(x in pid for x in ("/", "\\", "..")):
        raise PipelineError(f"invalid project_id={pid!r}")
    return pid


def parse_scope(scope: str) -> tuple[str, str]:
    s = str(scope or "").strip()
    if not s:
        raise PipelineError("scope is required: teamos | project:<id>")
    if s == "teamos":
        return ("teamos", "teamos")
    if s.startswith("project:"):
        return (s, safe_project_id(s.split(":", 1)[1].strip()))
    # backward compat: treat bare id as project:<id>
    return (f"project:{safe_project_id(s)}", safe_project_id(s))


def ensure_dir(path: Path, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.mkdir(parents=True, exist_ok=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_text(path: Path, text: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(read_text(path)) or {}


def write_yaml(path: Path, data: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: Path, obj: Any, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True).rstrip() + "\n", encoding="utf-8")


def append_jsonl(path: Path, obj: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


_TPL_RE = re.compile(r"{{\s*([A-Z0-9_]+)\s*}}")


def render_template(tpl_text: str, values: dict[str, str]) -> str:
    """
    Minimal deterministic templating:
    - replaces {{KEY}} with values["KEY"] (missing keys -> empty string)
    """

    def repl(m: re.Match[str]) -> str:
        k = m.group(1).strip()
        return str(values.get(k, ""))

    return _TPL_RE.sub(repl, tpl_text)


def load_schema(schema_path: Path) -> dict[str, Any]:
    try:
        obj = read_json(schema_path)
    except Exception as e:
        raise PipelineError(f"failed to load schema: {schema_path}: {e}") from e
    if not isinstance(obj, dict):
        raise PipelineError(f"schema must be an object: {schema_path}")
    return obj


def _type_ok(value: Any, want: Any) -> bool:
    def one(t: str) -> bool:
        if t == "object":
            return isinstance(value, dict)
        if t == "array":
            return isinstance(value, list)
        if t == "string":
            return isinstance(value, str)
        if t == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if t == "number":
            return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
        if t == "boolean":
            return isinstance(value, bool)
        if t == "null":
            return value is None
        return True

    if isinstance(want, list):
        return any(one(str(x)) for x in want)
    if isinstance(want, str):
        return one(want)
    return True


def validate_schema(obj: Any, schema: dict[str, Any], *, at: str = "$") -> list[str]:
    """
    Minimal JSON Schema validator (subset).
    Supports: type, required, properties, items, enum, minLength, minimum, additionalProperties(false).
    """
    errors: list[str] = []

    if "type" in schema and not _type_ok(obj, schema.get("type")):
        errors.append(f"{at}: type mismatch want={schema.get('type')} got={type(obj).__name__}")
        return errors

    if "enum" in schema:
        enum = schema.get("enum") or []
        if obj not in enum:
            errors.append(f"{at}: not in enum")

    if isinstance(obj, str) and "minLength" in schema:
        try:
            if len(obj) < int(schema.get("minLength")):
                errors.append(f"{at}: minLength")
        except Exception:
            pass

    if (isinstance(obj, int) or isinstance(obj, float)) and "minimum" in schema:
        try:
            if float(obj) < float(schema.get("minimum")):
                errors.append(f"{at}: minimum")
        except Exception:
            pass

    if isinstance(obj, dict):
        required = schema.get("required") or []
        if isinstance(required, list):
            for k in required:
                if str(k) not in obj:
                    errors.append(f"{at}: missing required property: {k}")

        props = schema.get("properties") or {}
        addl = schema.get("additionalProperties", True)
        if isinstance(props, dict):
            for k, v in obj.items():
                k2 = str(k)
                if k2 in props and isinstance(props[k2], dict):
                    errors.extend(validate_schema(v, props[k2], at=f"{at}.{k2}"))
                else:
                    if addl is False:
                        errors.append(f"{at}: additionalProperties disallowed: {k2}")

    if isinstance(obj, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, it in enumerate(obj):
                errors.extend(validate_schema(it, items, at=f"{at}[{i}]"))

    return errors


def validate_or_die(obj: Any, schema_path: Path, *, label: str) -> None:
    schema = load_schema(schema_path)
    errs = validate_schema(obj, schema)
    if errs:
        raise PipelineError(f"schema_validation_failed label={label} schema={schema_path} errors={errs[:20]}")


def add_default_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--repo-root", default="", help="override repo root (default: auto-detect)")
    ap.add_argument(
        "--workspace-root",
        default="",
        help="override workspace root (default: TEAMOS_WORKSPACE_ROOT or <runtime_root>/workspace)",
    )


def resolve_repo_root(args: argparse.Namespace) -> Path:
    if str(getattr(args, "repo_root", "") or "").strip():
        p = Path(str(getattr(args, "repo_root"))).expanduser().resolve()
        if not _looks_like_teamos_repo(p):
            raise PipelineError(f"invalid --repo-root (missing Team-OS repo markers): {p}")
        return p
    return repo_root()


def resolve_workspace_root(args: argparse.Namespace) -> Path:
    return workspace_root(override=str(getattr(args, "workspace_root", "") or ""))
