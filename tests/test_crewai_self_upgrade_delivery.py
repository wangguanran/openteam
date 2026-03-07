import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "templates", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import crewai_self_upgrade_delivery  # noqa: E402


class _FakeDB:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.assignments: list[dict] = []
        self._n = 0

    def register_agent(self, **kwargs):
        self._n += 1
        return f"agent-{self._n}"

    def update_assignment(self, **kwargs):
        self.assignments.append(kwargs)

    def add_event(self, **kwargs):
        self.events.append(kwargs)


def _base_task_doc(*, repo_root: Path, task_id: str = "TEAMOS-1001", status: str = "todo") -> dict:
    return {
        "id": task_id,
        "title": "Demo self-upgrade task",
        "status": status,
        "project_id": "teamos",
        "workstream_id": "general",
        "owners": ["Feature-Coding-Agent"],
        "owner_role": "Feature-Coding-Agent",
        "orchestration": {"engine": "crewai", "flow": "self_upgrade"},
        "execution_policy": {
            "allowed_paths": ["src/demo.py"],
            "review_role": "Review-Agent",
            "qa_role": "QA-Agent",
            "commit_message_template": f"{task_id}: demo",
        },
        "repo": {
            "workdir": str(repo_root),
            "locator": "foo/bar",
        },
        "links": {"issue": "https://github.com/foo/bar/issues/101"},
        "self_upgrade": {
            "summary": "Implement the demo change.",
            "rationale": "Validate delivery orchestration.",
            "work_item": {
                "tests": ["python -m unittest tests.test_demo"],
                "acceptance": ["Demo path is updated"],
            },
        },
    }


class CrewAISelfUpgradeDeliveryTests(unittest.TestCase):
    def test_list_delivery_tasks_only_returns_self_upgrade_ledgers(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            with mock.patch.dict(
                os.environ,
                {
                    "TEAMOS_RUNTIME_ROOT": str(runtime_root),
                    "TEAMOS_WORKSPACE_ROOT": str(workspace_root),
                },
                clear=False,
            ):
                task_dir = crewai_self_upgrade_delivery._task_ledger_dir("teamos")
                task_dir.mkdir(parents=True, exist_ok=True)
                (task_dir / "TEAMOS-1001.yaml").write_text(
                    yaml.safe_dump(_base_task_doc(repo_root=repo_root), sort_keys=False),
                    encoding="utf-8",
                )
                other = _base_task_doc(repo_root=repo_root, task_id="TEAMOS-1002")
                other["orchestration"] = {"engine": "teamos", "flow": "standard"}
                (task_dir / "TEAMOS-1002.yaml").write_text(yaml.safe_dump(other, sort_keys=False), encoding="utf-8")

                tasks = crewai_self_upgrade_delivery.list_delivery_tasks()

            self.assertEqual([task["task_id"] for task in tasks], ["TEAMOS-1001"])

    def test_execute_task_delivery_dry_run_closes_task(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            with mock.patch.dict(
                os.environ,
                {
                    "TEAMOS_RUNTIME_ROOT": str(runtime_root),
                    "TEAMOS_WORKSPACE_ROOT": str(workspace_root),
                },
                clear=False,
            ):
                task_dir = crewai_self_upgrade_delivery._task_ledger_dir("teamos")
                task_dir.mkdir(parents=True, exist_ok=True)
                ledger_path = task_dir / "TEAMOS-1001.yaml"
                ledger_path.write_text(yaml.safe_dump(task_doc, sort_keys=False), encoding="utf-8")

                with mock.patch(
                    "app.crewai_self_upgrade_delivery._ensure_task_worktree",
                    return_value=(task_doc, repo_root, repo_root),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(
                        summary="implemented",
                        changed_files=["src/demo.py"],
                        tests_to_run=["python -m unittest tests.test_demo"],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_review_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryReviewResult(approved=True, summary="review ok"),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_qa_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryQAResult(
                        approved=True,
                        summary="qa ok",
                        commands=["python -m unittest tests.test_demo"],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._changed_files",
                    return_value=["src/demo.py"],
                ):
                    out = crewai_self_upgrade_delivery.execute_task_delivery(
                        db=db,
                        actor="test",
                        ledger_path=ledger_path,
                        doc=task_doc,
                        dry_run=True,
                    )

                updated = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}

            self.assertTrue(out["ok"])
            self.assertEqual(out["status"], "closed")
            self.assertEqual(updated["status"], "closed")
            self.assertEqual((updated.get("self_upgrade_execution") or {}).get("stage"), "closed")
            self.assertEqual(updated.get("owner_role"), "Feature-Coding-Agent")

    def test_execute_task_delivery_blocks_after_review_rejections(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            with mock.patch.dict(
                os.environ,
                {
                    "TEAMOS_RUNTIME_ROOT": str(runtime_root),
                    "TEAMOS_WORKSPACE_ROOT": str(workspace_root),
                    "TEAMOS_SELF_UPGRADE_DELIVERY_MAX_ATTEMPTS": "2",
                },
                clear=False,
            ):
                task_dir = crewai_self_upgrade_delivery._task_ledger_dir("teamos")
                task_dir.mkdir(parents=True, exist_ok=True)
                ledger_path = task_dir / "TEAMOS-1001.yaml"
                ledger_path.write_text(yaml.safe_dump(task_doc, sort_keys=False), encoding="utf-8")

                with mock.patch(
                    "app.crewai_self_upgrade_delivery._ensure_task_worktree",
                    return_value=(task_doc, repo_root, repo_root),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(summary="implemented"),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_review_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryReviewResult(
                        approved=False,
                        summary="review rejected",
                        feedback=["missing tests"],
                    ),
                ) as review_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._changed_files",
                    return_value=[],
                ):
                    out = crewai_self_upgrade_delivery.execute_task_delivery(
                        db=db,
                        actor="test",
                        ledger_path=ledger_path,
                        doc=task_doc,
                        dry_run=True,
                    )

                updated = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}

            self.assertFalse(out["ok"])
            self.assertEqual(out["status"], "blocked")
            self.assertEqual(updated["status"], "blocked")
            self.assertEqual((updated.get("self_upgrade_execution") or {}).get("stage"), "blocked")
            self.assertGreaterEqual(review_mock.call_count, 2)

    def test_migrate_legacy_worktrees_rehomes_paths_into_runtime_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            (repo_root / ".git").mkdir()
            legacy_root = repo_root / "templates" / "runtime" / "orchestrator" / "wt-bug-startup-fix"
            legacy_root.mkdir(parents=True, exist_ok=True)
            (legacy_root / ".git").write_text("gitdir: /tmp/demo\n", encoding="utf-8")
            (legacy_root / "demo.txt").write_text("hello\n", encoding="utf-8")
            task_doc = _base_task_doc(repo_root=repo_root, status="blocked")
            task_doc["repo"]["workdir"] = str(legacy_root)
            task_doc["repo"]["source_workdir"] = str(repo_root)
            task_doc["execution_policy"]["worktree_hint"] = "wt-bug-startup-fix"
            task_doc["self_upgrade"]["lane"] = "bug"
            task_doc["self_upgrade"]["work_item"]["title"] = "Startup fix"
            task_doc["self_upgrade"]["work_item"]["worktree_hint"] = "wt-bug-startup-fix"
            task_doc["self_upgrade_execution"] = {
                "worktree_path": str(legacy_root),
                "source_repo_root": str(repo_root),
                "branch_name": "codex/self-upgrade/teamos-1001",
                "base_branch": "main",
            }

            with mock.patch.dict(
                os.environ,
                {
                    "TEAMOS_RUNTIME_ROOT": str(runtime_root),
                    "TEAMOS_WORKSPACE_ROOT": str(workspace_root),
                },
                clear=False,
            ):
                task_dir = crewai_self_upgrade_delivery._task_ledger_dir("teamos")
                task_dir.mkdir(parents=True, exist_ok=True)
                ledger_path = task_dir / "TEAMOS-1001.yaml"
                ledger_path.write_text(yaml.safe_dump(task_doc, sort_keys=False), encoding="utf-8")
                moved_to = runtime_root / "workspace" / "worktrees" / "wt-bug-startup-fix"

                def _fake_run(cmd, cwd: Path, timeout_sec: int):
                    if len(cmd) >= 7 and cmd[0] == "git" and cmd[3] == "worktree" and cmd[4] == "move":
                        Path(cmd[5]).rename(Path(cmd[6]))
                        return {"returncode": 0, "stdout": "", "stderr": ""}
                    return {"returncode": 0, "stdout": "main\n", "stderr": ""}

                with mock.patch("app.crewai_self_upgrade_delivery._run", side_effect=_fake_run):
                    out = crewai_self_upgrade_delivery.migrate_legacy_worktrees(project_id="teamos")

                updated = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}

            self.assertTrue(out["ok"])
            self.assertEqual(out["moved"], 1)
            self.assertTrue((moved_to / ".git").exists())
            self.assertFalse(legacy_root.exists())
            self.assertEqual((updated.get("self_upgrade_execution") or {}).get("worktree_path"), str(moved_to.resolve()))
            self.assertEqual((updated.get("execution_policy") or {}).get("worktree_hint"), str(moved_to.resolve()))


if __name__ == "__main__":
    unittest.main()
