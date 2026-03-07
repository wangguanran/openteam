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
    app_dir = os.path.join(repo_root, "templates", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import crewai_self_upgrade  # noqa: E402


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

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
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
            self.assertEqual(out["records"][0]["task_id"], "")
            self.assertEqual(out["records"][0]["issue_url"], "")
            self.assertTrue((runtime_root / "state" / "self_upgrade_state.json").exists())

    def test_decide_proposal_updates_version_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                crewai_self_upgrade._write_proposals_state(
                    {
                        "items": {
                            "su-feature-demo": {
                                "project_id": "teamos",
                                "lane": "feature",
                                "title": "Improve onboarding",
                                "summary": "Ship a new onboarding dashboard.",
                                "status": "PENDING_CONFIRMATION",
                                "current_version": "1.2.3",
                                "version_bump": "minor",
                                "target_version": "1.3.0",
                                "baseline_action": "new_baseline",
                            }
                        }
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
                    raw_hint=str(repo_root / "templates" / "runtime" / "orchestrator" / "wt-bug-startup-fix"),
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

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
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
            self.assertEqual(out["pending_proposals"][0]["status"], "PENDING_CONFIRMATION")
            self.assertEqual(out["pending_proposals"][0]["discussion_issue_url"], "https://example.com/issues/12")
            proposals = json.loads((runtime_root / "state" / "self_upgrade_proposals.json").read_text(encoding="utf-8"))
            self.assertEqual(len((proposals.get("items") or {}).keys()), 1)

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
                    repo_root=repo,
                    repo_locator="foo/bar",
                    project_id="teamos",
                    finding=finding,
                    current_version="0.1.0",
                )
                crewai_self_upgrade.decide_proposal(proposal_id=str(proposal["proposal_id"]), action="approve")
                state = crewai_self_upgrade._read_proposals_state()
                state["items"][proposal["proposal_id"]]["cooldown_until"] = "2026-01-01T00:00:00Z"
                crewai_self_upgrade._write_proposals_state(state)

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

                updated = crewai_self_upgrade._read_proposals_state()["items"][proposal["proposal_id"]]

            self.assertTrue(out["ok"])
            self.assertEqual(len(out["records"]), 1)
            self.assertEqual(out["records"][0]["task_id"], "TEAMOS-1234")
            self.assertEqual(updated["status"], "MATERIALIZED")

    def test_reconcile_feature_discussions_approves_from_issue_comment(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                crewai_self_upgrade._write_proposals_state(
                    {
                        "items": {
                            "su-feature-demo": {
                                "proposal_id": "su-feature-demo",
                                "project_id": "teamos",
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
                        }
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

                updated = crewai_self_upgrade._read_proposals_state()["items"]["su-feature-demo"]

            self.assertEqual(out["updated"], 1)
            self.assertEqual(out["replied"], 1)
            self.assertEqual(updated["status"], "APPROVED")
            self.assertEqual(updated["discussion_last_comment_id"], 101)
            self.assertFalse(updated["awaiting_user_reply"])


if __name__ == "__main__":
    unittest.main()
