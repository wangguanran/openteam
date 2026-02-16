import os
import sys
import tempfile
import unittest
from pathlib import Path


def _add_template_app_to_syspath():
    # Source of truth for control-plane path policy.
    repo_root = Path(__file__).resolve().parents[1]
    app_dir = repo_root / ".team-os" / "templates" / "runtime" / "orchestrator"
    sys.path.insert(0, str(app_dir))


_add_template_app_to_syspath()

from app import workspace_store  # noqa: E402
from app.requirements_store import ensure_scaffold  # noqa: E402


class WorkspacePathsEvals(unittest.TestCase):
    def test_project_scaffold_lands_in_workspace_not_repo(self):
        with tempfile.TemporaryDirectory() as td_repo, tempfile.TemporaryDirectory() as td_ws:
            repo_root = Path(td_repo).resolve()
            ws_root = Path(td_ws).resolve()

            # Ensure "outside repo" enforcement is real.
            with self.assertRaises(workspace_store.WorkspaceError):
                workspace_store.assert_project_paths_outside_repo(team_os_root=repo_root, workspace_root_path=repo_root / "ws")

            # Normal case: workspace outside repo root.
            workspace_store.assert_project_paths_outside_repo(team_os_root=repo_root, workspace_root_path=ws_root)

            os.environ["TEAMOS_WORKSPACE_ROOT"] = str(ws_root)
            workspace_store.ensure_workspace_scaffold()

            meta = workspace_store.ensure_project_scaffold("demo")
            self.assertEqual(meta["project_id"], "demo")

            # Requirements scaffold should be in workspace state/requirements.
            req_dir = workspace_store.requirements_dir("demo")
            ensure_scaffold(req_dir, project_id="demo")
            self.assertTrue((req_dir / "requirements.yaml").exists())

            # Other required per-project dirs should exist.
            self.assertTrue(workspace_store.ledger_tasks_dir("demo").exists())
            self.assertTrue(workspace_store.logs_tasks_dir("demo").exists())
            self.assertTrue((workspace_store.prompts_dir("demo") / "MASTER_PROMPT.md").exists())
            self.assertTrue((workspace_store.plan_dir("demo") / "plan.yaml").exists())

            # Hard guarantee: all of the above must be outside the repo root.
            for p in [
                req_dir,
                workspace_store.ledger_tasks_dir("demo"),
                workspace_store.logs_tasks_dir("demo"),
                workspace_store.prompts_dir("demo"),
                workspace_store.plan_dir("demo"),
            ]:
                self.assertFalse(str(p).startswith(str(repo_root)), f"path leaked into repo: {p}")


if __name__ == "__main__":
    unittest.main()

