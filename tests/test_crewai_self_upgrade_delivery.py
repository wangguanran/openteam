import copy
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()
os.environ.setdefault("TEAMOS_SELF_UPGRADE_LOCALIZE_ZH", "0")

from app import crewai_self_upgrade_delivery  # noqa: E402
from app.runtime_db import RuntimeDB  # noqa: E402


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
    (repo_root / "src").mkdir(parents=True, exist_ok=True)
    (repo_root / "src" / "demo.py").write_text("print('demo')\n", encoding="utf-8")
    (repo_root / "tests").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests" / "test_demo.py").write_text("import unittest\n", encoding="utf-8")
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
            "documentation_role": "Documentation-Agent",
            "commit_message_template": f"{task_id}: demo",
        },
        "repo": {
            "workdir": str(repo_root),
            "locator": "foo/bar",
        },
        "links": {"issue": "https://github.com/foo/bar/issues/101"},
        "self_upgrade": {
            "kind": "BUG",
            "lane": "bug",
            "module": "Runtime",
            "summary": "Implement the demo change.",
            "rationale": "Validate delivery orchestration.",
            "work_item": {
                "tests": ["python -m unittest tests.test_demo"],
                "acceptance": ["Demo path is updated"],
                "reproduction_steps": ["运行 python -m unittest tests.test_demo，观察当前失败或异常。"],
                "test_case_files": ["tests/test_demo.py"],
                "verification_steps": ["修复后重新运行 python -m unittest tests.test_demo。"],
            },
        },
        "self_upgrade_audit": {
            "status": "pending",
            "classification": "bug",
            "closure": "pending",
            "worth_doing": True,
            "docs_required": False,
        },
        "documentation_policy": {
            "required": False,
            "status": "not_required",
            "allowed_paths": ["README.md", "docs"],
            "documentation_role": "Documentation-Agent",
        },
    }


class CrewAISelfUpgradeDeliveryTests(unittest.TestCase):
    @staticmethod
    def _fake_run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300) -> dict:
        text = " ".join(str(x) for x in cmd)
        if "unittest" in text or "pytest" in text or text.startswith("go test") or text.startswith("cargo test"):
            return {"returncode": 0, "stdout": "OK\n", "stderr": ""}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    def setUp(self) -> None:
        self._env_patch = mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_FILE_MIRROR": "1"}, clear=False)
        self._env_patch.start()
        self._sync_issue_patch = mock.patch(
            "app.crewai_self_upgrade_delivery.planning.sync_task_issue_from_doc",
            return_value={"ok": False, "reason": "disabled_for_test"},
        )
        self._sync_issue_patch.start()
        self._run_patch = mock.patch("app.crewai_self_upgrade_delivery._run", side_effect=self._fake_run)
        self._run_patch.start()
        self._bug_repro_patch = mock.patch(
            "app.crewai_self_upgrade_delivery._run_bug_repro_stage",
            return_value=crewai_self_upgrade_delivery.DeliveryBugReproResult(
                approved=True,
                reproduced=True,
                summary="bug repro ok",
                reproduction_commands=["python -m unittest tests.test_demo"],
                reproduction_evidence=[
                    {
                        "command": "python -m unittest tests.test_demo",
                        "ok": False,
                        "returncode": 1,
                        "stdout_tail": "FAIL\n",
                        "stderr_tail": "",
                        "captured_at": "2026-03-10T00:00:00Z",
                        "source_stage": "bug_repro",
                    }
                ],
            ),
        )
        self._bug_repro_patch.start()

    def tearDown(self) -> None:
        self._bug_repro_patch.stop()
        self._run_patch.stop()
        self._sync_issue_patch.stop()
        self._env_patch.stop()

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

    def test_normalize_audit_result_routes_bug_without_reproduction_contract_into_validation(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["self_upgrade"]["work_item"]["reproduction_steps"] = []
            task_doc["self_upgrade"]["work_item"]["test_case_files"] = []
            task_doc["self_upgrade"]["work_item"]["verification_steps"] = []

            result = crewai_self_upgrade_delivery._normalize_audit_result(
                task_doc=task_doc,
                worktree_root=repo_root,
                result=crewai_self_upgrade_delivery.DeliveryAuditResult(
                    approved=True,
                    classification="bug",
                    closure="ready",
                    worth_doing=True,
                    docs_required=False,
                    summary="审计通过",
                ),
                audit_evidence=[],
            )

        self.assertTrue(result.approved)
        self.assertEqual(result.closure, "ready")
        self.assertIn("缺少明确的 bug 复现路径/步骤。", result.feedback)
        self.assertIn("缺少 repo 内可定位的测试 case 脚本。", result.feedback)
        self.assertIn("Issue Audit Agent 未留下实际的 pre-fix 复现测试证据。", result.feedback)

    def test_run_issue_audit_stage_executes_bug_reproduction_command(self):
        def _audit_run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300) -> dict:
            text = " ".join(str(x) for x in cmd)
            if "status --short" in text:
                return {"returncode": 0, "stdout": "", "stderr": ""}
            if "unittest" in text:
                return {"returncode": 1, "stdout": "FAIL\n", "stderr": ""}
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            (repo_root / "tests").mkdir(parents=True, exist_ok=True)
            (repo_root / "tests" / "test_demo.py").write_text("import unittest\n", encoding="utf-8")
            task_doc = _base_task_doc(repo_root=repo_root)

            with mock.patch("app.crewai_self_upgrade_delivery.crewai_runtime.require_crewai_importable"), mock.patch(
                "app.crewai_self_upgrade_delivery.planning._crewai_llm",
                return_value=object(),
            ), mock.patch(
                "app.crewai_self_upgrade_delivery._build_repo_tools",
                return_value={"qa": [], "read": [], "write": []},
            ), mock.patch(
                "app.crewai_self_upgrade_delivery.crewai_agent_factory.build_crewai_agent",
                return_value=object(),
            ), mock.patch(
                "app.crewai_self_upgrade_delivery.crewai_task_registry.kickoff_registered_task",
                return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                    approved=True,
                    classification="bug",
                    closure="ready",
                    worth_doing=True,
                    docs_required=False,
                    summary="审计通过",
                    reproduction_steps=["执行 demo 入口并观察测试失败。"],
                    test_case_files=["tests/test_demo.py"],
                    reproduction_commands=["python -m unittest tests.test_demo"],
                    verification_steps=["修复后重新运行 python -m unittest tests.test_demo。"],
                    verification_commands=["python -m unittest tests.test_demo"],
                ),
            ), mock.patch(
                "app.crewai_self_upgrade_delivery._issue_snapshot",
                return_value={"number": 101, "url": "https://github.com/foo/bar/issues/101", "title": "demo bug", "body": "demo body", "state": "open", "labels": ["bug"]},
            ), mock.patch(
                "app.crewai_self_upgrade_delivery._run",
                side_effect=_audit_run,
            ):
                result = crewai_self_upgrade_delivery._run_issue_audit_stage(
                    task_doc=task_doc,
                    worktree_root=repo_root,
                    verbose=False,
                )

        self.assertTrue(result.approved)
        self.assertTrue(result.reproduced_in_audit)
        self.assertEqual(result.reproduction_commands, ["python -m unittest tests.test_demo"])
        self.assertEqual(result.test_case_files, ["tests/test_demo.py"])
        self.assertEqual(len(result.reproduction_evidence), 1)
        self.assertEqual(result.reproduction_evidence[0]["returncode"], 1)

    def test_run_issue_audit_stage_blocks_when_bug_is_not_reproduced(self):
        def _audit_run(cmd: list[str], *, cwd: Path, timeout_sec: int = 300) -> dict:
            text = " ".join(str(x) for x in cmd)
            if "status --short" in text:
                return {"returncode": 0, "stdout": "", "stderr": ""}
            if "unittest" in text:
                return {"returncode": 0, "stdout": "OK\n", "stderr": ""}
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            (repo_root / "tests").mkdir(parents=True, exist_ok=True)
            (repo_root / "tests" / "test_demo.py").write_text("import unittest\n", encoding="utf-8")
            task_doc = _base_task_doc(repo_root=repo_root)

            with mock.patch("app.crewai_self_upgrade_delivery.crewai_runtime.require_crewai_importable"), mock.patch(
                "app.crewai_self_upgrade_delivery.planning._crewai_llm",
                return_value=object(),
            ), mock.patch(
                "app.crewai_self_upgrade_delivery._build_repo_tools",
                return_value={"qa": [], "read": [], "write": []},
            ), mock.patch(
                "app.crewai_self_upgrade_delivery.crewai_agent_factory.build_crewai_agent",
                return_value=object(),
            ), mock.patch(
                "app.crewai_self_upgrade_delivery.crewai_task_registry.kickoff_registered_task",
                return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                    approved=True,
                    classification="bug",
                    closure="ready",
                    worth_doing=True,
                    docs_required=False,
                    summary="审计通过",
                    reproduction_steps=["执行 demo 入口并观察测试失败。"],
                    test_case_files=["tests/test_demo.py"],
                    reproduction_commands=["python -m unittest tests.test_demo"],
                    verification_steps=["修复后重新运行 python -m unittest tests.test_demo。"],
                ),
            ), mock.patch(
                "app.crewai_self_upgrade_delivery._issue_snapshot",
                return_value={"number": 101, "url": "https://github.com/foo/bar/issues/101", "title": "demo bug", "body": "demo body", "state": "open", "labels": ["bug"]},
            ), mock.patch(
                "app.crewai_self_upgrade_delivery._run",
                side_effect=_audit_run,
            ):
                result = crewai_self_upgrade_delivery._run_issue_audit_stage(
                    task_doc=task_doc,
                    worktree_root=repo_root,
                    verbose=False,
                )

        self.assertFalse(result.approved)
        self.assertEqual(result.closure, "rejected")
        self.assertIn("确认当前无法复现 bug", " ".join(result.feedback))

    def test_execute_task_delivery_closes_when_issue_audit_confirms_bug_not_reproducible(self):
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=False,
                        classification="bug",
                        closure="rejected",
                        worth_doing=False,
                        docs_required=False,
                        summary="确认当前无法复现，直接关闭。",
                        feedback=["Issue Audit Agent 已执行复现测试，确认当前无法复现 bug；问题可能已被其他提交或流程更新消除，将直接关闭。"],
                        reproduction_steps=["运行回归测试。"],
                        test_case_files=["tests/test_demo.py"],
                        reproduction_commands=["python -m unittest tests.test_demo"],
                        verification_steps=["无需修复，记录关闭原因。"],
                        reproduced_in_audit=False,
                        reproduction_evidence=[
                            {
                                "command": "python -m unittest tests.test_demo",
                                "ok": True,
                                "returncode": 0,
                                "stdout_tail": "OK\n",
                                "stderr_tail": "",
                                "captured_at": "2026-03-10T00:00:00Z",
                                "source_stage": "audit",
                            }
                        ],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    side_effect=AssertionError("coding should not start when bug is not reproducible"),
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
        self.assertEqual(out["reason"], "bug_not_reproducible")
        self.assertEqual(updated["status"], "closed")
        execution = updated.get("self_upgrade_execution") or {}
        self.assertEqual(execution.get("close_reason"), "bug_not_reproducible")

    def test_execute_task_delivery_bootstraps_bug_testcase_before_coding(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["self_upgrade"]["work_item"]["reproduction_steps"] = []
            task_doc["self_upgrade"]["work_item"]["test_case_files"] = []
            task_doc["self_upgrade"]["work_item"]["verification_steps"] = []
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=False,
                        summary="审计通过",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_bug_testcase_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryBugTestCaseResult(
                        approved=True,
                        summary="已补 failing test。",
                        changed_files=["tests/test_demo.py"],
                        reproduction_steps=["运行 python -m unittest tests.test_demo。"],
                        test_case_files=["tests/test_demo.py"],
                        reproduction_commands=["python -m unittest tests.test_demo"],
                        verification_steps=["修复后重新运行 python -m unittest tests.test_demo。"],
                        verification_commands=["python -m unittest tests.test_demo"],
                    ),
                ) as testcase_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_bug_repro_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryBugReproResult(
                        approved=True,
                        reproduced=True,
                        summary="bug 复现成功",
                        reproduction_commands=["python -m unittest tests.test_demo"],
                        reproduction_evidence=[
                            {
                                "command": "python -m unittest tests.test_demo",
                                "ok": False,
                                "returncode": 1,
                                "stdout_tail": "FAIL\n",
                                "stderr_tail": "",
                                "captured_at": "2026-03-10T00:00:00Z",
                                "source_stage": "bug_repro",
                            }
                        ],
                    ),
                ) as repro_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(
                        summary="implemented",
                        changed_files=["src/demo.py"],
                        tests_to_run=["python -m unittest tests.test_demo"],
                    ),
                ) as coding_mock, mock.patch(
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
                    return_value=["src/demo.py", "tests/test_demo.py"],
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
        self.assertEqual(testcase_mock.call_count, 1)
        self.assertEqual(repro_mock.call_count, 1)
        self.assertEqual(coding_mock.call_count, 1)
        work_item = ((updated.get("self_upgrade") or {}).get("work_item") or {})
        self.assertEqual(work_item.get("test_case_files"), ["tests/test_demo.py"])
        self.assertEqual(work_item.get("verification_steps"), ["修复后重新运行 python -m unittest tests.test_demo。"])

    def test_execute_task_delivery_closes_when_bug_testcase_bootstrap_fails(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["self_upgrade"]["work_item"]["reproduction_steps"] = []
            task_doc["self_upgrade"]["work_item"]["test_case_files"] = []
            task_doc["self_upgrade"]["work_item"]["verification_steps"] = []
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=False,
                        summary="审计通过",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_bug_testcase_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryBugTestCaseResult(
                        approved=False,
                        summary="无法补出稳定 failing test。",
                        feedback=["缺少足够上下文，无法构造可执行测试。"],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    side_effect=AssertionError("coding should not start when testcase bootstrap fails"),
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
        self.assertEqual(out["reason"], "bug_not_verifiable")
        self.assertEqual(updated["status"], "closed")
        self.assertEqual((updated.get("self_upgrade_execution") or {}).get("close_reason"), "bug_not_verifiable")

    def test_execute_task_delivery_closes_when_bug_repro_fails(self):
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=False,
                        summary="审计通过",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_bug_repro_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryBugReproResult(
                        approved=False,
                        reproduced=False,
                        summary="当前无法复现。",
                        feedback=["Bug-Repro-Agent 未能证明 bug 当前可复现，问题将直接关闭。"],
                        reproduction_commands=["python -m unittest tests.test_demo"],
                        reproduction_evidence=[
                            {
                                "command": "python -m unittest tests.test_demo",
                                "ok": True,
                                "returncode": 0,
                                "stdout_tail": "OK\n",
                                "stderr_tail": "",
                                "captured_at": "2026-03-10T00:00:00Z",
                                "source_stage": "bug_repro",
                            }
                        ],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    side_effect=AssertionError("coding should not start when bug repro fails"),
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
        self.assertEqual(out["reason"], "bug_not_reproducible")
        self.assertEqual(updated["status"], "closed")
        self.assertEqual((updated.get("self_upgrade_execution") or {}).get("close_reason"), "bug_not_reproducible")

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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=False,
                        summary="审计通过",
                    ),
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=False,
                        summary="审计通过",
                    ),
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

    def test_release_task_blocks_when_scope_has_out_of_scope_changes(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            ledger_path = Path(td) / "TEAMOS-1001.yaml"
            with mock.patch(
                "app.crewai_self_upgrade_delivery._changed_files",
                return_value=["src/demo.py", "scripts/rogue.py"],
            ):
                with self.assertRaises(crewai_self_upgrade_delivery.DeliveryError) as ctx:
                    crewai_self_upgrade_delivery._release_task(
                        task_doc=task_doc,
                        ledger_path=ledger_path,
                        worktree_root=repo_root,
                    )

            self.assertIn("out-of-scope changes present", str(ctx.exception))

    def test_execute_task_delivery_retries_after_release_merge_conflict(self):
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=False,
                        summary="审计通过",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(
                        summary="implemented",
                        changed_files=["src/demo.py"],
                        tests_to_run=["python -m unittest tests.test_demo"],
                    ),
                ) as coding_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_review_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryReviewResult(approved=True, summary="review ok"),
                ) as review_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_qa_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryQAResult(
                        approved=True,
                        summary="qa ok",
                        commands=["python -m unittest tests.test_demo"],
                    ),
                ) as qa_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._release_task",
                    side_effect=[
                        crewai_self_upgrade_delivery.DeliveryMergeConflictError(
                            "git push failed: non-fast-forward update was rejected"
                        ),
                        {
                            "branch": "codex/self-upgrade/teamos-1001",
                            "base_branch": "main",
                            "commit_sha": "abc123",
                            "pull_request_url": "https://github.com/foo/bar/pull/1",
                            "issue_url": "https://github.com/foo/bar/issues/101",
                            "staged_files": ["src/demo.py"],
                        },
                    ],
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._changed_files",
                    return_value=["src/demo.py"],
                ):
                    out = crewai_self_upgrade_delivery.execute_task_delivery(
                        db=db,
                        actor="test",
                        ledger_path=ledger_path,
                        doc=task_doc,
                        dry_run=False,
                    )

                updated = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}

            self.assertTrue(out["ok"])
            self.assertEqual(out["status"], "closed")
            self.assertEqual(coding_mock.call_count, 2)
            self.assertEqual(review_mock.call_count, 2)
            self.assertEqual(qa_mock.call_count, 2)
            self.assertEqual(updated["status"], "closed")
            execution = updated.get("self_upgrade_execution") or {}
            self.assertEqual(execution.get("merge_conflict_count"), 1)
            history = list(execution.get("history") or [])
            self.assertIn("merge_conflict", [str(x.get("stage") or "") for x in history])
            self.assertIn(
                "SELF_UPGRADE_TASK_DELIVERY_MERGE_CONFLICT",
                [str(e.get("event_type") or "") for e in db.events],
            )

    def test_execute_task_delivery_stops_when_feature_issue_audit_needs_clarification(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["self_upgrade"]["lane"] = "feature"
            task_doc["self_upgrade"]["kind"] = "FEATURE"
            task_doc["owner_role"] = "Feature-Coding-Agent"
            task_doc["owners"] = ["Feature-Coding-Agent"]
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=False,
                        classification="bug",
                        closure="needs_clarification",
                        worth_doing=True,
                        docs_required=True,
                        summary="Issue 描述还不闭环。",
                        feedback=["缺少复现步骤"],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    side_effect=AssertionError("coding should not start before audit passes"),
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
            self.assertEqual(out["status"], "needs_clarification")
            self.assertEqual(updated["status"], "needs_clarification")
            self.assertEqual((updated.get("self_upgrade_execution") or {}).get("stage"), "needs_clarification")
            audit = updated.get("self_upgrade_audit") or {}
            self.assertEqual(audit.get("closure"), "needs_clarification")
            self.assertTrue(audit.get("docs_required"))

    def test_execute_task_delivery_runs_documentation_stage_when_required(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["documentation_policy"]["required"] = True
            task_doc["documentation_policy"]["status"] = "pending"
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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=True,
                        summary="审计通过，需同步文档。",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(summary="implemented"),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_review_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryReviewResult(approved=True, summary="review ok"),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_qa_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryQAResult(approved=True, summary="qa ok"),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_documentation_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryDocumentationResult(
                        approved=True,
                        updated=True,
                        summary="README 已同步。",
                        changed_files=["README.md"],
                    ),
                ) as docs_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._changed_files",
                    return_value=["src/demo.py", "README.md"],
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
            self.assertEqual(docs_mock.call_count, 1)
            policy = updated.get("documentation_policy") or {}
            self.assertEqual(policy.get("status"), "done")
            self.assertEqual(policy.get("changed_files"), ["README.md"])

    def test_execute_task_delivery_persists_validation_evidence_for_docs_review_and_qa(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["documentation_policy"]["required"] = True
            task_doc["documentation_policy"]["status"] = "pending"
            docs_seen: list[dict] = []
            review_seen: list[dict] = []

            def _docs_side_effect(*, task_doc, worktree_root, verbose):
                docs_seen.append(copy.deepcopy((task_doc.get("self_upgrade_execution") or {}).get("validation_evidence") or {}))
                return crewai_self_upgrade_delivery.DeliveryDocumentationResult(
                    approved=True,
                    updated=True,
                    summary="README 已同步。",
                    changed_files=["README.md"],
                )

            def _review_side_effect(*, task_doc, worktree_root, verbose):
                review_seen.append(copy.deepcopy((task_doc.get("self_upgrade_execution") or {}).get("validation_evidence") or {}))
                return crewai_self_upgrade_delivery.DeliveryReviewResult(
                    approved=True,
                    code_approved=True,
                    docs_approved=True,
                    summary="review ok",
                )

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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=True,
                        summary="审计通过，需同步文档。",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(
                        summary="implemented",
                        changed_files=["src/demo.py"],
                        tests_to_run=["python -m unittest tests.test_demo"],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_documentation_stage",
                    side_effect=_docs_side_effect,
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_review_stage",
                    side_effect=_review_side_effect,
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_qa_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryQAResult(
                        approved=True,
                        summary="qa ok",
                        commands=["python -m unittest tests.test_demo"],
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._changed_files",
                    return_value=["src/demo.py", "README.md"],
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
            self.assertTrue(docs_seen)
            self.assertTrue(review_seen)
            self.assertIn("coding", docs_seen[0])
            self.assertEqual(docs_seen[0]["coding"][0]["command"], "python -m unittest tests.test_demo")
            self.assertTrue(docs_seen[0]["coding"][0]["ok"])
            self.assertIn("coding", review_seen[0])
            evidence = ((updated.get("self_upgrade_execution") or {}).get("validation_evidence") or {})
            self.assertIn("coding", evidence)
            self.assertIn("qa", evidence)
            self.assertEqual(evidence["qa"][0]["command"], "python -m unittest tests.test_demo")
            self.assertEqual(evidence["qa"][0]["stdout_tail"], "OK\n")

    def test_execute_task_delivery_retries_docs_without_rerunning_coding_when_review_rejects_docs(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["documentation_policy"]["required"] = True
            task_doc["documentation_policy"]["status"] = "pending"
            docs_statuses: list[str] = []

            def _docs_side_effect(*, task_doc, worktree_root, verbose):
                docs_statuses.append(str((task_doc.get("documentation_policy") or {}).get("status") or ""))
                return crewai_self_upgrade_delivery.DeliveryDocumentationResult(
                    approved=True,
                    updated=True,
                    summary="README 已同步。",
                    changed_files=["README.md"],
                )

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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=True,
                        summary="审计通过，需同步文档。",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(summary="implemented"),
                ) as coding_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_documentation_stage",
                    side_effect=_docs_side_effect,
                ) as docs_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_review_stage",
                    side_effect=[
                        crewai_self_upgrade_delivery.DeliveryReviewResult(
                            approved=False,
                            code_approved=True,
                            docs_approved=False,
                            summary="文档不完整",
                            docs_feedback=["README 缺少回滚说明"],
                        ),
                        crewai_self_upgrade_delivery.DeliveryReviewResult(
                            approved=True,
                            code_approved=True,
                            docs_approved=True,
                            summary="review ok",
                        ),
                    ],
                ) as review_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_qa_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryQAResult(approved=True, summary="qa ok"),
                ) as qa_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._changed_files",
                    return_value=["src/demo.py", "README.md"],
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
            self.assertEqual(coding_mock.call_count, 1)
            self.assertEqual(docs_mock.call_count, 2)
            self.assertEqual(review_mock.call_count, 2)
            self.assertEqual(qa_mock.call_count, 1)
            self.assertEqual(docs_statuses, ["pending", "pending"])
            self.assertEqual((updated.get("documentation_policy") or {}).get("status"), "done")

    def test_execute_task_delivery_reruns_docs_after_qa_sends_task_back_to_coding(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = _base_task_doc(repo_root=repo_root)
            task_doc["documentation_policy"]["required"] = True
            task_doc["documentation_policy"]["status"] = "pending"
            docs_statuses: list[str] = []

            def _docs_side_effect(*, task_doc, worktree_root, verbose):
                docs_statuses.append(str((task_doc.get("documentation_policy") or {}).get("status") or ""))
                return crewai_self_upgrade_delivery.DeliveryDocumentationResult(
                    approved=True,
                    updated=True,
                    summary="README 已同步。",
                    changed_files=["README.md"],
                )

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
                    "app.crewai_self_upgrade_delivery._run_issue_audit_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryAuditResult(
                        approved=True,
                        classification="bug",
                        closure="ready",
                        worth_doing=True,
                        docs_required=True,
                        summary="审计通过，需同步文档。",
                    ),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery._run_coding_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryImplementationResult(summary="implemented"),
                ) as coding_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_documentation_stage",
                    side_effect=_docs_side_effect,
                ) as docs_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_review_stage",
                    return_value=crewai_self_upgrade_delivery.DeliveryReviewResult(
                        approved=True,
                        code_approved=True,
                        docs_approved=True,
                        summary="review ok",
                    ),
                ) as review_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._run_qa_stage",
                    side_effect=[
                        crewai_self_upgrade_delivery.DeliveryQAResult(
                            approved=False,
                            summary="qa rejected",
                            failures=["missing regression coverage"],
                        ),
                        crewai_self_upgrade_delivery.DeliveryQAResult(
                            approved=True,
                            summary="qa ok",
                        ),
                    ],
                ) as qa_mock, mock.patch(
                    "app.crewai_self_upgrade_delivery._changed_files",
                    return_value=["src/demo.py", "README.md"],
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
            self.assertEqual(coding_mock.call_count, 2)
            self.assertEqual(docs_mock.call_count, 2)
            self.assertEqual(review_mock.call_count, 2)
            self.assertEqual(qa_mock.call_count, 2)
            self.assertEqual(docs_statuses, ["pending", "pending"])
            self.assertEqual((updated.get("documentation_policy") or {}).get("status"), "done")

    def test_migrate_legacy_worktrees_rehomes_paths_into_runtime_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            (repo_root / ".git").mkdir()
            legacy_root = repo_root / "scaffolds" / "runtime" / "orchestrator" / "wt-bug-startup-fix"
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

    def test_run_delivery_sweep_skips_task_leased_by_other_instance(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            db_path = runtime_root / "state" / "runtime.db"
            task_one = _base_task_doc(repo_root=repo_root, task_id="TEAMOS-1001", status="todo")
            task_two = _base_task_doc(repo_root=repo_root, task_id="TEAMOS-1002", status="todo")
            task_two["title"] = "Second task"

            with mock.patch.dict(
                os.environ,
                {
                    "TEAMOS_RUNTIME_ROOT": str(runtime_root),
                    "TEAMOS_WORKSPACE_ROOT": str(workspace_root),
                    "RUNTIME_DB_PATH": str(db_path),
                },
                clear=False,
            ):
                task_dir = crewai_self_upgrade_delivery._task_ledger_dir("teamos")
                task_dir.mkdir(parents=True, exist_ok=True)
                ledger_one = task_dir / "TEAMOS-1001.yaml"
                ledger_two = task_dir / "TEAMOS-1002.yaml"
                ledger_one.write_text(yaml.safe_dump(task_one, sort_keys=False), encoding="utf-8")
                ledger_two.write_text(yaml.safe_dump(task_two, sort_keys=False), encoding="utf-8")

                db = RuntimeDB(str(db_path))
                other_lease = db.claim_task_lease(
                    lease_scope="self_upgrade_delivery",
                    lease_key=crewai_self_upgrade_delivery._delivery_lease_key(project_id="teamos", task_id="TEAMOS-1001"),
                    project_id="teamos",
                    task_id="TEAMOS-1001",
                    holder_instance_id="node-a",
                    holder_actor="other-worker",
                    lease_ttl_sec=600,
                    holder_meta={"source": "test"},
                )
                self.assertIsNotNone(other_lease)

                with mock.patch(
                    "app.crewai_self_upgrade_delivery.execute_task_delivery",
                    return_value={"ok": True, "task_id": "TEAMOS-1002", "status": "closed", "project_id": "teamos"},
                ) as execute_mock:
                    out = crewai_self_upgrade_delivery.run_delivery_sweep(
                        db=db,
                        actor="test-worker",
                        project_id="teamos",
                        dry_run=True,
                        max_tasks=1,
                    )

                self.assertEqual(execute_mock.call_count, 1)
                called_path = Path(str(execute_mock.call_args.kwargs["ledger_path"]))
                self.assertEqual(called_path.name, "TEAMOS-1002.yaml")
                self.assertEqual(out["processed"], 1)
                self.assertEqual(out["scanned"], 2)
                skipped = [item for item in out["tasks"] if item.get("reason") == "lease_held_by_other"]
                self.assertEqual(len(skipped), 1)
                self.assertEqual(skipped[0]["task_id"], "TEAMOS-1001")
                self.assertIsNotNone(db.get_task_lease(lease_key=crewai_self_upgrade_delivery._delivery_lease_key(project_id="teamos", task_id="TEAMOS-1001")))
                self.assertIsNone(db.get_task_lease(lease_key=crewai_self_upgrade_delivery._delivery_lease_key(project_id="teamos", task_id="TEAMOS-1002")))

    def test_run_delivery_sweep_skips_bug_task_outside_active_window(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            workspace_root = Path(td) / "workspace"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            db_path = runtime_root / "state" / "runtime.db"
            task_doc = _base_task_doc(repo_root=repo_root, task_id="TEAMOS-2001", status="todo")

            with mock.patch.dict(
                os.environ,
                {
                    "TEAMOS_RUNTIME_ROOT": str(runtime_root),
                    "TEAMOS_WORKSPACE_ROOT": str(workspace_root),
                    "RUNTIME_DB_PATH": str(db_path),
                },
                clear=False,
            ):
                task_dir = crewai_self_upgrade_delivery._task_ledger_dir("teamos")
                task_dir.mkdir(parents=True, exist_ok=True)
                ledger_path = task_dir / "TEAMOS-2001.yaml"
                ledger_path.write_text(yaml.safe_dump(task_doc, sort_keys=False), encoding="utf-8")

                db = RuntimeDB(str(db_path))

                with mock.patch(
                    "app.crewai_self_upgrade_delivery.crewai_workflow_registry.project_config_store.load_project_config",
                    return_value={
                        "repo_improvement": {
                            "workflow_settings": {
                                "bug-fix": {
                                    "active_window_start_hour": 9,
                                    "active_window_end_hour": 18,
                                }
                            }
                        }
                    },
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery.crewai_workflow_registry._workflow_now_local",
                    return_value=dt.datetime(2026, 3, 11, 20, 0, tzinfo=dt.timezone.utc),
                ), mock.patch(
                    "app.crewai_self_upgrade_delivery.execute_task_delivery",
                    side_effect=AssertionError("outside active window must not execute delivery"),
                ):
                    out = crewai_self_upgrade_delivery.run_delivery_sweep(
                        db=db,
                        actor="test-worker",
                        project_id="teamos",
                        dry_run=True,
                        max_tasks=1,
                    )

                self.assertEqual(out["processed"], 0)
                self.assertEqual(out["scanned"], 1)
                self.assertEqual(out["tasks"][0]["reason"], "outside_active_window")
                self.assertEqual(out["tasks"][0]["workflow_id"], "bug-fix")


if __name__ == "__main__":
    unittest.main()
