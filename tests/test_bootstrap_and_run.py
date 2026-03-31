import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest import mock


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "bootstrap_and_run.py"
    loader = importlib.machinery.SourceFileLoader("bootstrap_run_test_module", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class BootstrapAndRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_runtime_layout_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            rt = Path(td) / "openteam-runtime"
            self.mod._ensure_runtime_layout(rt)
            self.mod._ensure_runtime_layout(rt)
            self.assertTrue((rt / "state" / "audit").exists())
            self.assertFalse((rt / "workspace").exists())
            self.assertFalse((rt / "hub").exists())
            self.assertTrue((rt / "tmp").exists())
            self.assertTrue((rt / "cache").exists())

    def test_workspace_layout_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            workspace_root = Path(td) / "workspace"

            self.mod._ensure_workspace_layout(workspace_root)
            self.mod._ensure_workspace_layout(workspace_root)

            self.assertTrue((workspace_root / "projects").exists())
            self.assertTrue((workspace_root / "shared" / "cache").exists())
            self.assertTrue((workspace_root / "shared" / "tmp").exists())
            self.assertTrue((workspace_root / "config").exists())

    def test_workspace_root_defaults_to_openteam_home_workspace(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_HOME": str(Path(td) / "openteam-home")},
            clear=True,
        ):
            runtime_root = self.mod._runtime_root(Path(td) / "repo")

            workspace_root = self.mod._workspace_root(runtime_root)

        self.assertEqual(workspace_root, Path(td) / "openteam-home" / "workspace")

    def test_workspace_root_falls_back_to_runtime_root_workspace_when_only_runtime_root_is_set(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_RUNTIME_ROOT": str(Path(td) / "runtime-alt")},
            clear=True,
        ):
            runtime_root = self.mod._runtime_root(Path(td) / "repo")

            workspace_root = self.mod._workspace_root(runtime_root)

        self.assertEqual(workspace_root, Path(td) / "runtime-alt" / "workspace")

    def test_quarantine_legacy_openteam_dir(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "openteam-runtime"
            (repo / ".openteam" / "state").mkdir(parents=True, exist_ok=True)
            (repo / ".openteam" / "state" / "x.json").write_text("{}", encoding="utf-8")
            out = self.mod._quarantine_legacy_openteam_dir(repo, runtime_root)
            self.assertTrue(bool(out.get("ok")))
            self.assertTrue(bool(out.get("found")))
            self.assertFalse((repo / ".openteam").exists())
            self.assertTrue(Path(str(out.get("moved_to") or "")).exists())

    def test_quarantine_empty_legacy_openteam_dir_removes_it(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "openteam-runtime"
            (repo / ".openteam").mkdir(parents=True, exist_ok=True)

            out = self.mod._quarantine_legacy_openteam_dir(repo, runtime_root)

            self.assertTrue(bool(out.get("ok")))
            self.assertTrue(bool(out.get("found")))
            self.assertTrue(bool(out.get("removed_empty")))
            self.assertFalse((repo / ".openteam").exists())

    def test_llm_config_accepts_codex_oauth_without_api_key(self):
        with mock.patch.object(self.mod, "_codex_login_status", return_value=(True, "Logged in using ChatGPT")), mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_MODEL": "openai-codex/gpt-5.4"},
            clear=True,
        ):
            cfg = self.mod._llm_config()

        self.assertTrue(bool(cfg.get("ok")))
        self.assertTrue(bool(cfg.get("codex_oauth_ready")))
        self.assertEqual(cfg.get("auth_strategy"), "codex_oauth")
        self.assertEqual(cfg.get("model"), "openai-codex/gpt-5.4")

    def test_llm_config_accepts_litellm_proxy_without_api_key(self):
        with mock.patch.object(self.mod, "_codex_login_status", return_value=(False, "not logged in")), mock.patch.dict(
            os.environ,
            {
                "OPENTEAM_LLM_GATEWAY": "litellm_proxy",
                "OPENTEAM_LLM_MODEL": "anthropic/claude-sonnet-4",
            },
            clear=True,
        ):
            cfg = self.mod._llm_config()

        self.assertTrue(bool(cfg.get("ok")))
        self.assertEqual(cfg.get("auth_strategy"), "litellm_proxy")
        self.assertEqual(cfg.get("base_url"), "http://127.0.0.1:4000/v1")
        self.assertEqual(cfg.get("model"), "anthropic/claude-sonnet-4")

    def test_crewai_pip_spec_prefers_archive_url(self):
        with mock.patch.dict(
            os.environ,
            {"OPENTEAM_CREWAI_ARCHIVE_URL": "https://codeload.github.com/acme/crewAI/tar.gz/refs/heads/main"},
            clear=True,
        ):
            spec = self.mod._crewai_pip_spec()

        self.assertEqual(
            spec,
            "crewai @ https://codeload.github.com/acme/crewAI/tar.gz/refs/heads/main#subdirectory=lib/crewai",
        )

    def test_crewai_archive_url_falls_back_to_github_git_url(self):
        with mock.patch.dict(
            os.environ,
            {
                "OPENTEAM_CREWAI_GIT_URL": "https://github.com/example/crewAI.git",
                "OPENTEAM_CREWAI_GIT_REF": "main",
            },
            clear=True,
        ):
            archive_url = self.mod._crewai_archive_url()

        self.assertEqual(archive_url, "https://codeload.github.com/example/crewAI/tar.gz/refs/heads/main")

    def test_crewai_defaults_use_crewaiinc_repo(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            archive_url = self.mod._crewai_archive_url()

        self.assertEqual(archive_url, "https://codeload.github.com/crewAIInc/crewAI/tar.gz/refs/heads/main")

    def test_ensure_local_runtime_db_creates_bootstrap_probe_table(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "openteam-runtime"

            out = self.mod._ensure_local_runtime_db(runtime_root)

            db_path = Path(str(out.get("path") or ""))
            self.assertTrue(bool(out.get("ok")))
            self.assertTrue(db_path.exists())
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='bootstrap_probe'"
                ).fetchone()
            self.assertEqual(row, ("bootstrap_probe",))

    def test_missing_python_modules_only_checks_single_node_packages(self):
        missing = self.mod._missing_python_modules(Path(sys.executable))
        missing_names = {name for name, _ in missing}

        self.assertNotIn("redis", missing_names)
        self.assertNotIn("psycopg", missing_names)

    def test_doctor_uses_requested_control_plane_base_url(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            workspace_root = Path(td) / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            workspace_root.mkdir(parents=True, exist_ok=True)
            captured: dict[str, object] = {}

            def fake_run_json(cmd, **kwargs):
                captured["cmd"] = list(cmd)
                captured["kwargs"] = kwargs
                return {"ok": True}

            with mock.patch.object(self.mod, "_run_json", side_effect=fake_run_json):
                out = self.mod._doctor(repo, workspace_root, base_url="http://127.0.0.1:8878")

            self.assertTrue(bool(out.get("ok")))
            self.assertIn("--base-url", captured["cmd"])
            self.assertIn("http://127.0.0.1:8878", captured["cmd"])

    def test_write_litellm_config_collects_workflow_models(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            (repo / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "alpha" / "specs" / "workflows").mkdir(
                parents=True, exist_ok=True
            )
            (repo / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "alpha" / "specs" / "workflows" / "a.yaml").write_text(
                "\n".join(
                    [
                        "agents:",
                        "  - agent_id: a",
                        "    model: openai/gpt-5.4",
                        "  - agent_id: b",
                        "    model: anthropic/claude-opus-4.6",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"OPENTEAM_LLM_MODEL": "openai/gpt-5.4"}, clear=False):
                out = self.mod._write_litellm_config(repo, runtime_root)

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(
                out.get("models"),
                ["anthropic/claude-opus-4.6", "openai/gpt-5.4"],
            )
            config_path = Path(str(out.get("config_path") or ""))
            self.assertTrue(config_path.exists())
            text = config_path.read_text(encoding="utf-8")
            self.assertIn("model_name: openai/gpt-5.4", text)
            self.assertIn("model_name: anthropic/claude-opus-4.6", text)

    def test_start_litellm_proxy_drops_proxy_env_vars(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {
                "OPENTEAM_LLM_GATEWAY": "litellm_proxy",
                "OPENTEAM_LLM_MODEL": "openai/gpt-5.4",
                "ALL_PROXY": "socks5h://127.0.0.1:7893",
                "all_proxy": "socks5h://127.0.0.1:7893",
                "HTTP_PROXY": "http://127.0.0.1:7893",
                "HTTPS_PROXY": "http://127.0.0.1:7893",
                "http_proxy": "http://127.0.0.1:7893",
                "https_proxy": "http://127.0.0.1:7893",
            },
            clear=False,
        ):
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "alpha" / "specs" / "workflows").mkdir(
                parents=True, exist_ok=True
            )
            (repo / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "alpha" / "specs" / "workflows" / "a.yaml").write_text(
                "agents:\n  - agent_id: a\n    role_id: T\n    model: openai/gpt-5.4\n",
                encoding="utf-8",
            )
            captured_env: dict[str, str] = {}

            class _FakeProc:
                pid = 1234

            def fake_popen(cmd, **kwargs):
                _ = cmd
                captured_env.update(kwargs.get("env") or {})
                return _FakeProc()

            with mock.patch.object(self.mod.subprocess, "Popen", side_effect=fake_popen), mock.patch.object(
                self.mod, "_wait_litellm_ready", return_value={"ok": True}
            ):
                out = self.mod._start_litellm_proxy(repo, runtime_root, python_exec=sys.executable)

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(captured_env.get("ALL_PROXY", None), "")
            self.assertEqual(captured_env.get("all_proxy", None), "")
            self.assertEqual(captured_env.get("HTTP_PROXY", None), "")
            self.assertEqual(captured_env.get("HTTPS_PROXY", None), "")
            self.assertEqual(captured_env.get("http_proxy", None), "")
            self.assertEqual(captured_env.get("https_proxy", None), "")

    def test_start_litellm_proxy_uses_free_local_port_when_default_is_busy(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {
                "OPENTEAM_LLM_GATEWAY": "litellm_proxy",
                "OPENTEAM_LLM_MODEL": "openai/gpt-5.4",
            },
            clear=False,
        ):
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "alpha" / "specs" / "workflows").mkdir(
                parents=True, exist_ok=True
            )
            (repo / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "alpha" / "specs" / "workflows" / "a.yaml").write_text(
                "agents:\n  - agent_id: a\n    role_id: T\n    model: openai/gpt-5.4\n",
                encoding="utf-8",
            )
            captured_cmd: list[str] = []

            class _FakeProc:
                pid = 1234

            def fake_popen(cmd, **kwargs):
                _ = kwargs
                captured_cmd[:] = list(cmd)
                return _FakeProc()

            with mock.patch.object(self.mod, "_is_local_port_available", return_value=False), mock.patch.object(
                self.mod, "_find_free_local_port", return_value=4001
            ), mock.patch.object(self.mod.subprocess, "Popen", side_effect=fake_popen), mock.patch.object(
                self.mod, "_wait_litellm_ready", return_value={"ok": True}
            ):
                out = self.mod._start_litellm_proxy(repo, runtime_root, python_exec=sys.executable)

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(out.get("base_url"), "http://127.0.0.1:4001/v1")
            self.assertIn("--port", captured_cmd)
            self.assertIn("4001", captured_cmd)

    def test_litellm_proxy_status_prefers_runtime_saved_base_url_when_env_absent(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_GATEWAY": "litellm_proxy"},
            clear=False,
        ):
            runtime_root = Path(td) / "runtime"
            state_dir = runtime_root / "state" / "openteam"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "litellm_proxy.json").write_text(
                '{"base_url":"http://127.0.0.1:4001/v1"}\n',
                encoding="utf-8",
            )

            with mock.patch.object(self.mod, "_read_pid", return_value=4040), mock.patch.object(
                self.mod, "_pid_alive", return_value=True
            ), mock.patch.object(self.mod, "_wait_litellm_ready", return_value={"ok": True}):
                out = self.mod._litellm_proxy_status(runtime_root)

            self.assertEqual(out.get("base_url"), "http://127.0.0.1:4001/v1")
            self.assertEqual(out.get("pid"), 4040)

    def test_require_llm_config_prefers_runtime_saved_base_url_when_env_absent(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            self.mod, "_codex_login_status", return_value=(False, "not logged in")
        ), mock.patch.dict(
            os.environ,
            {
                "OPENTEAM_LLM_GATEWAY": "litellm_proxy",
                "OPENTEAM_LLM_MODEL": "openai/gpt-5.4",
            },
            clear=True,
        ):
            runtime_root = Path(td) / "runtime"
            state_dir = runtime_root / "state" / "openteam"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "litellm_proxy.json").write_text(
                '{"base_url":"http://127.0.0.1:4001/v1"}\n',
                encoding="utf-8",
            )

            cfg = self.mod._require_llm_config(runtime_root)

        self.assertTrue(bool(cfg.get("ok")))
        self.assertEqual(cfg.get("auth_strategy"), "litellm_proxy")
        self.assertEqual(cfg.get("base_url"), "http://127.0.0.1:4001/v1")

    def test_status_snapshot_includes_litellm_proxy_state(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_GATEWAY": "litellm_proxy"},
            clear=False,
        ):
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            runtime_root.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(self.mod, "_run_json", return_value={"ok": True}), mock.patch.object(
                self.mod, "_litellm_proxy_status",
                return_value={"enabled": True, "running": True, "pid": 4040, "base_url": "http://127.0.0.1:4000/v1"},
            ):
                out = self.mod._status_snapshot(repo, runtime_root, workspace_root, "http://127.0.0.1:8787")

            self.assertTrue(bool(out.get("llm_gateway", {}).get("enabled")))
            self.assertTrue(bool(out.get("llm_gateway", {}).get("running")))
            self.assertEqual(out.get("llm_gateway", {}).get("pid"), 4040)

    def test_stop_flow_stops_litellm_proxy_when_enabled(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_GATEWAY": "litellm_proxy"},
            clear=False,
        ):
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(self.mod, "_stop_pid", return_value={"ok": True, "stopped": True, "pid": 1234}), mock.patch.object(
                self.mod, "_stop_litellm_proxy", return_value={"ok": True, "stopped": True, "pid": 4040}
            ) as stop_proxy:
                out = self.mod._stop_flow(repo, runtime_root, workspace_root)

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(out.get("llm_gateway", {}).get("pid"), 4040)
            stop_proxy.assert_called_once_with(runtime_root)

    def test_start_flow_starts_litellm_proxy_before_control_plane(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_GATEWAY": "litellm_proxy"},
            clear=False,
        ):
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "openteam-runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            calls: list[str] = []
            real_layout = self.mod._ensure_runtime_layout

            def fake_purity(_repo, _ws):
                calls.append("purity")
                return {"ok": True}

            def fake_layout(_rt):
                calls.append("layout")
                real_layout(runtime_root)

            def fake_llm(*args, **kwargs):
                _ = args, kwargs
                calls.append("llm")
                return {"ok": True, "base_url": "http://127.0.0.1:4000/v1", "auth_strategy": "litellm_proxy"}

            def fake_db(*args, **kwargs):
                _ = args, kwargs
                calls.append("local_db")
                return {"ok": True, "path": str(runtime_root / "state" / "runtime.db")}

            def fake_deps(*args, **kwargs):
                _ = args, kwargs
                calls.append("deps")
                return {"ok": True, "installed": [], "missing": [], "python": sys.executable}

            def fake_proxy(*args, **kwargs):
                _ = args, kwargs
                calls.append("litellm_proxy")
                return {"ok": True, "pid": 4040, "base_url": "http://127.0.0.1:4000/v1"}

            def fake_cp(*args, **kwargs):
                _ = args, kwargs
                calls.append("control_plane")
                return {"ok": True, "pid": 2222}

            def fake_crewai(*args, **kwargs):
                _ = args, kwargs
                calls.append("crewai_ready")
                return {"ok": True}

            def fake_su_boot(*args, **kwargs):
                _ = args, kwargs
                calls.append("su_boot")
                return {"ok": True, "applied_count": 0}

            def fake_resume(*args, **kwargs):
                _ = args, kwargs
                calls.append("resume")
                return {"ok": True, "resumed": []}

            def fake_snapshot(*args, **kwargs):
                _ = args, kwargs
                calls.append("snapshot")
                return {"ok": True, "summary": "done"}

            with mock.patch.object(self.mod, "_check_repo_purity", side_effect=fake_purity), mock.patch.object(
                self.mod, "_ensure_runtime_layout", side_effect=fake_layout
            ), mock.patch.object(
                self.mod, "_require_llm_config", side_effect=fake_llm
            ), mock.patch.object(
                self.mod, "_ensure_local_runtime_db", side_effect=fake_db
            ), mock.patch.object(self.mod, "_ensure_python_dependencies", side_effect=fake_deps), mock.patch.object(
                self.mod, "_start_litellm_proxy", side_effect=fake_proxy
            ), mock.patch.object(
                self.mod, "_start_control_plane", side_effect=fake_cp
            ), mock.patch.object(
                self.mod, "_ensure_crewai_ready", side_effect=fake_crewai
            ), mock.patch.object(
                self.mod, "_run_default_team_bootstrap", side_effect=fake_su_boot
            ), mock.patch.object(
                self.mod, "_read_default_team_state", return_value={"last_run": {"ts": "2026-02-28T00:00:00Z", "status": "DONE"}}
            ), mock.patch.object(self.mod, "_resume_tasks", side_effect=fake_resume), mock.patch.object(
                self.mod, "_status_snapshot", side_effect=fake_snapshot
            ), mock.patch.dict(os.environ, dict(os.environ), clear=True):
                out = self.mod._start_flow(repo, runtime_root, workspace_root, port=8787)

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(
                calls,
                [
                    "purity",
                    "layout",
                    "llm",
                    "local_db",
                    "deps",
                    "litellm_proxy",
                    "control_plane",
                    "crewai_ready",
                    "su_boot",
                    "resume",
                    "snapshot",
                ],
            )

    def test_start_flow_reports_actual_litellm_base_url_after_auto_port_fallback(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_GATEWAY": "litellm_proxy"},
            clear=False,
        ):
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "openteam-runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            real_layout = self.mod._ensure_runtime_layout

            def fake_layout(_rt):
                real_layout(runtime_root)

            with mock.patch.object(self.mod, "_check_repo_purity", return_value={"ok": True}), mock.patch.object(
                self.mod, "_ensure_runtime_layout", side_effect=fake_layout
            ), mock.patch.object(
                self.mod,
                "_require_llm_config",
                return_value={
                    "ok": True,
                    "base_url": "http://127.0.0.1:4000/v1",
                    "auth_strategy": "litellm_proxy",
                },
            ), mock.patch.object(
                self.mod, "_ensure_local_runtime_db", return_value={"ok": True, "path": str(runtime_root / "state" / "runtime.db")}
            ), mock.patch.object(
                self.mod, "_ensure_python_dependencies", return_value={"ok": True, "python": sys.executable}
            ), mock.patch.object(
                self.mod, "_start_litellm_proxy", return_value={"ok": True, "enabled": True, "pid": 4040, "base_url": "http://127.0.0.1:4001/v1"}
            ), mock.patch.object(
                self.mod, "_start_control_plane", return_value={"ok": True, "pid": 2222}
            ), mock.patch.object(
                self.mod, "_ensure_crewai_ready", return_value={"ok": True}
            ), mock.patch.object(
                self.mod, "_run_default_team_bootstrap", return_value={"ok": True}
            ), mock.patch.object(
                self.mod, "_read_default_team_state", return_value={"last_run": {"ts": "2026-02-28T00:00:00Z", "status": "DONE"}}
            ), mock.patch.object(
                self.mod, "_resume_tasks", return_value={"ok": True, "resumed": []}
            ), mock.patch.object(
                self.mod, "_status_snapshot", return_value={"ok": True, "llm": {"base_url": "http://127.0.0.1:4001/v1"}}
            ):
                out = self.mod._start_flow(repo, runtime_root, workspace_root, port=8787)

            self.assertEqual(out.get("startup", {}).get("llm", {}).get("base_url"), "http://127.0.0.1:4001/v1")

    def test_start_control_plane_drops_proxy_env_vars(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            os.environ,
            {
                "ALL_PROXY": "socks5h://127.0.0.1:7893",
                "all_proxy": "socks5h://127.0.0.1:7893",
                "HTTP_PROXY": "http://127.0.0.1:7893",
                "HTTPS_PROXY": "http://127.0.0.1:7893",
                "http_proxy": "http://127.0.0.1:7893",
                "https_proxy": "http://127.0.0.1:7893",
            },
            clear=False,
        ):
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "runtime"
            workspace_root = Path(td) / "workspace"
            orch_dir = repo / "scaffolds" / "runtime" / "orchestrator"
            orch_dir.mkdir(parents=True, exist_ok=True)
            captured_env: dict[str, str] = {}

            class _FakeProc:
                pid = 4321

            def fake_popen(cmd, **kwargs):
                _ = cmd
                captured_env.update(kwargs.get("env") or {})
                return _FakeProc()

            with mock.patch.object(self.mod.subprocess, "Popen", side_effect=fake_popen), mock.patch.object(
                self.mod, "_wait_http_ready", return_value={"ok": True}
            ):
                out = self.mod._start_control_plane(
                    repo,
                    runtime_root,
                    workspace_root,
                    base_url="http://127.0.0.1:8787",
                    port=8787,
                    python_exec=sys.executable,
                )

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(captured_env.get("ALL_PROXY", None), "")
            self.assertEqual(captured_env.get("all_proxy", None), "")
            self.assertEqual(captured_env.get("HTTP_PROXY", None), "")
            self.assertEqual(captured_env.get("HTTPS_PROXY", None), "")
            self.assertEqual(captured_env.get("http_proxy", None), "")
            self.assertEqual(captured_env.get("https_proxy", None), "")

    def test_start_flow_requires_repo_improvement_actual_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "openteam-runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(self.mod, "_check_repo_purity", return_value={"ok": True}), mock.patch.object(
                self.mod, "_require_llm_config", return_value={"ok": True}
            ), mock.patch.object(self.mod, "_run_json", return_value={"ok": True}), mock.patch.object(
                self.mod, "_ensure_python_dependencies", return_value={"ok": True}
            ), mock.patch.object(
                self.mod, "_start_control_plane", return_value={"ok": True, "pid": 1234}
            ), mock.patch.object(
                self.mod, "_ensure_crewai_ready", return_value={"ok": True}
            ), mock.patch.object(
                self.mod, "_run_default_team_bootstrap", return_value={"ok": True}
            ), mock.patch.object(
                self.mod, "_read_default_team_state", return_value={}
            ), mock.patch.object(self.mod, "_resume_tasks", return_value={"ok": True, "resumed": []}), mock.patch.object(
                self.mod, "_status_snapshot", return_value={"ok": True}
            ), mock.patch.dict(os.environ, dict(os.environ), clear=True):
                with self.assertRaises(self.mod.BootstrapError):
                    self.mod._start_flow(repo, runtime_root, workspace_root, port=8787)

    def test_start_flow_order_and_success(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "openteam-runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            calls: list[str] = []
            real_layout = self.mod._ensure_runtime_layout

            def fake_purity(_repo, _ws):
                calls.append("purity")
                return {"ok": True}

            def fake_layout(_rt):
                calls.append("layout")
                real_layout(runtime_root)

            def fake_llm(*args, **kwargs):
                _ = args, kwargs
                calls.append("llm")
                return {"ok": True, "base_url": "x", "api_key_masked": "y"}

            def fake_db(*args, **kwargs):
                _ = args, kwargs
                calls.append("local_db")
                return {"ok": True, "path": str(runtime_root / "state" / "runtime.db")}

            def fake_deps(*args, **kwargs):
                _ = args, kwargs
                calls.append("deps")
                self.assertEqual(kwargs, {})
                return {"ok": True, "python": sys.executable}

            def fake_cp(*args, **kwargs):
                _ = args, kwargs
                calls.append("control_plane")
                return {"ok": True, "pid": 2222}

            def fake_crewai(*args, **kwargs):
                _ = args, kwargs
                calls.append("crewai_ready")
                return {"ok": True}

            def fake_su_boot(*args, **kwargs):
                _ = args, kwargs
                calls.append("team_bootstrap")
                return {"ok": True, "applied_count": 0}

            def fake_resume(*args, **kwargs):
                _ = args, kwargs
                calls.append("resume")
                return {"ok": True, "resumed": []}

            def fake_snapshot(*args, **kwargs):
                _ = args, kwargs
                calls.append("snapshot")
                return {"ok": True, "summary": "done"}

            with mock.patch.object(self.mod, "_check_repo_purity", side_effect=fake_purity), mock.patch.object(
                self.mod, "_ensure_runtime_layout", side_effect=fake_layout
            ), mock.patch.object(
                self.mod, "_require_llm_config", side_effect=fake_llm
            ), mock.patch.object(self.mod, "_ensure_local_runtime_db", side_effect=fake_db), mock.patch.object(
                self.mod, "_ensure_python_dependencies", side_effect=fake_deps
            ), mock.patch.object(
                self.mod, "_start_control_plane", side_effect=fake_cp
            ), mock.patch.object(
                self.mod, "_ensure_crewai_ready", side_effect=fake_crewai
            ), mock.patch.object(
                self.mod, "_run_default_team_bootstrap", side_effect=fake_su_boot
            ), mock.patch.object(
                self.mod, "_read_default_team_state", return_value={"last_run": {"ts": "2026-02-28T00:00:00Z", "status": "DONE"}}
            ), mock.patch.object(self.mod, "_resume_tasks", side_effect=fake_resume), mock.patch.object(
                self.mod, "_status_snapshot", side_effect=fake_snapshot
            ), mock.patch.dict(os.environ, dict(os.environ), clear=True):
                out = self.mod._start_flow(repo, runtime_root, workspace_root, port=8787)

            self.assertTrue(bool(out.get("ok")))
            self.assertEqual(
                calls,
                [
                    "purity",
                    "layout",
                    "llm",
                    "local_db",
                    "deps",
                    "control_plane",
                    "crewai_ready",
                    "team_bootstrap",
                    "resume",
                    "snapshot",
                ],
            )
            self.assertIn("recovery", out.get("startup") or {})
            self.assertNotIn("resume", out.get("startup") or {})


if __name__ == "__main__":
    unittest.main()
