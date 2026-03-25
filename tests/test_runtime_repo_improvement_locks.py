import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace
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
else:  # pragma: no cover
    app_main = None


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is not available in this test environment")
class RepoImprovementLockTests(unittest.TestCase):
    def test_team_run_lock_key_prefers_target_id(self) -> None:
        key = app_main._team_run_lock_key(
            project_id="openteam",
            target_id="openteam-dev-projectmanager",
            repo_path="/tmp/repo",
            repo_url="https://github.com/openteam-dev/ProjectManager.git",
            repo_locator="openteam-dev/ProjectManager",
        )
        self.assertEqual(key, "target:openteam-dev-projectmanager")

    def test_team_run_lock_key_falls_back_to_repo_path(self) -> None:
        key = app_main._team_run_lock_key(
            project_id="openteam",
            repo_path="/tmp/repo",
        )
        self.assertEqual(key, "repo_path:/tmp/repo")

    def test_scoped_run_locks_allow_parallel_different_keys(self) -> None:
        locks = app_main._ScopedRunLocks()
        self.assertTrue(locks.acquire("target:openteam"))
        self.assertTrue(locks.acquire("target:projectmanager"))
        self.assertFalse(locks.acquire("target:openteam"))
        locks.release("target:openteam")
        self.assertTrue(locks.acquire("target:openteam"))

    def test_delivery_lock_key_prefers_task_then_target(self) -> None:
        task_key = app_main._team_coding_lock_key(
            project_id="openteam",
            target_id="openteam-dev-projectmanager",
            task_id="TASK-123",
        )
        target_key = app_main._team_coding_lock_key(
            project_id="openteam",
            target_id="openteam-dev-projectmanager",
            task_id="",
        )
        self.assertEqual(task_key, "task:TASK-123")
        self.assertEqual(target_key, "target:openteam-dev-projectmanager")

    def test_team_run_id_set_reads_run_started_flow(self) -> None:
        fake_db = SimpleNamespace(
            list_events=lambda limit=5000: [
                SimpleNamespace(
                    event_type="RUN_STARTED",
                    payload={"run_id": "run-123", "flow": "team:repo-improvement"},
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
            self.assertEqual(app_main._team_run_id_set(), {"run-123"})
        finally:
            app_main.DB = original_db

    def test_effective_team_project_id_prefers_target_project(self) -> None:
        with mock.patch.object(
            app_main.improvement_store,
            "get_target",
            return_value={"target_id": "openteam-dev-projectmanager", "project_id": "projectmanager"},
        ):
            project_id = app_main._effective_team_project_id(
                project_id="openteam",
                target_id="openteam-dev-projectmanager",
            )
        self.assertEqual(project_id, "projectmanager")

    def test_repo_improvement_delivery_iteration_prefers_target_project(self) -> None:
        sweep_mock = mock.MagicMock(return_value={"ok": True, "processed": 0, "summary": {"total": 0}})
        fake_adapter = SimpleNamespace(run_delivery_sweep_fn=sweep_mock)
        with mock.patch.object(
            app_main.improvement_store,
            "get_target",
            return_value={"target_id": "openteam-dev-projectmanager", "project_id": "projectmanager"},
        ), mock.patch.object(
            app_main,
            "_cleanup_stale_team_activity",
        ), mock.patch.object(
            app_main._TEAM_CODING_LOCKS,
            "acquire",
            return_value=True,
        ), mock.patch.object(
            app_main._TEAM_CODING_LOCKS,
            "release",
        ) as release_mock, mock.patch.object(
            app_main,
            "_team_runtime",
            return_value=fake_adapter,
        ):
            out = app_main._run_team_coding_iteration(
                team_id="repo-improvement",
                actor="test",
                project_id="openteam",
                target_id="openteam-dev-projectmanager",
                task_id="PROJECTMANAGER-0003",
                force=True,
            )

        self.assertTrue(out["ok"])
        self.assertEqual(sweep_mock.call_args.kwargs["project_id"], "projectmanager")
        self.assertEqual(out["project_id"], "projectmanager")
        release_mock.assert_called_once_with("task:PROJECTMANAGER-0003")

    def test_active_team_run_for_scope_matches_running_project_run(self) -> None:
        active_run = SimpleNamespace(
            run_id="run-projectmanager-1",
            project_id="projectmanager",
            workstream_id="general",
            objective="Continuous improvement sweep for target openteam-dev/ProjectManager",
            state="RUNNING",
        )
        fake_db = SimpleNamespace(list_runs=lambda project_id=None, workstream_id=None: [active_run])
        original_db = app_main.DB
        try:
            app_main.DB = fake_db
            out = app_main._active_team_run_for_scope(
                project_id="projectmanager",
                workstream_id="general",
                lock_key="project:projectmanager",
            )
        finally:
            app_main.DB = original_db

        self.assertIsNotNone(out)
        self.assertEqual(out.run_id, "run-projectmanager-1")

    def test_run_team_iteration_skips_when_active_project_run_exists(self) -> None:
        """When another team run holds the lock, _run_team_iteration returns skipped."""
        with mock.patch.object(app_main._TEAM_WORKFLOW_LOCKS, "acquire", return_value=False):
            out = app_main._run_team_iteration(
                team_id="repo-improvement",
                actor="test",
                project_id="projectmanager",
                workstream_id="general",
                objective="Continuous improvement sweep for target openteam-dev/ProjectManager",
                trigger="continuous_loop",
            )

        self.assertTrue(out["ok"])
        self.assertTrue(out["skipped"])
        self.assertEqual(out["reason"], "team_run_already_running")

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
                    payload={"run_id": "run-123", "flow": "team:repo-improvement"},
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
        original_ttl = os.environ.get("OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC")
        try:
            app_main.DB = fake_db
            os.environ["OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC"] = "300"
            app_main._cleanup_stale_team_activity()
            self.assertEqual(fake_db.update_run_state_calls, [("run-123", "FAILED")])
            self.assertTrue(any(call[0] == "TEAM_WORKFLOW_STALE_RUN_CLEANED" for call in fake_db.event_calls))
        finally:
            app_main.DB = original_db
            if original_ttl is None:
                os.environ.pop("OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC", None)
            else:
                os.environ["OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC"] = original_ttl

    def test_cleanup_stale_repo_improvement_run_cleans_gap_and_milestone_agents(self) -> None:
        stale_agents = [
            SimpleNamespace(
                agent_id="agent-gap",
                role_id=app_main.role_registry.ROLE_TEST_CASE_GAP_AGENT,
                project_id="projectmanager",
                workstream_id="general",
                state="RUNNING",
                last_heartbeat="2026-03-11T16:27:16Z",
                current_action="mapping black-box and white-box test gaps",
            ),
            SimpleNamespace(
                agent_id="agent-ms",
                role_id=app_main.role_registry.ROLE_MILESTONE_MANAGER,
                project_id="projectmanager",
                workstream_id="general",
                state="RUNNING",
                last_heartbeat="2026-03-11T16:27:16Z",
                current_action="planning release lines and milestones",
            ),
        ]
        fake_db = SimpleNamespace(
            list_runs=lambda: [],
            list_agents=lambda: stale_agents,
            list_events=lambda limit=5000: [],
            update_calls=[],
            event_calls=[],
        )

        def _update_assignment(**kwargs) -> None:
            fake_db.update_calls.append(kwargs)

        def _add_event(**kwargs) -> int:
            fake_db.event_calls.append(kwargs)
            return 1

        fake_db.update_assignment = _update_assignment
        fake_db.add_event = _add_event

        original_db = app_main.DB
        original_ttl = os.environ.get("OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC")
        try:
            app_main.DB = fake_db
            os.environ["OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC"] = "300"
            app_main._cleanup_stale_team_activity()
            cleaned_ids = {str(call.get("agent_id") or "") for call in fake_db.update_calls}
            self.assertEqual(cleaned_ids, {"agent-gap", "agent-ms"})
            self.assertEqual(
                sum(1 for call in fake_db.event_calls if call.get("event_type") == "TEAM_WORKFLOW_STALE_AGENT_CLEANED"),
                2,
            )
        finally:
            app_main.DB = original_db
            if original_ttl is None:
                os.environ.pop("OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC", None)
            else:
                os.environ["OPENTEAM_RUNTIME_TEAM_WORKFLOW_STALE_TTL_SEC"] = original_ttl


if __name__ == "__main__":
    unittest.main()
