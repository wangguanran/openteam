# Pre-Run Bugfixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the known pre-run regressions that still block or mislead a local OpenTeam startup: `doctor` must accept Codex OAuth, and self-scope task/issue/repo-improvement flows must stop pointing at repo-local `.openteam`.

**Architecture:** Keep the fixes narrow. Reuse the existing runtime-state helpers as the single source of truth for self-scope paths, mirror the already-tested bootstrap auth contract inside `doctor`, and add focused regression tests around the exact behavior we are changing.

**Tech Stack:** Python 3, shell scripts, `unittest`, focused `ruff` checks

---

### Task 1: Lock the auth contract with a failing doctor test

**Files:**
- Create: `tests/test_doctor.py`
- Modify: `scripts/pipelines/doctor.py`

- [ ] Add a unit test that patches `_codex_status()` to return logged-in and verifies `_llm_config_check()` succeeds with `OPENTEAM_LLM_MODEL=openai-codex/gpt-5.4` and no API key.
- [ ] Run `python3 -m unittest -q tests.test_doctor` and confirm it fails before the implementation change.
- [ ] Update `scripts/pipelines/doctor.py` so its readiness logic matches `scripts/bootstrap_and_run.py`.
- [ ] Re-run `python3 -m unittest -q tests.test_doctor` and confirm it passes.

### Task 2: Lock runtime-state shell paths with failing tests

**Files:**
- Modify: `tests/test_self_artifact_paths.py`
- Modify: `scripts/_common.sh`
- Modify: `scripts/tasks/retro.sh`
- Modify: `scripts/issues/open.sh`

- [ ] Add a regression test proving `retro.sh` reads/writes `07_retro.md` under `OPENTEAM_RUNTIME_ROOT/state/logs/tasks/<TASK_ID>/`.
- [ ] Add a regression test proving `issues/open.sh` fallback writes pending issue drafts under runtime state, not repo-local `.openteam/`.
- [ ] Run the targeted tests and confirm they fail before changing the scripts.
- [ ] Add any missing shared helper in `scripts/_common.sh` and switch both shell scripts to the runtime-state path helpers.
- [ ] Re-run the targeted tests and confirm they pass.

### Task 3: Lock repo-improvement boundary text with failing tests

**Files:**
- Modify: `tests/test_ci_workflows.py`
- Modify: `scripts/pipelines/repo_understanding_gate.py`
- Modify: `specs/prompts/REPO_IMPROVEMENT.md`

- [ ] Add a regression test that asserts `repo_understanding_gate._arch_overview()` describes scope=`openteam` task ledger/logs in runtime state rather than repo-local `.openteam`.
- [ ] Add a regression test that asserts `specs/prompts/REPO_IMPROVEMENT.md` references runtime-state `07_retro.md` and runtime-state pending issue drafts.
- [ ] Run `python3 -m unittest -q tests.test_ci_workflows` and confirm the new assertions fail.
- [ ] Update the pipeline text and prompt text to the runtime-state contract.
- [ ] Re-run `python3 -m unittest -q tests.test_ci_workflows` and confirm it passes.

### Task 4: Focused verification

**Files:**
- Verify only

- [ ] Run `python3 -m unittest -q tests.test_doctor tests.test_self_artifact_paths tests.test_ci_workflows tests.test_bootstrap_and_run tests.test_openteam_root_detection evals.test_repo_purity`.
- [ ] Run `uvx ruff check scripts/pipelines/doctor.py scripts/pipelines/repo_understanding_gate.py scripts/_common.sh scripts/tasks/retro.sh scripts/issues/open.sh tests/test_doctor.py tests/test_self_artifact_paths.py tests/test_ci_workflows.py`.
- [ ] Summarize any remaining startup blockers separately from the fixes delivered here.
