import importlib.machinery
import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    pipelines_dir = repo_root / "scripts" / "pipelines"
    sys.path.insert(0, str(pipelines_dir))
    try:
        script = pipelines_dir / "doctor.py"
        loader = importlib.machinery.SourceFileLoader("doctor_test_module", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


class DoctorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_llm_config_accepts_codex_oauth_without_api_key(self):
        with mock.patch.object(self.mod, "_codex_status", return_value=(True, "Logged in using ChatGPT")), mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_MODEL": "openai-codex/gpt-5.4"},
            clear=True,
        ):
            cfg = self.mod._llm_config_check()

        self.assertTrue(bool(cfg.get("ok")))
        self.assertTrue(bool(cfg.get("codex_oauth_ready")))
        self.assertEqual(cfg.get("auth_strategy"), "codex_oauth")
        self.assertEqual(cfg.get("model"), "openai-codex/gpt-5.4")

    def test_main_accepts_single_node_openapi_without_deleted_cluster_routes(self):
        repo_root = Path(__file__).resolve().parents[1]
        workspace_root = repo_root / ".tmp-doctor-workspace"
        status_payload = {
            "instance_id": "local-1",
            "default_team_id": "repo-improvement",
            "teams": {"repo-improvement": {"last_run": {}}},
            "task_run_sync": {"ok": True},
        }
        openapi_payload = {
            "paths": {
                "/v1/status": {},
                "/v1/agents": {},
                "/v1/runs": {},
                "/v1/runs/start": {},
                "/v1/tasks": {},
                "/v1/focus": {},
                "/v1/chat": {},
                "/v1/requirements": {},
                "/v1/panel/github/sync": {},
                "/v1/panel/github/health": {},
                "/v1/panel/github/config": {},
                "/v1/tasks/new": {},
                "/v1/recovery/scan": {},
                "/v1/recovery/resume": {},
                "/v1/teams": {},
            }
        }

        def fake_http(url: str, *, timeout_sec: int = 5):
            if url.endswith("/healthz"):
                return {"status": "ok"}
            if url.endswith("/v1/status"):
                return status_payload
            if url.endswith("/openapi.json"):
                return openapi_payload
            raise AssertionError(f"unexpected url: {url}")

        stdout = io.StringIO()
        with (
            mock.patch.object(self.mod, "resolve_repo_root", return_value=repo_root),
            mock.patch.object(self.mod, "resolve_workspace_root", return_value=workspace_root),
            mock.patch.object(self.mod, "check_workspace", return_value={"ok": True}),
            mock.patch.object(self.mod, "_codex_status", return_value=(True, "ok")),
            mock.patch.object(self.mod, "_gh_status", return_value=(True, "ok")),
            mock.patch.object(self.mod, "_db_check", return_value={"ok": True, "status": "OK"}),
            mock.patch.object(self.mod, "_default_team_check", return_value={"ok": True, "last_run": {}}),
            mock.patch.object(self.mod, "_llm_config_check", return_value={"ok": True}),
            mock.patch.object(self.mod, "_http_json", side_effect=fake_http),
            mock.patch("sys.stdout", stdout),
        ):
            rc = self.mod.main(["--json"])

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn('"ok": true', output)
        self.assertNotIn("/v1/cluster/status", output)
        self.assertNotIn("/v1/hub/status", output)
        self.assertNotIn("/v1/nodes", output)


if __name__ == "__main__":
    unittest.main()
