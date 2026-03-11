import importlib.util
import os
import sys
import types
import unittest


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
else:  # pragma: no cover
    app_main = None


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is not available in this test environment")
class RepoImprovementLockTests(unittest.TestCase):
    def test_repo_improvement_lock_key_prefers_target_id(self) -> None:
        key = app_main._repo_improvement_lock_key(
            project_id="teamos",
            target_id="wangguanran-projectmanager",
            repo_path="/tmp/repo",
            repo_url="https://github.com/wangguanran/ProjectManager.git",
            repo_locator="wangguanran/ProjectManager",
        )
        self.assertEqual(key, "target:wangguanran-projectmanager")

    def test_repo_improvement_lock_key_falls_back_to_repo_path(self) -> None:
        key = app_main._repo_improvement_lock_key(
            project_id="teamos",
            repo_path="/tmp/repo",
        )
        self.assertEqual(key, "repo_path:/tmp/repo")

    def test_scoped_run_locks_allow_parallel_different_keys(self) -> None:
        locks = app_main._ScopedRunLocks()
        self.assertTrue(locks.acquire("target:teamos"))
        self.assertTrue(locks.acquire("target:projectmanager"))
        self.assertFalse(locks.acquire("target:teamos"))
        locks.release("target:teamos")
        self.assertTrue(locks.acquire("target:teamos"))

    def test_delivery_lock_key_prefers_task_then_target(self) -> None:
        task_key = app_main._repo_improvement_delivery_lock_key(
            project_id="teamos",
            target_id="wangguanran-projectmanager",
            task_id="TASK-123",
        )
        target_key = app_main._repo_improvement_delivery_lock_key(
            project_id="teamos",
            target_id="wangguanran-projectmanager",
            task_id="",
        )
        self.assertEqual(task_key, "task:TASK-123")
        self.assertEqual(target_key, "target:wangguanran-projectmanager")


if __name__ == "__main__":
    unittest.main()
