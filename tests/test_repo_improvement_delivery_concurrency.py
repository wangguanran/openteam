import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app.domains.team_workflow import task_runtime as repo_delivery  # noqa: E402


class RepoImprovementDeliveryConcurrencyTests(unittest.TestCase):
    def test_delivery_worker_concurrency_defaults_to_ten(self):
        self.assertEqual(repo_delivery._delivery_worker_concurrency(None), 10)

    def test_run_delivery_sweep_processes_multiple_tasks_concurrently(self):
        tasks = [
            {
                "task_id": "TASK-1",
                "project_id": "demo",
                "target_id": "target-a",
                "status": "todo",
                "ledger_path": "/tmp/TASK-1.yaml",
                "team_workflow": {"lane": "bug"},
            },
            {
                "task_id": "TASK-2",
                "project_id": "demo",
                "target_id": "target-a",
                "status": "todo",
                "ledger_path": "/tmp/TASK-2.yaml",
                "team_workflow": {"lane": "bug"},
            },
        ]

        first_waiting = threading.Event()
        second_started = threading.Event()
        overlap = threading.Event()

        def _fake_execute(*, ledger_path, **kwargs):
            task_id = Path(str(ledger_path)).stem
            if task_id == "TASK-1":
                first_waiting.set()
                if second_started.wait(1.0):
                    overlap.set()
            else:
                second_started.set()
                if first_waiting.is_set():
                    overlap.set()
            return {"ok": True, "task_id": task_id, "status": "closed", "project_id": "demo"}

        fake_workflow = mock.Mock(workflow_id="coding")
        fake_runtime_policy = mock.Mock(allowed=True, reason="")

        with mock.patch("app.domains.team_workflow.task_runtime.list_delivery_tasks", return_value=tasks), mock.patch(
            "app.domains.team_workflow.task_runtime.crewai_workflow_registry.workflow_for_phase",
            return_value=fake_workflow,
        ), mock.patch(
            "app.domains.team_workflow.task_runtime.crewai_workflow_registry.evaluate_workflow_runtime_policy",
            return_value=fake_runtime_policy,
        ), mock.patch(
            "app.domains.team_workflow.task_runtime.crewai_workflow_registry.update_workflow_runtime_state",
            return_value={},
        ), mock.patch(
            "app.domains.team_workflow.task_runtime._claim_delivery_task_lease",
            side_effect=[object(), object()],
        ), mock.patch(
            "app.domains.team_workflow.task_runtime._load_yaml",
            return_value={},
        ), mock.patch(
            "app.domains.team_workflow.task_runtime.execute_task_delivery",
            side_effect=_fake_execute,
        ), mock.patch(
            "app.domains.team_workflow.task_runtime._release_delivery_task_lease",
            return_value=None,
        ), mock.patch(
            "app.domains.team_workflow.task_runtime.delivery_summary",
            return_value={"total": 2, "queued": 0, "coding": 2},
        ):
            out = repo_delivery.run_delivery_sweep(
                db=object(),
                actor="test-worker",
                project_id="demo",
                dry_run=True,
                concurrency=2,
            )

        self.assertEqual(out["processed"], 2)
        self.assertEqual(out["scanned"], 2)
        self.assertTrue(overlap.is_set(), "expected multiple delivery workers to overlap")


if __name__ == "__main__":
    unittest.main()
