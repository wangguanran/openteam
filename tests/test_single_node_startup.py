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
                "team_id": "delivery-studio",
                "display_name_zh": "交付工作室",
                "last_run": {"ts": "2026-03-30T00:00:00Z", "status": "READY"},
                "proposal_counts": {"total": 0, "pending": 0},
                "coding": {"summary": {"active": 0}},
            }
            status_payload = {"default_team_id": "delivery-studio", "teams": {"delivery-studio": team_payload}}

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
            self.assertEqual(out["default_team"]["team_id"], "delivery-studio")
            self.assertEqual(out["default_team"]["display_name_zh"], "交付工作室")
            self.assertEqual(out["default_team"]["proposal_counts"], {"total": 0, "pending": 0})
            self.assertEqual(out["default_team"]["last_run"], {"ts": "2026-03-30T00:00:00Z", "status": "READY"})
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
                out = self.mod._stop_flow(repo, runtime_root, workspace_root)

            self.assertNotIn("hub", out)
            self.assertEqual(out["default_team"], {"ok": True, "mode": "single_node"})
            self.assertEqual(out["control_plane"]["stopped"], True)

    def test_start_control_plane_omits_hub_db_and_redis_env(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = runtime_root / "workspace"
            orch_dir = repo / "scaffolds" / "runtime" / "orchestrator"
            orch_dir.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}

            class _FakeProcess:
                pid = 9876

            def fake_popen(cmd, cwd=None, stdout=None, stderr=None, env=None, start_new_session=None):
                captured["cmd"] = cmd
                captured["cwd"] = cwd
                captured["env"] = dict(env or {})
                captured["start_new_session"] = start_new_session
                if stdout is not None:
                    stdout.close()
                if stderr is not None and stderr is not stdout:
                    stderr.close()
                return _FakeProcess()

            with mock.patch.object(self.mod.subprocess, "Popen", side_effect=fake_popen), mock.patch.object(
                self.mod, "_wait_http_ready", return_value={"ok": True}
            ):
                out = self.mod._start_control_plane(
                    repo,
                    runtime_root,
                    workspace_root,
                    base_url="http://127.0.0.1:8787",
                    port=8787,
                    python_exec="/usr/bin/python3",
                )

            env = captured["env"]
            assert isinstance(env, dict)
            self.assertEqual(out["pid"], 9876)
            self.assertNotIn("OPENTEAM_DB_URL", env)
            self.assertNotIn("OPENTEAM_REDIS_URL", env)

    def test_start_control_plane_strips_legacy_db_and_redis_env_from_host_process(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = runtime_root / "workspace"
            orch_dir = repo / "scaffolds" / "runtime" / "orchestrator"
            orch_dir.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}

            class _FakeProcess:
                pid = 9876

            def fake_popen(cmd, cwd=None, stdout=None, stderr=None, env=None, start_new_session=None):
                _ = cmd, cwd, start_new_session
                captured["env"] = dict(env or {})
                if stdout is not None:
                    stdout.close()
                if stderr is not None and stderr is not stdout:
                    stderr.close()
                return _FakeProcess()

            with mock.patch.dict(
                self.mod.os.environ,
                {
                    "OPENTEAM_DB_URL": "postgresql://example/db",
                    "OPENTEAM_REDIS_URL": "redis://example/0",
                },
                clear=False,
            ), mock.patch.object(self.mod.subprocess, "Popen", side_effect=fake_popen), mock.patch.object(
                self.mod, "_wait_http_ready", return_value={"ok": True}
            ):
                self.mod._start_control_plane(
                    repo,
                    runtime_root,
                    workspace_root,
                    base_url="http://127.0.0.1:8787",
                    port=8787,
                    python_exec="/usr/bin/python3",
                )

            env = captured["env"]
            assert isinstance(env, dict)
            self.assertNotIn("OPENTEAM_DB_URL", env)
            self.assertNotIn("OPENTEAM_REDIS_URL", env)

    def test_main_rejects_keep_hub_flag(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            stderr = []

            def fake_eprint(*args, **kwargs):
                _ = kwargs
                stderr.append(" ".join(str(arg) for arg in args))

            with mock.patch.object(self.mod, "_repo_root", return_value=repo), mock.patch.object(
                self.mod, "_runtime_root", return_value=Path(td) / "runtime"
            ), mock.patch.object(self.mod, "_workspace_root", return_value=Path(td) / "runtime" / "workspace"), mock.patch.object(
                self.mod, "_ensure_runtime_layout", return_value=None
            ), mock.patch("sys.stderr.write", side_effect=lambda text: stderr.append(text) or len(text)):
                with self.assertRaises(SystemExit) as ctx:
                    self.mod.main(["stop", "--keep-hub"])

            self.assertEqual(ctx.exception.code, 2)
            self.assertTrue(any("--keep-hub" in part for part in stderr))


if __name__ == "__main__":
    unittest.main()
