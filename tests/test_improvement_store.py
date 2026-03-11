import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import improvement_store  # noqa: E402
from app import runtime_state_store  # noqa: E402


def _git(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *cmd], cwd=str(cwd), check=True, capture_output=True, text=True)


class ImprovementStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

    def _configure_runtime(self, root: str) -> None:
        os.environ["TEAMOS_RUNTIME_ROOT"] = root
        os.environ["TEAMOS_WORKSPACE_ROOT"] = str(Path(root) / "workspace")
        os.environ["RUNTIME_DB_PATH"] = str(Path(root) / "state" / "runtime.db")

    def test_runtime_state_and_docs_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._configure_runtime(td)

            runtime_state_store.put_state("ns", "key", {"value": 1, "name": "demo"})
            loaded = runtime_state_store.get_state("ns", "key")
            self.assertEqual(loaded["value"], 1)

            runtime_state_store.put_doc(
                "docns",
                "doc-1",
                project_id="demo",
                scope_id="target-a",
                state="open",
                category="feature",
                value={"id": "doc-1", "project_id": "demo", "status": "open", "lane": "feature"},
            )
            docs = runtime_state_store.list_docs("docns", project_id="demo", scope_id="target-a")
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["id"], "doc-1")

    def test_improvement_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._configure_runtime(td)

            target = improvement_store.upsert_target(
                {
                    "target_id": "demo-target",
                    "project_id": "demo",
                    "display_name": "Demo Target",
                    "repo_root": "/tmp/demo-repo",
                    "repo_locator": "owner/demo",
                    "enabled": True,
                }
            )
            self.assertEqual(target["target_id"], "demo-target")

            improvement_store.save_target_state("demo-target", {"backoff_until": "2026-03-07T00:00:00Z"})
            improvement_store.append_target_history("demo-target", {"status": "DONE", "run_id": "run-1"})
            state = improvement_store.load_target_state("demo-target")
            self.assertEqual(state["backoff_until"], "2026-03-07T00:00:00Z")
            self.assertEqual(len(state["history"]), 1)

            proposal = improvement_store.upsert_proposal(
                {
                    "proposal_id": "p-1",
                    "project_id": "demo",
                    "target_id": "demo-target",
                    "lane": "feature",
                    "status": "PENDING_CONFIRMATION",
                    "title": "Add feature",
                }
            )
            self.assertEqual(improvement_store.get_proposal("p-1")["title"], "Add feature")
            self.assertEqual(len(improvement_store.list_proposals(target_id="demo-target")), 1)

            task = improvement_store.upsert_delivery_task(
                {
                    "id": "TASK-1",
                    "project_id": "demo",
                    "title": "Fix runtime issue",
                    "status": "todo",
                    "target": {"target_id": "demo-target"},
                    "repo": {"locator": "owner/demo", "workdir": "/tmp/demo-repo"},
                    "orchestration": {"flow": "self_upgrade", "finding_lane": "bug"},
                    "self_upgrade": {"lane": "bug"},
                }
            )
            self.assertEqual(task["target_id"], "demo-target")
            self.assertEqual(len(improvement_store.list_delivery_tasks(target_id="demo-target")), 1)

            milestone = improvement_store.upsert_milestone(
                {
                    "milestone_id": "v0.1.1",
                    "project_id": "demo",
                    "target_id": "demo-target",
                    "title": "v0.1.1",
                    "state": "active",
                }
            )
            self.assertEqual(milestone["target_id"], "demo-target")
            self.assertEqual(len(improvement_store.list_milestones(target_id="demo-target", project_id="demo")), 1)

            report = improvement_store.save_report(target_id="demo-target", project_id="demo", report={"run_id": "run-1", "ok": True})
            self.assertEqual(report["run_id"], "run-1")
            self.assertEqual(improvement_store.get_report("run-1")["run_id"], "run-1")
            self.assertEqual(len(improvement_store.list_reports(target_id="demo-target", project_id="demo")), 1)

    def test_materialize_target_repo_clones_remote_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self._configure_runtime(td)

            origin = Path(td) / "origin"
            origin.mkdir(parents=True, exist_ok=True)
            _git(["init"], cwd=origin)
            _git(["config", "user.name", "Team OS"], cwd=origin)
            _git(["config", "user.email", "team-os@example.com"], cwd=origin)
            (origin / "README.md").write_text("# demo\n", encoding="utf-8")
            _git(["add", "README.md"], cwd=origin)
            _git(["commit", "-m", "init"], cwd=origin)

            target = improvement_store.upsert_target(
                {
                    "target_id": "demo-target",
                    "project_id": "demo",
                    "display_name": "Demo Target",
                    "repo_url": origin.as_uri(),
                    "enabled": True,
                }
            )
            materialized = improvement_store.materialize_target_repo(target, fetch=False)
            repo_root = Path(materialized["repo_root"])

            self.assertTrue((repo_root / ".git").exists())
            self.assertEqual(materialized["target_id"], "demo-target")
            expected_root = (Path(td) / "workspace" / "targets" / "demo-target").resolve()
            self.assertTrue(str(repo_root).startswith(str(expected_root)))
            self.assertEqual(improvement_store.get_target("demo-target")["repo_root"], str(repo_root))


if __name__ == "__main__":
    unittest.main()
