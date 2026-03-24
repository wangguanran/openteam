#!/usr/bin/env python3
import argparse
import json
import sys

from _common import add_template_app_to_syspath, requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Deterministic render: requirements.yaml -> REQUIREMENTS.md")
    ap.add_argument("--scope", required=True, help="openteam | project:<id>")
    args = ap.parse_args(argv)

    scope, pid = parse_scope(args.scope)
    req_dir = requirements_dir(scope, ensure=False)
    add_template_app_to_syspath()
    from app.requirements_store import rebuild_requirements_md  # noqa: E402

    out = rebuild_requirements_md(req_dir, project_id=pid)
    print(json.dumps(out, ensure_ascii=False, indent=2).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

