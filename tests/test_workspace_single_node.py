import argparse
import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import openteam_cli._shared as cli_shared
import openteam_cli.workspace as cli_workspace


def _load_workspace_doctor():
    repo_root = Path(__file__).resolve().parents[1]
    pipelines_dir = repo_root / "scripts" / "pipelines"
    sys.path.insert(0, str(pipelines_dir))
    try:
        script = pipelines_dir / "workspace_doctor.py"
        loader = importlib.machinery.SourceFileLoader("workspace_doctor_test_module", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


class WorkspaceSingleNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace_doctor = _load_workspace_doctor()

    def test_project_scaffold_does_not_create_cluster_state(self):
        with tempfile.TemporaryDirectory() as td:
            workspace_root = Path(td) / "workspace"

            cli_shared._ensure_project_scaffold(workspace_root, "demo")

            self.assertTrue((workspace_root / "projects" / "demo" / "state" / "kb").exists())
            self.assertFalse((workspace_root / "projects" / "demo" / "state" / "cluster").exists())

    def test_workspace_doctor_accepts_project_without_cluster_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            workspace_root = Path(td) / "workspace"
            repo_root.mkdir(parents=True, exist_ok=True)
            cli_shared._ensure_workspace_scaffold(workspace_root)
            cli_shared._ensure_project_scaffold(workspace_root, "demo")

            out = self.workspace_doctor.check_workspace(repo_root=repo_root, workspace_root=workspace_root)

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(out.get("workspace_root"), str(workspace_root))

    def test_workspace_cli_doctor_accepts_project_without_cluster_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            workspace_root = Path(td) / "workspace"
            repo_root.mkdir(parents=True, exist_ok=True)
            cli_shared._ensure_workspace_scaffold(workspace_root)
            cli_shared._ensure_project_scaffold(workspace_root, "demo")
            args = argparse.Namespace(workspace_root=str(workspace_root))

            with unittest.mock.patch.object(cli_workspace, "_find_openteam_repo_root", return_value=repo_root):
                cli_workspace.cmd_workspace_doctor(args)


if __name__ == "__main__":
    unittest.main()
