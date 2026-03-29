from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_repo_understanding_gate():
    pipelines_dir = ROOT / "scripts" / "pipelines"
    sys.path.insert(0, str(pipelines_dir))
    try:
        script = pipelines_dir / "repo_understanding_gate.py"
        loader = importlib.machinery.SourceFileLoader("repo_understanding_gate_test_module", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


class CiWorkflowTests(unittest.TestCase):
    def test_main_ci_does_not_swallow_failures(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("|| true", text)

    def test_ci_workflow_runs_delivery_coverage_gate(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("check_delivery_coverage.py", workflow)
        self.assertIn("coverage run -m pytest", workflow)
        self.assertNotIn("tests/test_cockpit_state.py", workflow)

    def test_runtime_ci_runs_delivery_studio_suite(self) -> None:
        text = (ROOT / ".github" / "workflows" / "runtime-ci.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_delivery_studio_runtime", text)
        self.assertIn("tests.test_delivery_studio_panel_projection", text)
        self.assertIn("tests.test_delivery_studio_review_gate", text)
        self.assertNotIn("tests.test_crewai_self_upgrade", text)

    def test_repo_understanding_gate_uses_runtime_state_for_openteam_task_artifacts(self) -> None:
        mod = _load_repo_understanding_gate()
        overview = mod._arch_overview()

        self.assertIn("~/.openteam/runtime/default/state/ledger", overview)
        self.assertIn("~/.openteam/runtime/default/state/logs", overview)
        self.assertNotIn("`.openteam/ledger`", overview)
        self.assertNotIn("`.openteam/logs`", overview)

    def test_repo_improvement_prompt_uses_runtime_state_paths(self) -> None:
        text = (ROOT / "specs" / "prompts" / "REPO_IMPROVEMENT.md").read_text(encoding="utf-8")

        self.assertIn("~/.openteam/runtime/default/state/logs/tasks/<TASK_ID>/07_retro.md", text)
        self.assertIn("~/.openteam/runtime/default/state/ledger/openteam_issues_pending/", text)
        self.assertNotIn("`.openteam/logs/tasks/<TASK_ID>/07_retro.md`", text)
        self.assertNotIn("`.openteam/ledger/openteam_issues_pending/`", text)


if __name__ == "__main__":
    unittest.main()
