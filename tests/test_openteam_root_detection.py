import argparse
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_template_app_to_syspath() -> None:
    app_dir = _repo_root() / "scaffolds" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


def _load_common_module():
    script = _repo_root() / "scripts" / "pipelines" / "_common.py"
    spec = importlib.util.spec_from_file_location("pipeline_common", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_add_template_app_to_syspath()

from app import state_store  # noqa: E402
from app import workspace_store  # noqa: E402


class OpenTeamRootDetectionTests(unittest.TestCase):
    def test_current_repo_is_recognized_with_repo_agents_md(self):
        repo = _repo_root()
        common = _load_common_module()

        self.assertTrue((repo / "AGENTS.md").exists())
        self.assertTrue(common._looks_like_openteam_repo(repo))
        resolved = common.resolve_repo_root(argparse.Namespace(repo_root=str(repo)))

        self.assertEqual(resolved, repo)

    def test_runtime_helpers_find_current_repo_with_repo_agents_md(self):
        repo = _repo_root()

        with mock.patch.dict(os.environ, {"OPENTEAM_REPO_PATH": ""}, clear=False):
            self.assertEqual(state_store.openteam_root(), repo)
            self.assertEqual(workspace_store.openteam_root(), repo)


if __name__ == "__main__":
    unittest.main()
