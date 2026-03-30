import os
import sys
import types
import unittest
import importlib.util
from pathlib import Path
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

if "agents" not in sys.modules:
    agents_mod = types.ModuleType("agents")

    class _DummyAgent:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    agents_mod.Agent = _DummyAgent
    sys.modules["agents"] = agents_mod

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None
if FASTAPI_AVAILABLE:
    from app import main as app_main  # noqa: E402
    from app import state_store  # noqa: E402
else:  # pragma: no cover
    app_main = None
    state_store = None


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is not available in this test environment")
class RuntimeHealthzTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = dict(os.environ)
        self.repo_root = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

    def test_openteam_checks_follow_current_repo_layout(self) -> None:
        checks = app_main._openteam_checks(str(self.repo_root))

        self.assertTrue(checks["exists"])
        self.assertTrue(checks["specs_workflows_dir_exists"])
        self.assertTrue(checks["specs_roles_dir_exists"])
        self.assertTrue(checks["runtime_role_library_exists"])
        self.assertTrue(checks["team_specs_exist"])
        self.assertTrue(checks["orchestrator_exists"])
        self.assertIn("trunk.yaml", checks["workflow_files"])
        self.assertIn("Architect.md", checks["role_files"])

    def test_healthz_is_ok_with_current_repo_layout(self) -> None:
        os.environ["OPENTEAM_REPO_PATH"] = str(self.repo_root)
        response = app_main.Response()
        with (
            mock.patch.object(app_main.engine_runtime, "probe_crewai", return_value={"importable": True, "version": "test"}),
            mock.patch.object(app_main.DB, "list_events", return_value=[]),
        ):
            payload = app_main.healthz(response)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(response.status_code, 200)

    def test_healthz_no_longer_reports_redis_bus(self) -> None:
        os.environ["OPENTEAM_REPO_PATH"] = str(self.repo_root)
        response = app_main.Response()
        with (
            mock.patch.object(app_main.engine_runtime, "probe_crewai", return_value={"importable": True, "version": "test"}),
            mock.patch.object(app_main.DB, "list_events", return_value=[]),
        ):
            payload = app_main.healthz(response)

        self.assertEqual(payload["status"], "ok")
        self.assertNotIn("redis_bus", payload)

    def test_status_no_longer_reports_redis_bus(self) -> None:
        with (
            mock.patch.object(app_main, "_active_projects_summary", return_value=[]),
            mock.patch.object(app_main, "_load_tasks_summary", return_value=[]),
            mock.patch.object(app_main.DB, "list_runs", return_value=[]),
            mock.patch.object(app_main.DB, "list_agents", return_value=[]),
        ):
            payload = app_main.v1_status()

        self.assertNotIn("redis_bus", payload)

    def test_openteam_requirements_dir_uses_product_docs_path(self) -> None:
        os.environ["OPENTEAM_REPO_PATH"] = str(self.repo_root)
        req_dir = state_store.openteam_requirements_dir()
        self.assertEqual(req_dir, self.repo_root / "docs" / "product" / "openteam" / "requirements")


if __name__ == "__main__":
    unittest.main()
