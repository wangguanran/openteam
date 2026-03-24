#!/usr/bin/env python3
import argparse
import json
import sys

from _common import add_template_app_to_syspath, requirements_dir, parse_scope


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Baseline Drift Check (structural/determinism)")
    ap.add_argument("--scope", required=True, help="openteam | project:<id>")
    ap.add_argument("--fix", action="store_true", help="apply safe fixes (rewrite REQUIREMENTS.md, update baseline metadata)")
    args = ap.parse_args(argv)

    scope, pid = parse_scope(args.scope)
    req_dir = requirements_dir(scope, ensure=args.fix)

    add_template_app_to_syspath()
    from app.requirements_store import drift_check  # noqa: E402

    out = drift_check(req_dir, project_id=pid, scope=scope, fix=bool(args.fix))
    print(
        json.dumps(
            {
                "ok": out.ok,
                "fixed": out.fixed,
                "need_pm_decision": out.need_pm_decision,
                "report_path": out.report_path,
                "drift_points": out.drift_points,
                "actions_taken": out.actions_taken,
            },
            ensure_ascii=False,
            indent=2,
        ).rstrip()
    )
    return 0 if out.ok else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

