# Boundary And Verification Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move high-signal self-scope artifacts off repo-local `.openteam` paths and make CI fail loudly instead of masking errors.

**Architecture:** Add shell helpers that resolve runtime-root self artifact locations, then point task scaffolding and skill boot at those helpers. Lock the contract in with regression tests that exercise the shell scripts and with workflow tests that prevent CI from swallowing failures or referencing missing test modules. Update the canonical prompt/workflow/docs that currently instruct agents to write back into repo-local `.openteam`.

**Tech Stack:** Bash, Python 3.11+/3.12, pytest/unittest, GitHub Actions YAML

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `scripts/_common.sh` | Modify | Add runtime-root helpers for self-scope artifact directories |
| `scripts/tasks/new.sh` | Modify | Write self task ledgers/logs under runtime state instead of repo-local `.openteam` |
| `scripts/skills/boot.sh` | Modify | Write self knowledge/memory artifacts under runtime state instead of repo-local `.openteam` |
| `tests/test_self_artifact_paths.py` | Create | Regression-test `new.sh` and `boot.sh` output paths |
| `README.md` | Modify | Update user-facing repo/runtime boundary guidance |
| `OPENTEAM.md` | Modify | Update canonical self-scope artifact paths |
| `docs/product/GOVERNANCE.md` | Modify | Align governance wording with runtime-root self artifacts |
| `docs/runbooks/EXECUTION_RUNBOOK.md` | Modify | Align runbook examples with runtime-root self artifacts |
| `specs/workflows/trunk.yaml` | Modify | Update canonical workflow artifact paths |
| `specs/prompts/NEW_TASK.md` | Modify | Update new-task instructions to runtime paths |
| `.github/workflows/ci.yml` | Modify | Remove `|| true`, install deterministic test deps, keep ignored crewAI tests explicit |
| `.github/workflows/runtime-ci.yml` | Modify | Replace stale missing test module reference |
| `tests/test_ci_workflows.py` | Create | Guard against failure-swallowing CI and stale test module references |

---

### Task 1: Move Self Task And Skill Artifacts To Runtime State

**Files:**
- Modify: `scripts/_common.sh`
- Modify: `scripts/tasks/new.sh`
- Modify: `scripts/skills/boot.sh`
- Create: `tests/test_self_artifact_paths.py`

- [ ] **Step 1: Write the failing regression tests**

```python
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_script(rel_path: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / rel_path), *args],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class SelfArtifactPathTests(unittest.TestCase):
    def test_new_task_writes_under_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime-root"
            env = os.environ.copy()
            env["HOME"] = td
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)
            out = _run_script("scripts/tasks/new.sh", "Boundary convergence task", env=env)
            self.assertEqual(out.returncode, 0, msg=out.stderr)

            kv = dict(
                line.split("=", 1)
                for line in out.stdout.splitlines()
                if "=" in line
            )
            ledger = Path(kv["ledger"])
            logs_dir = Path(kv["logs_dir"])

            self.assertTrue(ledger.exists(), msg=out.stdout)
            self.assertTrue(logs_dir.exists(), msg=out.stdout)
            self.assertTrue(str(ledger).startswith(str(runtime_root / "state" / "ledger" / "tasks")))
            self.assertTrue(str(logs_dir).startswith(str(runtime_root / "state" / "logs" / "tasks")))
            self.assertNotIn("/.openteam/", str(ledger))

    def test_skill_boot_writes_under_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime-root"
            env = os.environ.copy()
            env["HOME"] = td
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)
            out = _run_script("scripts/skills/boot.sh", "Researcher", "Runtime Paths", env=env)
            self.assertEqual(out.returncode, 0, msg=out.stderr)

            kv = dict(
                line.split("=", 1)
                for line in out.stdout.splitlines()
                if "=" in line
            )
            src_path = Path(kv["created_source_summary"])
            skill_path = Path(kv["created_skill_card"])
            mem_index = Path(kv["updated_memory_index"])

            self.assertTrue(src_path.exists(), msg=out.stdout)
            self.assertTrue(skill_path.exists(), msg=out.stdout)
            self.assertTrue(mem_index.exists(), msg=out.stdout)
            self.assertTrue(str(src_path).startswith(str(runtime_root / "state" / "openteam" / "kb" / "sources")))
            self.assertTrue(str(skill_path).startswith(str(runtime_root / "state" / "openteam" / "kb" / "roles")))
            self.assertTrue(str(mem_index).startswith(str(runtime_root / "state" / "openteam" / "memory" / "roles")))
            self.assertNotIn("/.openteam/", str(skill_path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the regression tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_self_artifact_paths.py -v`
Expected: FAIL because `new.sh` and `boot.sh` still write into repo-local `.openteam/...`.

- [ ] **Step 3: Add runtime-root helpers to `scripts/_common.sh`**

```bash
openteam_runtime_root() {
  if [[ -n "${OPENTEAM_RUNTIME_ROOT:-}" ]]; then
    printf '%s\n' "$OPENTEAM_RUNTIME_ROOT"
  else
    printf '%s/runtime/default\n' "$(openteam_home_dir)"
  fi
}

openteam_runtime_state_dir() {
  printf '%s/state\n' "$(openteam_runtime_root)"
}

openteam_self_ledger_tasks_dir() {
  printf '%s/ledger/tasks\n' "$(openteam_runtime_state_dir)"
}

openteam_self_logs_tasks_dir() {
  printf '%s/logs/tasks\n' "$(openteam_runtime_state_dir)"
}

openteam_self_kb_root() {
  printf '%s/openteam/kb\n' "$(openteam_runtime_state_dir)"
}

openteam_self_memory_root() {
  printf '%s/openteam/memory\n' "$(openteam_runtime_state_dir)"
}
```

- [ ] **Step 4: Point `scripts/tasks/new.sh` at the new helpers**

Replace the repo-local directory setup:

```bash
ledger_dir="$(openteam_self_ledger_tasks_dir)"
logs_root="$(openteam_self_logs_tasks_dir)"
ensure_dir "$ledger_dir"
ensure_dir "$logs_root"
```

and update the later path assignments:

```bash
if [[ ! -e "$ledger_dir/$cand.yaml" ]]; then
```

```bash
logs_dir="$logs_root/$task_id"
ledger_out="$ledger_dir/$task_id.yaml"
```

- [ ] **Step 5: Point `scripts/skills/boot.sh` at runtime state**

Replace the repo-local KB/memory paths:

```bash
kb_root="$(openteam_self_kb_root)"
memory_root="$(openteam_self_memory_root)"

ensure_dir "$kb_root/sources"
ensure_dir "$kb_root/roles/$ROLE/skill_cards"
ensure_dir "$memory_root/roles/$ROLE"

src_path="$kb_root/sources/$(date +%Y%m%d)_${slug}.md"
skill_path="$kb_root/roles/$ROLE/skill_cards/$(date +%Y%m%d)_${slug}.md"
mem_index="$memory_root/roles/$ROLE/index.md"
```

- [ ] **Step 6: Run the regression tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_self_artifact_paths.py -v`
Expected: 2 PASSED

- [ ] **Step 7: Run the related smoke tests**

Run: `.venv/bin/python -m pytest tests/test_bootstrap_and_run.py tests/test_openteam_root_detection.py -v`
Expected: PASS

---

### Task 2: Align Canonical Prompts, Workflow Specs, And Docs

**Files:**
- Modify: `README.md`
- Modify: `OPENTEAM.md`
- Modify: `docs/product/GOVERNANCE.md`
- Modify: `docs/runbooks/EXECUTION_RUNBOOK.md`
- Modify: `specs/workflows/trunk.yaml`
- Modify: `specs/prompts/NEW_TASK.md`

- [ ] **Step 1: Update the canonical artifact path wording**

Make these path replacements:

```text
.openteam/ledger/tasks/<TASK_ID>.yaml
-> ~/.openteam/runtime/default/state/ledger/tasks/<TASK_ID>.yaml

.openteam/logs/tasks/<TASK_ID>/**
-> ~/.openteam/runtime/default/state/logs/tasks/<TASK_ID>/**

.openteam/kb/sources/
-> ~/.openteam/runtime/default/state/openteam/kb/sources/

.openteam/memory/roles/<Role>/index.md
-> ~/.openteam/runtime/default/state/openteam/memory/roles/<Role>/index.md
```

- [ ] **Step 2: Update `specs/workflows/trunk.yaml`**

Change the required artifacts to runtime-state paths, for example:

```yaml
required_artifacts:
  - "~/.openteam/runtime/default/state/ledger/tasks/<TASK_ID>.yaml"
  - "~/.openteam/runtime/default/state/logs/tasks/<TASK_ID>/00_intake.md"
```

Repeat the same replacement for `01_plan.md` through `07_retro.md`.

- [ ] **Step 3: Update `specs/prompts/NEW_TASK.md`**

Replace the task instructions so the generated evidence paths point at runtime state:

```text
填写 `~/.openteam/runtime/default/state/logs/tasks/<TASK_ID>/00_intake.md`
...
结束时补齐 `~/.openteam/runtime/default/state/logs/tasks/<TASK_ID>/07_retro.md`
```

- [ ] **Step 4: Update README + OPENTEAM + governance/runbook text**

Make the narrative consistent with the implemented layout:

```text
scope=openteam 的 task/log/knowledge artifacts 不再写入 repo-local `.openteam/`，
而是写入 runtime state root；repo 仅保留 static specs/docs/templates/code。
```

- [ ] **Step 5: Run a focused verification grep**

Run: `rg -n '\.openteam/(ledger/tasks|logs/tasks|kb/sources|memory/roles)' README.md OPENTEAM.md docs/product/GOVERNANCE.md docs/runbooks/EXECUTION_RUNBOOK.md specs/workflows/trunk.yaml specs/prompts/NEW_TASK.md`
Expected: no matches

---

### Task 3: Make CI Fail On Real Problems

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/runtime-ci.yml`
- Create: `tests/test_ci_workflows.py`

- [ ] **Step 1: Write the failing workflow guard tests**

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CiWorkflowTests(unittest.TestCase):
    def test_main_ci_does_not_swallow_failures(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("|| true", text)

    def test_runtime_ci_references_existing_runtime_auto_update_test(self) -> None:
        text = (ROOT / ".github" / "workflows" / "runtime-ci.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_runtime_auto_update", text)
        self.assertNotIn("tests.test_crewai_self_upgrade", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the workflow guard tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ci_workflows.py -v`
Expected: FAIL because `ci.yml` still contains `|| true` and `runtime-ci.yml` still references `tests.test_crewai_self_upgrade`.

- [ ] **Step 3: Tighten `.github/workflows/ci.yml`**

Replace the install/lint/test commands with deterministic failure behavior:

```yaml
- name: Install dependencies
  run: |
    python -m pip install --upgrade pip
    python -m pip install pytest ruff fastapi "uvicorn[standard]" openai-agents PyYAML "psycopg[binary]" litellm redis pydantic

- name: Lint with ruff
  run: ruff check --select E,F,W . --exclude __pycache__ --exclude pytest-of-*

- name: Run tests
  env:
    OPENTEAM_RUNTIME_WORKFLOW_LOOPS_ENABLED: "0"
  run: |
    python -m pytest tests/ evals/ -v --tb=short \
      --ignore=tests/test_crewai_orchestrator.py \
      --ignore=tests/test_crewai_repo_improvement.py \
      --ignore=tests/test_crewai_repo_improvement_delivery.py \
      --ignore=tests/test_crewai_role_registry.py \
      --ignore=tests/test_crewai_runtime.py \
      --ignore=tests/test_crewai_task_registry.py \
      --ignore=tests/test_crewai_team_registry.py \
      --ignore=tests/test_crewai_workflow_registry.py \
      --ignore=tests/test_codex_llm.py
```

- [ ] **Step 4: Fix `.github/workflows/runtime-ci.yml`**

Replace the stale test module:

```yaml
python -m unittest \
  tests.test_crewai_runtime \
  tests.test_crewai_orchestrator \
  tests.test_runtime_auto_update \
  tests.test_panel_github_sync \
  tests.test_improvement_store \
  tests.test_openclaw_reporter \
  tests.test_bootstrap_and_run
```

- [ ] **Step 5: Run the workflow guard tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ci_workflows.py -v`
Expected: 2 PASSED

- [ ] **Step 6: Run the full local verification set for this change**

Run: `.venv/bin/python -m pytest tests/test_self_artifact_paths.py tests/test_ci_workflows.py tests/test_bootstrap_and_run.py tests/test_openteam_root_detection.py evals/test_repo_purity.py -v`
Expected: PASS

