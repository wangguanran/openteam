#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

import yaml

from _common import add_template_app_to_syspath, parse_scope, repo_root, requirements_dir, workspace_root


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 2


def _utc_now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_raw_item(item: dict) -> list[str]:
    errs: list[str] = []
    for k in ("timestamp", "scope", "channel", "text"):
        if not str(item.get(k) or "").strip():
            errs.append(f"missing {k}")
    ch = str(item.get("channel") or "").strip()
    if ch and ch not in ("cli", "api", "chat", "import", "migration", "baseline"):
        errs.append(f"invalid channel={ch!r}")
    return errs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Requirements doctor (v2 Raw-First)")
    ap.add_argument("--scope", required=True, help="teamos | project:<id>")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    scope, pid = parse_scope(args.scope)
    rr = repo_root()
    ws = workspace_root()
    req_dir = requirements_dir(scope, ensure=False).resolve()

    # Path governance.
    if scope == "teamos":
        if not _is_within(req_dir, rr):
            return _fail(f"teamos requirements_dir must be inside repo: dir={req_dir} repo={rr}")
    else:
        if not _is_within(req_dir, ws):
            return _fail(f"project requirements_dir must be inside workspace: dir={req_dir} workspace={ws}")
        if _is_within(req_dir, rr):
            return _fail(f"project requirements_dir must NOT be inside repo: dir={req_dir} repo={rr}")

    # Structure.
    must_dirs = [req_dir / "conflicts", req_dir / "baseline"]
    missing_dirs = [str(d) for d in must_dirs if not d.exists()]
    if missing_dirs:
        return _fail("missing_dirs=" + ",".join(missing_dirs[:5]))

    # Raw inputs.
    raw = req_dir / "raw_inputs.jsonl"
    if raw.exists():
        bad = 0
        for i, ln in enumerate(raw.read_text(encoding="utf-8").splitlines(), 1):
            if not ln.strip():
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                bad += 1
                if bad < 5 and (not args.quiet):
                    print(f"raw_inputs.jsonl: invalid json at line {i}")
                continue
            if not isinstance(obj, dict):
                bad += 1
                continue
            errs = _validate_raw_item(obj)
            if errs:
                bad += 1
                if bad < 5 and (not args.quiet):
                    print(f"raw_inputs.jsonl: schema errors line {i}: {errs}")
        if bad:
            return _fail(f"raw_inputs.jsonl has invalid entries: {bad}")

    # Baseline v1 must exist once Expanded exists.
    y = req_dir / "requirements.yaml"
    b1 = req_dir / "baseline" / "original_description_v1.md"
    if y.exists() and (not b1.exists()):
        return _fail("baseline v1 missing but requirements.yaml exists (run req add once or set baseline v1)")

    # Expanded determinism.
    if y.exists():
        try:
            data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
        except Exception as e:
            return _fail(f"requirements.yaml parse error: {str(e)[:200]}")
        if not isinstance(data, dict):
            return _fail("requirements.yaml invalid: root is not a mapping")
        if str(data.get("project_id") or "").strip() != pid:
            return _fail(f"requirements.yaml project_id mismatch found={data.get('project_id')!r} expected={pid!r}")

        add_template_app_to_syspath()
        from app.requirements_store import render_requirements_md  # noqa: E402

        reqs = list(data.get("requirements") or [])
        expected = render_requirements_md(pid, reqs)
        md = req_dir / "REQUIREMENTS.md"
        actual = md.read_text(encoding="utf-8") if md.exists() else ""
        if actual != expected:
            return _fail(f"REQUIREMENTS.md drift (run: teamos req rebuild --scope {scope})")

    if not args.quiet:
        print(f"ok=true scope={scope} requirements_dir={req_dir} checked_at={_utc_now_iso()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

