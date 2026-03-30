import argparse
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def _load_pipeline_common():
    repo_root = Path(__file__).resolve().parents[1]
    pipelines_dir = repo_root / "scripts" / "pipelines"
    sys.path.insert(0, str(pipelines_dir))
    try:
        script = pipelines_dir / "_common.py"
        loader = importlib.machinery.SourceFileLoader("pipeline_common_test_module", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


def _load_runtime_workspace_store():
    repo_root = Path(__file__).resolve().parents[1]
    app_dir = repo_root / "scaffolds" / "runtime" / "orchestrator"
    sys.path.insert(0, str(app_dir))
    try:
        from app import workspace_store

        return workspace_store
    finally:
        sys.path.pop(0)


class WorkspaceSingleNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace_doctor = _load_workspace_doctor()
        cls.pipeline_common = _load_pipeline_common()
        cls.runtime_workspace_store = _load_runtime_workspace_store()

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

            with mock.patch.object(cli_workspace, "_find_openteam_repo_root", return_value=repo_root):
                cli_workspace.cmd_workspace_doctor(args)

    def test_pipeline_workspace_root_defaults_to_openteam_home_workspace(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_HOME": str(Path(td) / "openteam-home")},
            clear=True,
        ):
            workspace_root = self.pipeline_common.workspace_root()

        self.assertEqual(workspace_root, Path(td) / "openteam-home" / "workspace")

    def test_pipeline_workspace_root_falls_back_to_runtime_root_workspace(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_RUNTIME_ROOT": str(Path(td) / "runtime-alt")},
            clear=True,
        ):
            workspace_root = self.pipeline_common.workspace_root()

        self.assertEqual(workspace_root, Path(td) / "runtime-alt" / "workspace")

    def test_cli_workspace_default_uses_openteam_home_workspace(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_HOME": str(Path(td) / "openteam-home")},
            clear=True,
        ):
            workspace_root = cli_shared._workspace_root_from_cfg({})

        self.assertEqual(workspace_root, Path(td) / "openteam-home" / "workspace")

    def test_runtime_project_scaffold_matches_workspace_doctor_requirements(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_WORKSPACE_ROOT": str(Path(td) / "workspace")},
            clear=False,
        ):
            meta = self.runtime_workspace_store.ensure_project_scaffold("demo")
            project_dir = Path(meta["project_dir"])

            self.assertTrue((project_dir / "state" / "requirements" / "requirements.yaml").exists())
            self.assertTrue((project_dir / "state" / "requirements" / "REQUIREMENTS.md").exists())
            self.assertTrue((project_dir / "state" / "requirements" / "CHANGELOG.md").exists())
            self.assertFalse((project_dir / "state" / "cluster").exists())

    def test_runtime_project_scaffold_passes_workspace_doctor(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_WORKSPACE_ROOT": str(Path(td) / "workspace")},
            clear=False,
        ):
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            self.runtime_workspace_store.ensure_project_scaffold("demo")

            out = self.workspace_doctor.check_workspace(
                repo_root=repo_root,
                workspace_root=Path(td) / "workspace",
            )

            self.assertTrue(bool(out.get("ok")), out)

    def test_runtime_workspace_default_uses_openteam_home_workspace(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_HOME": str(Path(td) / "openteam-home")},
            clear=True,
        ):
            workspace_root = self.runtime_workspace_store.workspace_root()

        self.assertEqual(workspace_root, Path(td) / "openteam-home" / "workspace")

    def test_runtime_workspace_root_falls_back_to_runtime_root_workspace(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_RUNTIME_ROOT": str(Path(td) / "runtime-alt")},
            clear=True,
        ):
            workspace_root = self.runtime_workspace_store.workspace_root()

        self.assertEqual(workspace_root, Path(td) / "runtime-alt" / "workspace")

    def test_cli_config_path_follows_openteam_home(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_HOME": str(Path(td) / "openteam-home")},
            clear=True,
        ):
            config_path = cli_shared._config_path()

        self.assertEqual(config_path, Path(td) / "openteam-home" / "config.toml")


if __name__ == "__main__":
    unittest.main()
