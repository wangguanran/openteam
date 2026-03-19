#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from _common import PipelineError, add_default_args, read_text, render_template, resolve_repo_root, ts_compact_utc, utc_now_iso, write_text


def _run(repo: Path, cmd: list[str], *, timeout_sec: int = 10, max_chars: int = 6000) -> str:
    p = subprocess.run(cmd, cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout_sec, check=False)
    out = (p.stdout or "") + ("\n" + (p.stderr or "") if p.stderr else "")
    out = out.strip()
    if len(out) > max_chars:
        out = out[: max_chars - 20] + "\n...<truncated>..."
    return out


def _git_sha(repo: Path) -> str:
    return _run(repo, ["git", "rev-parse", "--short", "HEAD"], timeout_sec=5, max_chars=80).strip()


def _arch_overview() -> str:
    lines = [
        "- `team-os/teamos`：CLI 客户端（默认连本机 Control Plane）。",
        "- `scaffolds/runtime/orchestrator/app/main.py`：Control Plane（FastAPI）模板代码。",
        "- 真相源（scope=teamos）在 repo 内：`.team-os/ledger`、`.team-os/logs`、`docs/product/teamos/requirements`。",
        "- 真相源（scope=project:<id>）必须在 Workspace（repo 外）。",
        "- GitHub Projects v2 为视图层（mapping 在 `integrations/github_projects/mapping.yaml`）。",
    ]
    return "\n".join(lines)


def _modules() -> str:
    lines = [
        "- CLI：`team-os/teamos`。",
        "- Pipelines（本次新增）：`team-os/scripts/pipelines/`。",
        "- Governance：`team-os/scripts/governance/`（repo purity 等）。",
        "- Runtime/Task 入口实现：`team-os/scripts/runtime/`、`team-os/scripts/tasks/`、`team-os/scripts/issues/`、`team-os/scripts/skills/`、`team-os/scripts/policy/`。",
        "- Requirements 协议：`team-os/scripts/requirements/` + runtime template `app/requirements_store.py`。",
        "- Panel Sync：runtime template `app/panel_github_sync.py`（通过 Control Plane 触发）。",
        "- Runtime 模板：`team-os/scaffolds/runtime/`（生成到 repo 外 `team-os-runtime/`）。",
    ]
    return "\n".join(lines)


def _entrypoints() -> str:
    lines = [
        "- CLI：`team-os/teamos`。",
        "- Shell 入口：`team-os/scripts/teamos.sh` -> `team-os/teamos`。",
        "- Pipelines：`team-os/scripts/pipelines/*.py`。",
        "- Requirements 真相源：`team-os/docs/product/teamos/requirements/`。",
        "- Prompt 真相源（teamos）：`team-os/specs/prompts/teamos/`。",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repo understanding gate (generate docs/product/teamos/REPO_UNDERSTANDING.md)")
    add_default_args(ap)
    ap.add_argument("--task-id", default="", help="optional task id to embed in the artifact")
    ap.add_argument("--out", default="docs/product/teamos/REPO_UNDERSTANDING.md")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    out_path = repo / str(args.out)

    tpl_path = repo / "templates" / "content" / "repo_understanding.md.j2"
    if not tpl_path.exists():
        raise PipelineError(f"missing template: {tpl_path}")

    evidence_tree = "\n\n".join(
        [
            "$ ls -la",
            _run(repo, ["ls", "-la"], timeout_sec=5),
            "",
            "$ find . -maxdepth 2 -type d (selected)",
            _run(repo, ["bash", "-lc", "find . -maxdepth 2 -type d | sort | sed -n '1,120p'"], timeout_sec=10),
        ]
    ).strip()
    evidence_rg = "\n\n".join(
        [
            "$ rg -n \"@app.(get|post)\\(\\\"/v1/\" scaffolds/runtime/orchestrator/app/main.py | head",
            _run(repo, ["bash", "-lc", "rg -n \"@app\\.(get|post)\\(\\\"/v1/\" scaffolds/runtime/orchestrator/app/main.py | head -n 40 || true"], timeout_sec=10),
            "",
            "$ rg -n \"cmd_task_new|cmd_req_add|cmd_team_run|cmd_team_coding_run\" teamos",
            _run(repo, ["bash", "-lc", "rg -n \"cmd_task_new|cmd_req_add|cmd_team_run|cmd_team_coding_run\" teamos | head -n 80 || true"], timeout_sec=10),
        ]
    ).strip()
    evidence_scripts = "\n\n".join(
        [
            "$ ls -la scripts",
            _run(repo, ["ls", "-la", "scripts"], timeout_sec=5),
            "",
            "$ ls -la scripts/pipelines",
            _run(repo, ["bash", "-lc", "ls -la scripts/pipelines 2>/dev/null || true"], timeout_sec=5),
        ]
    ).strip()

    body = render_template(
        read_text(tpl_path),
        {
            "REPO": str(repo),
            "GENERATED_AT": utc_now_iso(),
            "TASK_ID": str(args.task_id or ""),
            "GIT_SHA": _git_sha(repo),
            "ARCH_OVERVIEW": _arch_overview(),
            "MODULES": _modules(),
            "ENTRYPOINTS": _entrypoints(),
            "BUILD_COMMANDS": "\n".join(["python3 -m unittest -q", "./teamos --help"]),
            "TEST_COMMANDS": "\n".join(["python3 -m unittest -q"]),
            "DEPENDENCIES": "\n".join(["- Python3", "- pyyaml (PyYAML)", "- tomli (for config parsing)"]),
            "RISKS": "\n".join(
                [
                    "- Control Plane runtime 可能与 repo 模板不同步，导致 openapi 缺失端点（doctor 会失败）。",
                    "- 自我优化若以 CLI auto-wake 方式触发，可能产生非任务化写入（需要改为 daemon + leader-only）。",
                    "- 任何 project scope 真相源写入 repo 会破坏 repo purity（必须强制拦截）。",
                ]
            ),
            "SUGGESTIONS": "\n".join(
                [
                    "- 所有真相源写入改为 pipelines 统一入口 + schema 校验。",
                    "- `teamos task close` 作为 commit/push 前闸门（tests/purity/secrets）。",
                    "- prompt/requirements/projects sync/team workflow 全部幂等化并可全量重建。",
                ]
            ),
            "ROLLBACK": "\n".join(
                [
                    "- 以 git 为回滚机制：revert 单个 task 分支的 merge/commit。",
                    "- truth-source 文件由 pipelines 生成，必要时可用 rebuild/compile 重新生成。",
                ]
            ),
            "EVIDENCE_TREE": evidence_tree,
            "EVIDENCE_RG": evidence_rg,
            "EVIDENCE_SCRIPTS": evidence_scripts,
        },
    ).rstrip() + "\n"

    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep a timestamped copy for audit continuity when regenerating.
        prev = out_path
        if prev.exists():
            snap = out_path.parent / f"REPO_UNDERSTANDING_{ts_compact_utc()}.md"
            snap.write_text(prev.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        write_text(out_path, body, dry_run=False)

    print(f"repo_understanding_path={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
