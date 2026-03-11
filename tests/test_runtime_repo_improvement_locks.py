import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace


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

    def test_repo_improvement_run_id_set_reads_run_started_flow(self) -> None:
        fake_db = SimpleNamespace(
            list_events=lambda limit=5000: [
                SimpleNamespace(
                    event_type="RUN_STARTED",
                    payload={"run_id": "run-123", "flow": "repo_improvement"},
                ),
                SimpleNamespace(
                    event_type="RUN_STARTED",
                    payload={"run_id": "run-ignored", "flow": "other"},
                ),
            ]
        )
        original_db = app_main.DB
        try:
            app_main.DB = fake_db
            self.assertEqual(app_main._repo_improvement_run_id_set(), {"run-123"})
        finally:
            app_main.DB = original_db

    def test_cleanup_stale_repo_improvement_run_uses_event_flow_not_objective_text(self) -> None:
        stale_run = SimpleNamespace(
            run_id="run-123",
            project_id="projectmanager",
            workstream_id="general",
            objective="parallel lock test projectmanager",
            state="RUNNING",
            started_at="2026-03-11T16:27:16Z",
            last_update="2026-03-11T16:27:16Z",
        )
        fake_db = SimpleNamespace(
            list_runs=lambda: [stale_run],
            list_agents=lambda: [],
            list_events=lambda limit=5000: [
                SimpleNamespace(
                    event_type="RUN_STARTED",
                    payload={"run_id": "run-123", "flow": "repo_improvement"},
                )
            ],
            update_run_state_calls=[],
            event_calls=[],
        )

        def _update_run_state(*, run_id: str, state: str) -> None:
            fake_db.update_run_state_calls.append((run_id, state))

        def _add_event(*, event_type: str, actor: str, project_id: str, workstream_id: str, payload: dict) -> int:
            fake_db.event_calls.append((event_type, actor, project_id, workstream_id, payload))
            return 1

        fake_db.update_run_state = _update_run_state
        fake_db.add_event = _add_event

        original_db = app_main.DB
        original_ttl = os.environ.get("TEAMOS_REPO_IMPROVEMENT_STALE_TTL_SEC")
        try:
            app_main.DB = fake_db
            os.environ["TEAMOS_REPO_IMPROVEMENT_STALE_TTL_SEC"] = "300"
            app_main._cleanup_stale_repo_improvement_activity()
            self.assertEqual(fake_db.update_run_state_calls, [("run-123", "FAILED")])
            self.assertTrue(any(call[0] == "REPO_IMPROVEMENT_STALE_RUN_CLEANED" for call in fake_db.event_calls))
        finally:
            app_main.DB = original_db
            if original_ttl is None:
                os.environ.pop("TEAMOS_REPO_IMPROVEMENT_STALE_TTL_SEC", None)
            else:
                os.environ["TEAMOS_REPO_IMPROVEMENT_STALE_TTL_SEC"] = original_ttl


if __name__ == "__main__":
    unittest.main()
