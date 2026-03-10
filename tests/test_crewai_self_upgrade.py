import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()
os.environ.setdefault("TEAMOS_SELF_UPGRADE_LOCALIZE_ZH", "0")

from app import crewai_self_upgrade, improvement_store, plan_store  # noqa: E402


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


class CrewAISelfUpgradeTests(unittest.TestCase):
    def test_parse_repo_locator_supports_https_and_ssh(self):
        self.assertEqual(crewai_self_upgrade._parse_repo_locator("https://github.com/foo/bar.git"), "foo/bar")
        self.assertEqual(crewai_self_upgrade._parse_repo_locator("git@github.com:foo/bar.git"), "foo/bar")

    def test_collect_repo_context_marks_dirty_repo_when_git_status_has_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            fake_proc = SimpleNamespace(returncode=0, stdout=" M src/demo.py\n", stderr="")
            with mock.patch("app.crewai_self_upgrade.subprocess.run", return_value=fake_proc):
                ctx = crewai_self_upgrade.collect_repo_context(repo_root=repo)

            self.assertTrue(ctx["git_status_dirty"])
            self.assertEqual(ctx["git_status_sample"], [" M src/demo.py"])

    def test_crewai_llm_matches_codex_oauth_demo_defaults(self):
        captured: dict[str, object] = {}

        class FakeLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_crewai_pkg = ModuleType("crewai")
        fake_crewai_llm = ModuleType("crewai.llm")
        fake_crewai_llm.LLM = FakeLLM

        with mock.patch("app.crewai_self_upgrade.crewai_runtime.require_crewai_importable", return_value={"importable": True}), mock.patch(
            "app.crewai_self_upgrade.codex_llm.codex_login_status",
            return_value=(True, "Logged in using ChatGPT"),
        ), mock.patch.dict(
            sys.modules,
            {"crewai": fake_crewai_pkg, "crewai.llm": fake_crewai_llm},
            clear=False,
        ), mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "sk-test",
                "TEAMOS_LLM_BASE_URL": "https://api.openai.com/v1",
            },
            clear=True,
        ):
            crewai_self_upgrade._crewai_llm()

        self.assertEqual(captured["model"], "openai-codex/gpt-5.3-codex")
        self.assertEqual(captured["api"], "responses")
        self.assertEqual(captured["is_litellm"], False)
        self.assertEqual(captured["max_tokens"], 4000)
        self.assertNotIn("api_key", captured)
        self.assertNotIn("base_url", captured)

    def test_run_self_upgrade_dry_run_skips_materialized_records(self):
        db = _FakeDB()
        plan = crewai_self_upgrade.UpgradePlan(
            summary="planned",
            findings=[
                crewai_self_upgrade.UpgradeFinding(
                    kind="CI",
                    title="Add runtime CI",
                    summary="Missing automated tests in GitHub Actions.",
                    workstream_id="general",
                    tests=["python -m unittest tests.test_crewai_orchestrator"],
                )
            ],
            ci_actions=["Add GitHub Actions workflow"],
        )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=True,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": True, "stats": {"created": 1}},
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("dry_run should not create issues"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("dry_run should not create tasks"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-1",
                        crewai_info={"importable": True},
                    )

                self.assertTrue(out["ok"])
                self.assertTrue(out["dry_run"])
                self.assertEqual(out["records"][0]["workflow_id"], "bug-fix")
                self.assertEqual(out["records"][0]["task_id"], "")
                self.assertEqual(out["records"][0]["issue_url"], "")
                state = crewai_self_upgrade._read_state(out["target_id"])
                self.assertEqual((state.get("last_run") or {}).get("status"), "DONE")

    def test_decide_proposal_updates_version_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                improvement_store.upsert_proposal(
                    {
                        "proposal_id": "su-feature-demo",
                        "project_id": "teamos",
                        "target_id": "teamos",
                        "lane": "feature",
                        "title": "Improve onboarding",
                        "summary": "Ship a new onboarding dashboard.",
                        "status": "PENDING_CONFIRMATION",
                        "current_version": "1.2.3",
                        "version_bump": "minor",
                        "target_version": "1.3.0",
                        "baseline_action": "new_baseline",
                    }
                )
                out = crewai_self_upgrade.decide_proposal(
                    proposal_id="su-feature-demo",
                    action="approve",
                    version_bump="major",
                )

            self.assertEqual(out["status"], "APPROVED")
            self.assertEqual(out["version_bump"], "major")
            self.assertEqual(out["target_version"], "2.0.0")
            self.assertEqual(out["baseline_action"], "new_baseline")

    def test_normalize_worktree_hint_rehomes_relative_and_legacy_paths(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                relative = crewai_self_upgrade._normalize_worktree_hint(
                    repo_root=repo_root,
                    lane="bug",
                    title="Startup fix",
                    raw_hint="upgrade/bug-entrypoint-wiring",
                )
                legacy = crewai_self_upgrade._normalize_worktree_hint(
                    repo_root=repo_root,
                    lane="bug",
                    title="Startup fix",
                    raw_hint=str(repo_root / "scaffolds" / "runtime" / "orchestrator" / "wt-bug-startup-fix"),
                )

            self.assertEqual(
                relative,
                str((runtime_root / "workspace" / "worktrees" / "upgrade" / "bug-entrypoint-wiring").resolve()),
            )
            self.assertEqual(
                legacy,
                str((runtime_root / "workspace" / "worktrees" / "wt-bug-startup-fix").resolve()),
            )

    def test_run_self_upgrade_feature_finding_creates_pending_proposal(self):
        db = _FakeDB()
        plan = crewai_self_upgrade.UpgradePlan(
            summary="feature pending",
            findings=[
                crewai_self_upgrade.UpgradeFinding(
                    kind="FEATURE",
                    lane="feature",
                    title="Add team dashboard",
                    summary="Introduce a dashboard for team health.",
                    workstream_id="general",
                    version_bump="minor",
                    target_version="0.2.0",
                    requires_user_confirmation=True,
                    cooldown_hours=1,
                    work_items=[
                        crewai_self_upgrade.UpgradeWorkItem(
                            title="Build dashboard UI",
                            summary="Implement the first dashboard screen.",
                            owner_role="Feature-Coding-Agent",
                            review_role="Review-Agent",
                            qa_role="QA-Agent",
                            workstream_id="general",
                            allowed_paths=["src/dashboard.py"],
                            tests=["python -m unittest tests.test_dashboard"],
                            acceptance=["Dashboard renders key team metrics"],
                            worktree_hint="/tmp/worktrees/dashboard",
                        )
                    ],
                )
            ],
        )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=False,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_proposal_discussion_issue",
                    side_effect=lambda proposal: {**proposal, "discussion_issue_number": 12, "discussion_issue_url": "https://example.com/issues/12"},
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": False, "stats": {"updated": 0}},
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("feature proposals must not materialize before approval"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("feature proposals must not materialize before approval"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-feature-pending",
                        crewai_info={"importable": True},
                    )

                self.assertTrue(out["ok"])
                self.assertEqual(out["records"], [])
                self.assertEqual(len(out["pending_proposals"]), 1)
                self.assertEqual(out["pending_proposals"][0]["workflow_id"], "feature-improvement")
                self.assertEqual(out["pending_proposals"][0]["status"], "PENDING_CONFIRMATION")
                self.assertEqual(out["pending_proposals"][0]["discussion_issue_url"], "https://example.com/issues/12")
                proposals = improvement_store.list_proposals(target_id=out["target_id"])
                self.assertEqual(len(proposals), 1)
                self.assertEqual(proposals[0].get("workflow_id"), "feature-improvement")

    def test_coerce_plan_limits_feature_findings_to_workflow_cap(self):
        feature_findings = [
            crewai_self_upgrade.UpgradeFinding(
                kind="FEATURE",
                lane="feature",
                title=f"Feature {idx}",
                summary=f"Summary {idx}",
                workstream_id="general",
                version_bump="minor",
                target_version="0.2.0",
                requires_user_confirmation=True,
                work_items=[
                    crewai_self_upgrade.UpgradeWorkItem(
                        title=f"Feature work item {idx}",
                        summary="Implement feature candidate",
                        owner_role="Feature-Coding-Agent",
                        review_role="Review-Agent",
                        qa_role="QA-Agent",
                        workstream_id="general",
                        allowed_paths=[f"src/feature_{idx}.py"],
                        tests=[f"python -m unittest tests.test_feature_{idx}"],
                        acceptance=[f"Feature {idx} is available"],
                        worktree_hint=f"/tmp/worktrees/feature-{idx}",
                    )
                ],
            )
            for idx in range(6)
        ]
        bug_finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="Bug 1",
            summary="Fix bug",
            workstream_id="general",
            version_bump="patch",
            target_version="0.1.1",
            work_items=[
                crewai_self_upgrade.UpgradeWorkItem(
                    title="Bug work item",
                    summary="Fix bug",
                    owner_role="Bugfix-Coding-Agent",
                    review_role="Review-Agent",
                    qa_role="QA-Agent",
                    workstream_id="general",
                    allowed_paths=["src/bug.py"],
                    tests=["python -m unittest tests.test_bug"],
                    acceptance=["Bug fixed"],
                    worktree_hint="/tmp/worktrees/bug-1",
                )
            ],
        )
        plan = crewai_self_upgrade.UpgradePlan(summary="capped", findings=[*feature_findings, bug_finding])

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            normalized = crewai_self_upgrade._coerce_plan(
                SimpleNamespace(to_dict=lambda: plan.model_dump()),
                max_findings=10,
                repo_root=repo_root,
                current_version="0.1.0",
                project_id="teamos",
            )

        feature_titles = [item.title for item in normalized.findings if item.lane == "feature"]
        bug_titles = [item.title for item in normalized.findings if item.lane == "bug"]
        self.assertEqual(len(feature_titles), 5)
        self.assertEqual(bug_titles, ["Bug 1"])

    def test_run_self_upgrade_approved_feature_materializes_after_cooldown(self):
        db = _FakeDB()
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="FEATURE",
            lane="feature",
            title="Add team dashboard",
            summary="Introduce a dashboard for team health.",
            workstream_id="general",
            version_bump="minor",
            target_version="0.2.0",
            requires_user_confirmation=True,
            cooldown_hours=1,
            work_items=[
                crewai_self_upgrade.UpgradeWorkItem(
                    title="Build dashboard UI",
                    summary="Implement the first dashboard screen.",
                    owner_role="Feature-Coding-Agent",
                    review_role="Review-Agent",
                    qa_role="QA-Agent",
                    workstream_id="general",
                    allowed_paths=["src/dashboard.py"],
                    tests=["python -m unittest tests.test_dashboard"],
                    acceptance=["Dashboard renders key team metrics"],
                    worktree_hint="/tmp/worktrees/dashboard",
                )
            ],
        )
        plan = crewai_self_upgrade.UpgradePlan(summary="feature approved", findings=[finding])

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=False,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                proposal = crewai_self_upgrade._upsert_proposal(
                    target_id="teamos",
                    repo_root=repo,
                    repo_locator="foo/bar",
                    project_id="teamos",
                    finding=finding,
                    current_version="0.1.0",
                )
                crewai_self_upgrade.decide_proposal(proposal_id=str(proposal["proposal_id"]), action="approve")
                current = improvement_store.get_proposal(str(proposal["proposal_id"]))
                assert current is not None
                current["cooldown_until"] = "2026-01-01T00:00:00Z"
                improvement_store.upsert_proposal(current)

                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": False, "stats": {"updated": 1}},
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    return_value=crewai_self_upgrade._IssueRecord(title="issue", url="https://example.com/1"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    return_value={"task_id": "TEAMOS-1234", "ledger_path": "/tmp/task.yaml"},
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-feature-approved",
                        crewai_info={"importable": True},
                    )

                updated = improvement_store.get_proposal(str(proposal["proposal_id"]))

            self.assertTrue(out["ok"])
            self.assertEqual(len(out["records"]), 1)
            self.assertEqual(out["records"][0]["task_id"], "TEAMOS-1234")
            self.assertEqual((updated or {})["status"], "MATERIALIZED")

    def test_run_self_upgrade_skips_disabled_feature_workflow(self):
        db = _FakeDB()
        plan = crewai_self_upgrade.UpgradePlan(
            summary="feature disabled",
            findings=[
                crewai_self_upgrade.UpgradeFinding(
                    kind="FEATURE",
                    lane="feature",
                    title="Add team dashboard",
                    summary="Introduce a dashboard for team health.",
                    workstream_id="general",
                    version_bump="minor",
                    target_version="0.2.0",
                    requires_user_confirmation=True,
                )
            ],
        )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=False,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": False, "stats": {"updated": 0}},
                ), mock.patch(
                    "app.crewai_self_upgrade.crewai_workflow_registry.project_config_store.load_project_config",
                    return_value={
                        "repo_improvement": {
                            "workflow_settings": {
                                "feature-improvement": {
                                    "enabled": False,
                                    "disabled_reason": "disabled_for_repo",
                                }
                            }
                        }
                    },
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_proposal_discussion_issue",
                    side_effect=AssertionError("disabled feature workflow must not create proposal discussion"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("disabled feature workflow must not create issues"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("disabled feature workflow must not create tasks"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-feature-disabled",
                        crewai_info={"importable": True},
                    )

            self.assertTrue(out["ok"])
            self.assertEqual(out["records"], [])
            self.assertEqual(out["pending_proposals"], [])
            self.assertEqual(improvement_store.list_proposals(target_id=out["target_id"]), [])
            skipped = [event for event in db.events if event.get("event_type") == "SELF_UPGRADE_FINDING_SKIPPED"]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["payload"]["workflow_id"], "feature-improvement")
            self.assertEqual(skipped[0]["payload"]["reason"], "disabled_for_repo")

    def test_reconcile_feature_discussions_approves_from_issue_comment(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                improvement_store.upsert_proposal(
                    {
                        "proposal_id": "su-feature-demo",
                        "project_id": "teamos",
                        "target_id": "teamos",
                        "lane": "feature",
                        "title": "Improve onboarding",
                        "summary": "Ship a new onboarding dashboard.",
                        "status": "PENDING_CONFIRMATION",
                        "current_version": "1.2.3",
                        "version_bump": "minor",
                        "target_version": "1.3.0",
                        "baseline_action": "new_baseline",
                        "repo_locator": "foo/bar",
                        "workstream_id": "general",
                        "discussion_issue_number": 12,
                        "discussion_issue_url": "https://example.com/issues/12",
                        "discussion_last_comment_id": 0,
                    }
                )
                comment = SimpleNamespace(
                    id=101,
                    body="/approve",
                    user_login="wangguanran",
                    created_at="2026-03-06T06:35:00Z",
                    updated_at="2026-03-06T06:35:00Z",
                )
                with mock.patch(
                    "app.crewai_self_upgrade.list_issue_comments",
                    return_value=[comment],
                ), mock.patch(
                    "app.crewai_self_upgrade.update_issue",
                    return_value=SimpleNamespace(number=12, url="https://example.com/issues/12", title="t", body="b"),
                ), mock.patch(
                    "app.crewai_self_upgrade.upsert_comment_with_marker",
                    return_value=SimpleNamespace(id=201, url="https://example.com/issues/12#issuecomment-201", body="ok"),
                ), mock.patch(
                    "app.crewai_self_upgrade.kickoff_proposal_discussion",
                    return_value=crewai_self_upgrade.ProposalDiscussionResponse(reply_body="approved", action="pending"),
                ):
                    out = crewai_self_upgrade.reconcile_feature_discussions(db=db, actor="test")

                updated = improvement_store.get_proposal("su-feature-demo")

            self.assertEqual(out["updated"], 1)
            self.assertEqual(out["replied"], 1)
            self.assertEqual((updated or {})["status"], "APPROVED")
            self.assertEqual((updated or {})["discussion_last_comment_id"], 101)
            self.assertFalse((updated or {})["awaiting_user_reply"])

    def test_proposal_issue_template_is_chinese(self):
        doc = {
            "proposal_id": "su-feature-demo",
            "repo_root": "/tmp/team-os",
            "repo_locator": "foo/bar",
            "module": "Runtime",
            "status": "PENDING_CONFIRMATION",
            "version_bump": "minor",
            "target_version": "0.2.0",
            "cooldown_until": "2026-03-07T02:00:00Z",
            "title": "运行时启动预检",
            "summary": "在开发前先完成运行时启动预检。",
            "rationale": "这样可以减少启动回归。",
            "work_items": [
                {
                    "title": "补齐启动预检命令",
                    "owner_role": crewai_self_upgrade.ROLE_FEATURE_CODING_AGENT,
                }
            ],
        }

        title = crewai_self_upgrade._proposal_issue_title(doc)
        body = crewai_self_upgrade._proposal_issue_body(doc)
        labels = crewai_self_upgrade._proposal_issue_labels(doc)

        self.assertEqual(title, "[Feature][Runtime] 运行时启动预检")
        self.assertIn("# 改进提案讨论", body)
        self.assertIn("## 如何回复", body)
        self.assertIn("功能编码 Agent", body)
        self.assertIn("- Module: Runtime", body)
        self.assertEqual(
            labels,
            [
                "module:runtime",
                "proposal:pending-confirmation",
                "source:self-upgrade",
                "stage:proposal",
                "teamos",
                "type:feature",
                "version:minor",
            ],
        )

    def test_quality_lane_uses_quality_titles_and_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                finding = crewai_self_upgrade.UpgradeFinding(
                    kind="CODE_QUALITY",
                    lane="quality",
                    title="删除未引用的旧启动适配层",
                    summary="清理当前已经没有调用路径的旧适配文件，并抽取公共启动检查逻辑。",
                    module="Runtime",
                    rationale="旧适配层已经和当前入口脱节，继续保留会增加维护成本。",
                    impact="MED",
                    workstream_id="general",
                    files=["scaffolds/runtime/orchestrator/app/main.py", "scaffolds/runtime/orchestrator/app/legacy_startup.py"],
                    version_bump="none",
                    target_version="0.1.0",
                )
                normalized = crewai_self_upgrade._coerce_plan(
                    SimpleNamespace(to_dict=lambda: crewai_self_upgrade.UpgradePlan(summary="quality", findings=[finding]).model_dump()),
                    max_findings=3,
                    repo_root=Path("/tmp/team-os"),
                    current_version="0.1.0",
                )
                proposal = crewai_self_upgrade._upsert_proposal(
                    target_id="teamos",
                    repo_root=Path("/tmp/team-os"),
                    repo_locator="foo/bar",
                    project_id="teamos",
                    finding=normalized.findings[0],
                    current_version="0.1.0",
                )

            self.assertEqual(normalized.findings[0].kind, "CODE_QUALITY")
            self.assertEqual(normalized.findings[0].lane, "quality")
            self.assertTrue(normalized.findings[0].requires_user_confirmation)
            self.assertEqual(normalized.findings[0].version_bump, "none")
            self.assertEqual(normalized.findings[0].work_items[0].owner_role, crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT)
            self.assertEqual(crewai_self_upgrade._proposal_issue_title(proposal), "[Quality][Runtime] 删除未引用的旧启动适配层")
            self.assertIn("type:quality", crewai_self_upgrade._proposal_issue_labels(proposal))

    def test_quality_test_gap_metadata_survives_plan_normalization(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="CODE_QUALITY",
            lane="quality",
            title="补齐 runtime 健康检查黑盒覆盖",
            summary="当前 /healthz 用户路径缺少集成级回归覆盖。",
            module="Runtime",
            rationale="没有黑盒覆盖时，启动与健康检查回归容易漏掉。",
            impact="MED",
            workstream_id="general",
            files=["scaffolds/runtime/orchestrator/app/main.py", "tests/test_runtime_health.py"],
            tests=["python -m unittest tests.test_runtime_health"],
            acceptance=["新增集成测试能覆盖 /healthz 行为"],
            test_gap_type="blackbox",
            target_paths=["scaffolds/runtime/orchestrator/app/main.py"],
            missing_paths=["/healthz startup path"],
            suggested_test_files=["tests/test_runtime_health.py"],
            why_not_covered="当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
            version_bump="none",
            target_version="0.1.0",
            work_items=[
                crewai_self_upgrade.UpgradeWorkItem(
                    title="补齐 /healthz 黑盒回归",
                    summary="新增端到端测试覆盖运行时健康检查。",
                    owner_role=crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT,
                    review_role=crewai_self_upgrade.ROLE_REVIEW_AGENT,
                    qa_role=crewai_self_upgrade.ROLE_QA_AGENT,
                    workstream_id="general",
                    allowed_paths=["tests/test_runtime_health.py", "scaffolds/runtime/orchestrator/app/main.py"],
                    tests=["python -m unittest tests.test_runtime_health"],
                    acceptance=["新增集成测试能覆盖 /healthz 行为"],
                    test_gap_type="blackbox",
                    target_paths=["scaffolds/runtime/orchestrator/app/main.py"],
                    missing_paths=["/healthz startup path"],
                    suggested_test_files=["tests/test_runtime_health.py"],
                    why_not_covered="当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
                    worktree_hint="/tmp/worktrees/runtime-health-gap",
                    module="Runtime",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            normalized = crewai_self_upgrade._coerce_plan(
                SimpleNamespace(to_dict=lambda: crewai_self_upgrade.UpgradePlan(summary="quality", findings=[finding]).model_dump()),
                max_findings=3,
                repo_root=repo_root,
                current_version="0.1.0",
            )

        normalized_finding = normalized.findings[0]
        normalized_item = normalized_finding.work_items[0]
        self.assertEqual(normalized_finding.test_gap_type, "blackbox")
        self.assertEqual(normalized_finding.target_paths, ["scaffolds/runtime/orchestrator/app/main.py"])
        self.assertEqual(normalized_finding.missing_paths, ["/healthz startup path"])
        self.assertEqual(normalized_finding.suggested_test_files, ["tests/test_runtime_health.py"])
        self.assertIn("黑盒回归", normalized_finding.why_not_covered)
        self.assertEqual(normalized_item.test_gap_type, "blackbox")
        self.assertEqual(normalized_item.suggested_test_files, ["tests/test_runtime_health.py"])
        self.assertEqual(normalized_item.owner_role, crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT)

    def test_quality_test_gap_proposal_issue_includes_gap_metadata(self):
        doc = {
            "proposal_id": "su-quality-gap",
            "repo_root": "/tmp/team-os",
            "repo_locator": "foo/bar",
            "lane": "quality",
            "module": "Runtime",
            "status": "PENDING_CONFIRMATION",
            "version_bump": "none",
            "target_version": "0.1.0",
            "title": "补齐 runtime 健康检查黑盒覆盖",
            "summary": "当前 /healthz 用户路径缺少集成级回归覆盖。",
            "rationale": "没有黑盒覆盖时，启动与健康检查回归容易漏掉。",
            "test_gap_type": "blackbox",
            "target_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
            "missing_paths": ["/healthz startup path"],
            "suggested_test_files": ["tests/test_runtime_health.py"],
            "why_not_covered": "当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
            "work_items": [
                {
                    "title": "补齐 /healthz 黑盒回归",
                    "owner_role": crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT,
                }
            ],
        }

        body = crewai_self_upgrade._proposal_issue_body(doc)
        labels = crewai_self_upgrade._proposal_issue_labels(doc)

        self.assertIn("## 测试缺口分析", body)
        self.assertIn("- 测试缺口类型: blackbox", body)
        self.assertIn("- 建议测试文件: tests/test_runtime_health.py", body)
        self.assertIn("未覆盖原因", body)
        self.assertIn("test-gap:blackbox", labels)

    def test_task_issue_template_is_chinese(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="修复启动导入回归",
            summary="修复启动路径中的导入回归。",
            module="Runtime",
            rationale="当前启动链路仍可能引用旧模块。",
            impact="HIGH",
            workstream_id="general",
            files=["scaffolds/runtime/orchestrator/app/main.py"],
            tests=["python -m unittest tests.test_crewai_runtime"],
            acceptance=["启动后 /healthz 返回 ok"],
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(
            title="清理旧导入引用",
            summary="移除旧 self_improve runner 引用。",
            owner_role=crewai_self_upgrade.ROLE_BUGFIX_CODING_AGENT,
            review_role=crewai_self_upgrade.ROLE_REVIEW_AGENT,
            qa_role=crewai_self_upgrade.ROLE_QA_AGENT,
            allowed_paths=["scaffolds/runtime/orchestrator/app/main.py"],
            tests=["python -m unittest tests.test_crewai_runtime"],
            acceptance=["启动后 /healthz 返回 ok"],
            worktree_hint="/tmp/wt-bug",
            module="Runtime",
        )

        title = crewai_self_upgrade._issue_title_for_work_item("team-os", finding, item)
        body = crewai_self_upgrade._issue_body(
            repo_root=Path("/tmp/team-os"),
            repo_locator="foo/bar",
            finding=finding,
            work_item=item,
            fingerprint="demo-fp",
            marker="<!-- teamos:self_upgrade:demo-fp-runtime-cleanup -->",
            doc={
                "self_upgrade_audit": {
                    "status": "approved",
                    "classification": "bug",
                    "closure": "ready",
                    "worth_doing": True,
                    "docs_required": True,
                    "summary": "问题闭环，可以进入开发。",
                    "feedback": [],
                },
                "documentation_policy": {
                    "required": True,
                    "status": "pending",
                    "documentation_role": crewai_self_upgrade.ROLE_DOCUMENTATION_AGENT,
                    "allowed_paths": ["README.md", "docs"],
                    "rationale": "运行时行为变更需要同步说明文档。",
                },
            },
        )

        self.assertEqual(title, "[Bug][Runtime] 清理旧导入引用")
        self.assertIn("<!-- teamos:self_upgrade:demo-fp-runtime-cleanup -->", body)
        self.assertIn("# 自升级任务", body)
        self.assertIn("- Module: Runtime", body)
        self.assertIn("- GitHub Milestone: v0.1.1", body)
        self.assertIn("## 范围外", body)
        self.assertIn("## 审计状态", body)
        self.assertIn("## 文档同步", body)
        self.assertIn("## 版本与里程碑", body)
        self.assertIn("问题审计 Agent", body)
        self.assertIn("文档同步 Agent", body)
        self.assertIn("里程碑经理 Agent", body)
        self.assertIn("## 执行约束", body)
        self.assertIn("缺陷修复 Agent", body)

    def test_build_milestone_doc_assigns_patch_release_line(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                finding = crewai_self_upgrade.UpgradeFinding(
                    kind="BUG",
                    lane="bug",
                    title="修复启动导入回归",
                    summary="修复启动路径中的导入回归。",
                    module="Runtime",
                    version_bump="patch",
                    target_version="0.1.1",
                    workstream_id="general",
                )
                item = crewai_self_upgrade.UpgradeWorkItem(
                    title="清理旧导入引用",
                    summary="移除旧 self_improve runner 引用。",
                    owner_role=crewai_self_upgrade.ROLE_BUGFIX_CODING_AGENT,
                    workstream_id="general",
                    module="Runtime",
                )
                out = crewai_self_upgrade._build_milestone_doc(
                    project_id="teamos",
                    repo_locator="foo/bar",
                    finding=finding,
                    work_item=item,
                )

        self.assertEqual(out["title"], "v0.1.1")
        self.assertEqual(out["milestone_id"], "v0-1-1")
        self.assertEqual(out["release_line"], "patch")
        self.assertEqual(out["state"], "draft")
        self.assertEqual(out["manager_role"], crewai_self_upgrade.ROLE_MILESTONE_MANAGER)

    def test_sync_milestone_from_doc_persists_runtime_milestone_state(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = {
                "project_id": "teamos",
                "task_id": "TEAMOS-1001",
                "title": "[Bug][Runtime] 清理旧导入引用",
                "status": "todo",
                "workstream_id": "general",
                "repo": {
                    "locator": "foo/bar",
                    "workdir": str(repo_root),
                },
                "links": {
                    "issue": "https://github.com/foo/bar/issues/55",
                },
                "self_upgrade": {
                    "kind": "BUG",
                    "lane": "bug",
                    "module": "Runtime",
                    "summary": "修复启动路径中的导入回归。",
                    "rationale": "当前启动链路仍可能引用旧模块。",
                    "impact": "HIGH",
                    "files": ["scaffolds/runtime/orchestrator/app/main.py"],
                    "tests": ["python -m unittest tests.test_crewai_runtime"],
                    "acceptance": ["启动后 /healthz 返回 ok"],
                    "version_bump": "patch",
                    "target_version": "0.1.1",
                    "baseline_action": "",
                    "work_item": {
                        "title": "清理旧导入引用",
                        "summary": "移除旧 self_improve runner 引用。",
                        "owner_role": crewai_self_upgrade.ROLE_BUGFIX_CODING_AGENT,
                        "review_role": crewai_self_upgrade.ROLE_REVIEW_AGENT,
                        "qa_role": crewai_self_upgrade.ROLE_QA_AGENT,
                        "workstream_id": "general",
                        "allowed_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
                        "tests": ["python -m unittest tests.test_crewai_runtime"],
                        "acceptance": ["启动后 /healthz 返回 ok"],
                        "worktree_hint": str(runtime_root / "workspace" / "worktrees" / "wt-bug-runtime"),
                        "module": "Runtime",
                    },
                },
                "orchestration": {
                    "flow": "self_upgrade",
                },
            }
            release_issue = SimpleNamespace(number=101, url="https://github.com/foo/bar/issues/101", title="[Process][Release] 跟踪 v0.1.1 版本发布")
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
                "app.crewai_self_upgrade.ensure_milestone",
                return_value=7,
            ), mock.patch(
                "app.crewai_self_upgrade.ensure_issue",
                return_value=release_issue,
            ), mock.patch(
                "app.crewai_self_upgrade.update_issue",
                return_value=release_issue,
            ):
                out = crewai_self_upgrade.sync_milestone_from_doc(task_doc)
                milestones = plan_store.list_milestones("teamos")

        self.assertTrue(out["ok"])
        self.assertEqual(task_doc["self_upgrade_milestone"]["github_milestone_number"], 7)
        self.assertEqual(task_doc["self_upgrade_milestone"]["release_issue_number"], 101)
        self.assertEqual(task_doc["self_upgrade_milestone"]["state"], "active")
        managed = [m for m in milestones if m.milestone_id == "v0-1-1"]
        self.assertEqual(len(managed), 1)
        self.assertEqual(managed[0].title, "v0.1.1")
        self.assertEqual(managed[0].release_issue_number, 101)

    def test_task_issue_labels_include_module_stage_and_version(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="修复启动导入回归",
            summary="修复启动路径中的导入回归。",
            module="Runtime",
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(title="清理旧导入引用", module="Runtime")
        labels = crewai_self_upgrade._task_issue_labels(doc={"status": "todo"}, finding=finding, work_item=item)
        self.assertEqual(
            labels,
            [
                "milestone:v0-1-1",
                "module:runtime",
                "source:self-upgrade",
                "stage:queued",
                "teamos",
                "type:bug",
                "version:patch",
            ],
        )

    def test_task_issue_labels_use_merge_conflict_stage_when_delivery_hits_conflict(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="修复发布冲突回退",
            summary="修复 release 阶段的冲突回退链路。",
            module="Self-Upgrade",
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(title="回退到 coding 处理冲突", module="Self-Upgrade")
        labels = crewai_self_upgrade._task_issue_labels(
            doc={"status": "merge_conflict", "self_upgrade_execution": {"stage": "merge_conflict"}},
            finding=finding,
            work_item=item,
        )
        self.assertIn("stage:merge-conflict", labels)

    def test_task_issue_labels_use_needs_clarification_stage_when_audit_blocks(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="补齐 issue 闭环描述",
            summary="当前 issue 缺少复现步骤。",
            module="Runtime",
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(title="补齐 issue 闭环描述", module="Runtime")
        labels = crewai_self_upgrade._task_issue_labels(
            doc={"status": "needs_clarification", "self_upgrade_execution": {"stage": "needs_clarification"}},
            finding=finding,
            work_item=item,
        )
        self.assertIn("stage:needs-clarification", labels)

    def test_finding_from_task_doc_roundtrips_quality_test_gap_fields(self):
        task_doc = {
            "title": "[Quality][Runtime] 补齐 /healthz 黑盒回归",
            "status": "todo",
            "workstream_id": "general",
            "execution_policy": {
                "allowed_paths": ["tests/test_runtime_health.py", "scaffolds/runtime/orchestrator/app/main.py"],
                "review_role": crewai_self_upgrade.ROLE_REVIEW_AGENT,
                "qa_role": crewai_self_upgrade.ROLE_QA_AGENT,
            },
            "self_upgrade": {
                "kind": "CODE_QUALITY",
                "lane": "quality",
                "module": "Runtime",
                "summary": "当前 /healthz 用户路径缺少集成级回归覆盖。",
                "rationale": "没有黑盒覆盖时，启动与健康检查回归容易漏掉。",
                "impact": "MED",
                "files": ["scaffolds/runtime/orchestrator/app/main.py", "tests/test_runtime_health.py"],
                "tests": ["python -m unittest tests.test_runtime_health"],
                "acceptance": ["新增集成测试能覆盖 /healthz 行为"],
                "test_gap_type": "blackbox",
                "target_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
                "missing_paths": ["/healthz startup path"],
                "suggested_test_files": ["tests/test_runtime_health.py"],
                "why_not_covered": "当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
                "version_bump": "none",
                "target_version": "0.1.0",
                "work_item": {
                    "title": "补齐 /healthz 黑盒回归",
                    "summary": "新增端到端测试覆盖运行时健康检查。",
                    "owner_role": crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT,
                    "review_role": crewai_self_upgrade.ROLE_REVIEW_AGENT,
                    "qa_role": crewai_self_upgrade.ROLE_QA_AGENT,
                    "workstream_id": "general",
                    "allowed_paths": ["tests/test_runtime_health.py", "scaffolds/runtime/orchestrator/app/main.py"],
                    "tests": ["python -m unittest tests.test_runtime_health"],
                    "acceptance": ["新增集成测试能覆盖 /healthz 行为"],
                    "test_gap_type": "blackbox",
                    "target_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
                    "missing_paths": ["/healthz startup path"],
                    "suggested_test_files": ["tests/test_runtime_health.py"],
                    "why_not_covered": "当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
                    "module": "Runtime",
                },
            },
        }

        finding, item = crewai_self_upgrade._finding_from_task_doc(task_doc)

        self.assertIsNotNone(finding)
        self.assertIsNotNone(item)
        assert finding is not None
        assert item is not None
        self.assertEqual(finding.test_gap_type, "blackbox")
        self.assertEqual(finding.target_paths, ["scaffolds/runtime/orchestrator/app/main.py"])
        self.assertEqual(finding.missing_paths, ["/healthz startup path"])
        self.assertEqual(finding.suggested_test_files, ["tests/test_runtime_health.py"])
        self.assertIn("黑盒回归", finding.why_not_covered)
        self.assertEqual(item.test_gap_type, "blackbox")
        self.assertEqual(item.suggested_test_files, ["tests/test_runtime_health.py"])


if __name__ == "__main__":
    unittest.main()
