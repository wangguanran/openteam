import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()
os.environ.setdefault("TEAMOS_RUNTIME_LOCALIZE_ZH", "0")

from app.domains.repo_improvement import task_runtime  # noqa: E402
from app.skill_library import repo_skills as workflow_skills  # noqa: E402


class _FakeDB:
    pass


def _workflow(lane: str, *, max_units_per_tick: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_id=f"{lane}-coding",
        lane=lane,
        loop=SimpleNamespace(max_units_per_tick=max_units_per_tick),
    )


class CrewAIRepoImprovementDeliveryTests(unittest.TestCase):
    def test_run_delivery_workflow_stops_when_no_matching_lane_tasks(self):
        context = SimpleNamespace(
            db=_FakeDB(),
            workflow=_workflow("bug"),
            actor="test",
            project_id="demo",
            target_id="target-a",
            task_id="",
            dry_run=True,
            force=False,
        )

        with mock.patch("app.skill_library.repo_skills.task_runtime.list_delivery_tasks", return_value=[]), mock.patch(
            "app.skill_library.repo_skills.task_runtime.delivery_summary",
            return_value={"total": 0, "queued": 0},
        ):
            out = workflow_skills.run_delivery_pipeline_skill(context=context, inputs={}, state={}, spec=None)

        self.assertTrue(out["ok"])
        self.assertTrue(out["control"]["stop"])
        self.assertEqual(out["control"]["reason"], "no_bug_delivery_tasks")

    def test_run_delivery_workflow_filters_lane_and_honors_limit(self):
        with tempfile.TemporaryDirectory() as td:
            task_dir = Path(td)
            bug_doc = {
                "id": "BUG-1",
                "status": "todo",
                "repo_improvement": {"lane": "bug"},
                "execution_policy": {"allowed_paths": ["src/demo.py"]},
            }
            feature_doc = {
                "id": "FEATURE-1",
                "status": "todo",
                "repo_improvement": {"lane": "feature"},
                "execution_policy": {"allowed_paths": ["src/demo.py"]},
            }
            second_bug_doc = {
                "id": "BUG-2",
                "status": "todo",
                "repo_improvement": {"lane": "bug"},
                "execution_policy": {"allowed_paths": ["src/demo.py"]},
            }
            bug_path = task_dir / "BUG-1.yaml"
            feature_path = task_dir / "FEATURE-1.yaml"
            second_bug_path = task_dir / "BUG-2.yaml"
            bug_path.write_text(yaml.safe_dump(bug_doc, sort_keys=False), encoding="utf-8")
            feature_path.write_text(yaml.safe_dump(feature_doc, sort_keys=False), encoding="utf-8")
            second_bug_path.write_text(yaml.safe_dump(second_bug_doc, sort_keys=False), encoding="utf-8")

            tasks = [
                {"task_id": "BUG-1", "project_id": "demo", "target_id": "target-a", "status": "todo", "ledger_path": str(bug_path)},
                {"task_id": "FEATURE-1", "project_id": "demo", "target_id": "target-a", "status": "todo", "ledger_path": str(feature_path)},
                {"task_id": "BUG-2", "project_id": "demo", "target_id": "target-a", "status": "todo", "ledger_path": str(second_bug_path)},
            ]
            context = SimpleNamespace(
                db=_FakeDB(),
                workflow=_workflow("bug", max_units_per_tick=1),
                actor="test",
                project_id="demo",
                target_id="target-a",
                task_id="",
                dry_run=True,
                force=False,
            )

            with mock.patch("app.skill_library.repo_skills.task_runtime.list_delivery_tasks", return_value=tasks), mock.patch(
                "app.skill_library.repo_skills.task_runtime._claim_delivery_task_lease",
                return_value={"lease_key": "lease-1"},
            ), mock.patch(
                "app.skill_library.repo_skills.task_runtime._execute_delivery_candidate",
                return_value={"ok": True, "task_id": "BUG-1", "status": "closed", "project_id": "demo"},
            ) as execute_mock, mock.patch(
                "app.skill_library.repo_skills.task_runtime.delivery_summary",
                return_value={"total": 3, "queued": 1},
            ):
                out = workflow_skills.run_delivery_pipeline_skill(context=context, inputs={}, state={}, spec=None)

        self.assertTrue(out["ok"])
        self.assertEqual(out["outputs"]["scanned"], 2)
        self.assertEqual(out["outputs"]["processed"], 1)
        self.assertEqual(len(out["outputs"]["tasks"]), 1)
        execute_mock.assert_called_once()
        executed_path = execute_mock.call_args.kwargs["ledger_path"]
        self.assertEqual(Path(executed_path).name, "BUG-1.yaml")

    def test_list_delivery_tasks_reads_runtime_docs(self):
        with mock.patch(
            "app.domains.repo_improvement.task_runtime.improvement_store.list_delivery_tasks",
            return_value=[
                {
                    "id": "TASK-1",
                    "title": "Demo task",
                    "project_id": "demo",
                    "workstream_id": "general",
                    "status": "todo",
                    "owner_role": "Coding-Agent",
                    "orchestration": {"engine": "crewai", "flow": "repo_improvement"},
                    "artifacts": {"ledger_path": "/tmp/TASK-1.yaml"},
                    "repo_improvement_execution": {"stage": "coding", "attempt_count": 1},
                    "links": {"issue": "https://github.com/foo/bar/issues/1"},
                }
            ],
        ), mock.patch("app.domains.repo_improvement.task_runtime.workspace_store.list_projects", return_value=[]):
            tasks = task_runtime.list_delivery_tasks(project_id="demo")

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "TASK-1")
        self.assertEqual(tasks[0]["stage"], "coding")


if __name__ == "__main__":
    unittest.main()
