#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_ROOT = REPO_ROOT / "scaffolds" / "runtime" / "orchestrator"
if str(ORCHESTRATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_ROOT))

from app import improvement_store  # noqa: E402
from app.teams.repo_improvement import planning  # noqa: E402


def _print_section(title: str) -> None:
    print()
    print(title)
    print("=" * len(title))


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _resolve_target(*, target_id: str, project_id: str) -> dict:
    existing = improvement_store.get_target(target_id)
    if existing:
        project_id = str(existing.get("project_id") or project_id or "teamos").strip() or "teamos"
        return planning._resolve_target(
            target_id=target_id,
            repo_path=str(existing.get("repo_root") or "").strip(),
            repo_url=str(existing.get("repo_url") or "").strip(),
            repo_locator=str(existing.get("repo_locator") or "").strip(),
            project_id=project_id,
        )
    return planning._resolve_target(
        target_id=target_id,
        repo_path="",
        repo_url="",
        repo_locator="",
        project_id=project_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repo-improvement bug discovery live and print the whole-repository CrewAI scan result.")
    parser.add_argument("--target-id", required=True, help="Improvement target id")
    parser.add_argument("--project-id", default="", help="Project id override")
    parser.add_argument("--json", action="store_true", help="Print the structured repository scan result as JSON")
    args = parser.parse_args()

    project_id = str(args.project_id or "teamos").strip() or "teamos"
    target = _resolve_target(target_id=str(args.target_id).strip(), project_id=project_id)
    repo_root = Path(str(target.get("repo_root") or "")).expanduser().resolve()
    scan_repo_root = planning._prepare_discovery_repo(source_repo_root=repo_root, target=target)
    repo_context = planning.collect_repo_context(
        repo_root=repo_root,
        scan_repo_root=scan_repo_root,
        explicit_repo_locator=str(target.get("repo_locator") or ""),
        target_id=str(target.get("target_id") or ""),
    )
    bug_scan_limit = planning._scan_limit(0, planning._lane_max_candidates("bug", project_id=project_id))
    llm = planning._crewai_llm()

    if not args.json:
        _print_section("Repo Improvement Bug Scan Live")
        print(f"target_id: {target.get('target_id')}")
        print(f"project_id: {project_id}")
        print(f"repo_root: {repo_root}")
        print(f"scan_repo_root: {scan_repo_root}")
        print(f"repo_locator: {repo_context.get('repo_locator')}")
        print(f"model: {getattr(llm, 'model', '')}")
        print(f"reasoning_effort: {getattr(llm, 'reasoning_effort', '')}")
        _print_section("Baseline Checks")
        baseline_checks = list(((repo_context.get("repository_inspection") or {}).get("baseline_checks") or []))
        if not baseline_checks:
            print("(none)")
        for item in baseline_checks:
            if not isinstance(item, dict):
                continue
            print(f"- {item.get('command')}: status={item.get('status')} returncode={item.get('returncode')}")

    if not args.json:
        _print_section("Repository Scan")
        print("calling CrewAI...")
    overall_started = time.time()
    result, debug_task = planning._structured_bug_scan_for_repo(
        repo_context=repo_context,
        bug_scan_limit=max(1, int(bug_scan_limit or 0)),
        bug_scan_dormant=False,
        verbose=True,
    )
    elapsed = time.time() - overall_started
    finding_count = len(list(result.findings or []))
    payload = {
        "elapsed_sec": round(elapsed, 2),
        "finding_count": finding_count,
        "result": result.model_dump(),
        "debug_task": debug_task,
    }
    if args.json:
        print(_dump(payload))
        return 0
    print(f"elapsed_sec: {elapsed:.2f}")
    print(f"finding_count: {finding_count}")
    print()
    print("CrewAI output:")
    print(_dump(result.model_dump()))

    _print_section("Summary")
    print("scan_mode: repository")
    print(f"total_findings: {finding_count}")
    print(f"total_elapsed_sec: {elapsed:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
