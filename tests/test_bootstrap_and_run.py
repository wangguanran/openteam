import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
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
            rt = Path(td) / "team-os-runtime"
            self.mod._ensure_runtime_layout(rt)
            self.mod._ensure_runtime_layout(rt)
            self.assertTrue((rt / "state" / "audit").exists())
            self.assertTrue((rt / "workspace" / "projects").exists())
            self.assertTrue((rt / "workspace" / "shared" / "cache").exists())
            self.assertTrue((rt / "workspace" / "shared" / "tmp").exists())
            self.assertTrue((rt / "workspace" / "config").exists())
            self.assertTrue((rt / "hub").exists())
            self.assertTrue((rt / "tmp").exists())
            self.assertTrue((rt / "cache").exists())

    def test_quarantine_legacy_team_os_dir(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            (repo / ".team-os" / "state").mkdir(parents=True, exist_ok=True)
            (repo / ".team-os" / "state" / "x.json").write_text("{}", encoding="utf-8")
            out = self.mod._quarantine_legacy_team_os_dir(repo, runtime_root)
            self.assertTrue(bool(out.get("ok")))
            self.assertTrue(bool(out.get("found")))
            self.assertFalse((repo / ".team-os").exists())
            self.assertTrue(Path(str(out.get("moved_to") or "")).exists())

    def test_start_flow_requires_self_improve_actual_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            (runtime_root / "hub" / "env").mkdir(parents=True, exist_ok=True)
            (runtime_root / "hub" / "env" / ".env").write_text(
                "\n".join(
                    [
                        "POSTGRES_USER=teamos",
                        "POSTGRES_PASSWORD=pw",
                        "POSTGRES_DB=teamos",
                        "PG_BIND_IP=127.0.0.1",
                        "PG_PORT=5432",
                        "REDIS_BIND_IP=127.0.0.1",
                        "REDIS_PORT=6379",
                        "REDIS_PASSWORD=rpw",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(self.mod, "_check_repo_purity", return_value={"ok": True}), mock.patch.object(self.mod, "_run_json", return_value={"ok": True}), mock.patch.object(
                self.mod, "_wait_hub_healthy", return_value={"ok": True, "postgres": {"tcp_open": True}, "redis": {"tcp_open": True}
                }
            ), mock.patch.object(self.mod, "_ensure_python_dependencies", return_value={"ok": True}), mock.patch.object(
                self.mod, "_start_control_plane", return_value={"ok": True, "pid": 1234}
            ), mock.patch.object(
                self.mod, "_ensure_crewai_ready", return_value={"ok": True}
            ), mock.patch.object(self.mod, "_run_self_improve_start", return_value={"ok": True}), mock.patch.object(
                self.mod, "_run_self_improve_bootstrap", return_value={"ok": True}
            ), mock.patch.object(self.mod, "_resume_tasks", return_value={"ok": True, "resumed": []}), mock.patch.object(
                self.mod, "_status_snapshot", return_value={"ok": True}
            ), mock.patch.dict(os.environ, dict(os.environ), clear=True):
                with self.assertRaises(self.mod.BootstrapError):
                    self.mod._start_flow(repo, runtime_root, workspace_root, port=8787)

    def test_start_flow_order_and_success(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = runtime_root / "workspace"
            repo.mkdir(parents=True, exist_ok=True)
            (runtime_root / "hub" / "env").mkdir(parents=True, exist_ok=True)
            (runtime_root / "hub" / "env" / ".env").write_text(
                "\n".join(
                    [
                        "POSTGRES_USER=teamos",
                        "POSTGRES_PASSWORD=pw",
                        "POSTGRES_DB=teamos",
                        "PG_BIND_IP=127.0.0.1",
                        "PG_PORT=5432",
                        "REDIS_BIND_IP=127.0.0.1",
                        "REDIS_PORT=6379",
                        "REDIS_PASSWORD=rpw",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            calls: list[str] = []
            real_layout = self.mod._ensure_runtime_layout

            def fake_purity(_repo, _ws):
                calls.append("purity")
                return {"ok": True}

            def fake_layout(_rt):
                calls.append("layout")
                real_layout(runtime_root)

            def fake_run_json(cmd, **kwargs):
                _ = kwargs
                txt = " ".join(cmd)
                if "hub_init.py" in txt:
                    calls.append("hub_init")
                elif "hub_up.py" in txt:
                    calls.append("hub_up")
                elif "hub_migrate.py" in txt:
                    calls.append("hub_migrate")
                return {"ok": True}

            def fake_hub_health(_repo, _ws, timeout_sec=90):
                _ = timeout_sec
                calls.append("hub_health")
                return {"ok": True, "postgres": {"tcp_open": True}, "redis": {"tcp_open": True}}

            def fake_cp(*args, **kwargs):
                _ = args, kwargs
                calls.append("control_plane")
                return {"ok": True, "pid": 2222}

            def fake_deps(*args, **kwargs):
                _ = args, kwargs
                calls.append("deps")
                return {"ok": True, "installed": [], "missing": []}

            def fake_crewai(*args, **kwargs):
                _ = args, kwargs
                calls.append("crewai_ready")
                return {"ok": True}

            def fake_si_start(*args, **kwargs):
                _ = args, kwargs
                calls.append("si_start")
                return {"ok": True}

            def fake_si_boot(*args, **kwargs):
                _ = args, kwargs
                calls.append("si_boot")
                st = runtime_root / "state" / "self_improve_state.json"
                st.parent.mkdir(parents=True, exist_ok=True)
                st.write_text(json.dumps({"last_run": {"ts": "2026-02-28T00:00:00Z"}}, ensure_ascii=False), encoding="utf-8")
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
            ), mock.patch.object(self.mod, "_run_json", side_effect=fake_run_json), mock.patch.object(
                self.mod, "_wait_hub_healthy", side_effect=fake_hub_health
            ), mock.patch.object(self.mod, "_ensure_python_dependencies", side_effect=fake_deps), mock.patch.object(
                self.mod, "_start_control_plane", side_effect=fake_cp
            ), mock.patch.object(
                self.mod, "_ensure_crewai_ready", side_effect=fake_crewai
            ), mock.patch.object(self.mod, "_run_self_improve_start", side_effect=fake_si_start), mock.patch.object(
                self.mod, "_run_self_improve_bootstrap", side_effect=fake_si_boot
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
                    "hub_init",
                    "hub_up",
                    "hub_health",
                    "hub_migrate",
                    "deps",
                    "control_plane",
                    "crewai_ready",
                    "si_start",
                    "si_boot",
                    "resume",
                    "snapshot",
                ],
            )


if __name__ == "__main__":
    unittest.main()
