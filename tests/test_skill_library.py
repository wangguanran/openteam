import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app.skill_library import executor as skill_executor  # noqa: E402
from app.skill_library import registry as skill_registry  # noqa: E402


class SkillLibraryTests(unittest.TestCase):
    def test_skill_specs_are_loaded_from_skill_library(self) -> None:
        ids = {spec.skill_id for spec in skill_registry.list_skill_specs()}
        self.assertIn("repo.collect-context", ids)
        self.assertIn("planning.materialize-findings", ids)
        self.assertIn("github.claim-issue-discussion", ids)
        self.assertIn("delivery.run-task-pipeline", ids)

    def test_execute_skill_uses_registered_handler(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            context = SimpleNamespace(
                project_id="teamos",
                target_id="demo-target",
                force=False,
                workflow=SimpleNamespace(lane="feature", max_candidates=lambda: 5),
                extra={},
            )
            target = {"target_id": "demo-target", "project_id": "teamos", "repo_root": str(repo), "repo_locator": "foo/bar"}
            repo_context = {"repo_locator": "foo/bar", "current_version": "0.1.0"}
            with mock.patch("app.skill_library.repo_skills.proposal_runtime._resolve_target", return_value=target), mock.patch(
                "app.skill_library.repo_skills.proposal_runtime._prepare_discovery_repo",
                return_value=repo,
            ), mock.patch(
                "app.skill_library.repo_skills.proposal_runtime.collect_repo_context",
                return_value=repo_context,
            ), mock.patch(
                "app.skill_library.repo_skills.proposal_runtime._should_skip",
                return_value=(False, ""),
            ), mock.patch(
                "app.skill_library.repo_skills.proposal_runtime._prompt_safe_repo_context",
                return_value=repo_context,
            ):
                out = skill_executor.execute_skill("repo.collect-context", context=context, inputs={}, state={})

        self.assertTrue(out["ok"])
        self.assertEqual(out["outputs"]["repo_locator"], "foo/bar")
        self.assertEqual(out["outputs"]["target_id"], "demo-target")


if __name__ == "__main__":
    unittest.main()
