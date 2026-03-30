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

    def test_status_snapshot_uses_control_plane_default_team_payload(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            runtime_root.mkdir(parents=True, exist_ok=True)

            team_payload = {
                "team_id": "repo-improvement",
                "display_name_zh": "仓库改进",
                "last_run": {"ts": "2026-03-30T00:00:00Z", "status": "DONE"},
                "proposal_counts": {"total": 2, "pending": 1},
                "coding": {"summary": {"active": 0}},
            }
            status_payload = {"default_team_id": "repo-improvement", "teams": {"repo-improvement": team_payload}}

            with mock.patch.object(self.mod, "_read_pid", return_value=4321), mock.patch.object(
                self.mod, "_pid_alive", return_value=True
            ), mock.patch.object(self.mod, "_llm_config", return_value={"ok": True, "model": "openai/codex"}), mock.patch.object(
                self.mod, "_http_json", side_effect=[
                    {"status": "ok"},
                    status_payload,
                ],
            ), mock.patch.object(self.mod, "_read_default_team_state", side_effect=AssertionError("helper should not be used")):
                out = self.mod._status_snapshot(repo, runtime_root, workspace_root, "http://127.0.0.1:8787")

            self.assertEqual(out["control_plane"]["status"], status_payload)
            self.assertEqual(out["default_team"]["team_id"], "repo-improvement")
            self.assertEqual(out["default_team"]["display_name_zh"], "仓库改进")
            self.assertEqual(out["default_team"]["proposal_counts"], {"total": 2, "pending": 1})
            self.assertEqual(out["default_team"]["last_run"], {"ts": "2026-03-30T00:00:00Z", "status": "DONE"})
            self.assertEqual(out["default_team"]["state_backend"], "control_plane_status")

    def test_stop_flow_is_single_node_and_skips_hub_down(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            runtime_root.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(self.mod, "_stop_pid", return_value={"ok": True, "stopped": True, "pid": 4321}), mock.patch.object(
                self.mod, "_run_json", side_effect=AssertionError("hub_down should not be called")
            ):
                out = self.mod._stop_flow(repo, runtime_root, workspace_root, keep_hub=False)

            self.assertNotIn("hub", out)
            self.assertEqual(out["default_team"], {"ok": True, "mode": "single_node"})
            self.assertEqual(out["control_plane"]["stopped"], True)


if __name__ == "__main__":
    unittest.main()
