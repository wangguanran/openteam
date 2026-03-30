import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "bootstrap_and_run.py"
    loader = importlib.machinery.SourceFileLoader("bootstrap_run_single_node_test_module", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class SingleNodeStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_status_snapshot_no_longer_reports_hub(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            runtime_root.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(self.mod, "_pid_alive", return_value=False), mock.patch.object(
                self.mod, "_llm_config", return_value={"ok": True, "model": "openai/codex"}
            ), mock.patch.object(self.mod, "_read_default_team_state", return_value={}):
                out = self.mod._status_snapshot(repo, runtime_root, workspace_root, "http://127.0.0.1:8787")

            self.assertNotIn("hub", out)
            self.assertEqual(out["control_plane"]["running"], False)


if __name__ == "__main__":
    unittest.main()
