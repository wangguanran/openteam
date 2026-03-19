import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()
os.environ.setdefault("TEAMOS_RUNTIME_LOCALIZE_ZH", "0")

from app.domains.repo_improvement import proposal_runtime  # noqa: E402


class _FakeDB:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def add_event(self, **kwargs):
        self.events.append(kwargs)


class CrewAIRepoImprovementTests(unittest.TestCase):
    def _spec(self, repo: Path) -> SimpleNamespace:
        return SimpleNamespace(
            project_id="teamos",
            workstream_id="general",
            repo_path=str(repo),
            repo_url="",
            repo_locator="foo/bar",
            target_id="demo-target",
            force=False,
            dry_run=True,
            trigger="test",
            task_id="",
        )

    def test_parse_repo_locator_supports_https_and_ssh(self):
        self.assertEqual(proposal_runtime._parse_repo_locator("https://github.com/foo/bar.git"), "foo/bar")
        self.assertEqual(proposal_runtime._parse_repo_locator("git@github.com:foo/bar.git"), "foo/bar")

    def test_collect_repo_context_marks_dirty_repo_when_git_status_has_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            fake_proc = SimpleNamespace(returncode=0, stdout=" M src/demo.py\n", stderr="")
            with mock.patch("app.domains.repo_improvement.proposal_runtime.subprocess.run", return_value=fake_proc):
                ctx = proposal_runtime.collect_repo_context(repo_root=repo)

            self.assertTrue(ctx["git_status_dirty"])
            self.assertEqual(ctx["git_status_sample"], [" M src/demo.py"])

    def test_run_repo_improvement_returns_skipped_when_no_enabled_workflows(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            target = {"target_id": "demo-target", "project_id": "teamos", "repo_root": str(repo), "repo_locator": "foo/bar"}
            with mock.patch("app.domains.repo_improvement.proposal_runtime._resolve_target", return_value=target), mock.patch(
                "app.domains.repo_improvement.proposal_runtime.crewai_workflow_registry.list_workflows",
                return_value=(),
            ):
                out = proposal_runtime.run_repo_improvement(
                    db=db,
                    spec=self._spec(repo),
                    actor="test",
                    run_id="run-1",
                    crewai_info={"importable": True},
                )

        self.assertTrue(out["ok"])
        self.assertTrue(out["skipped"])
        self.assertEqual(out["reason"], "no_enabled_workflows")
        self.assertTrue(any(event.get("event_type") == "REPO_IMPROVEMENT_SKIPPED" for event in db.events))

    def test_run_repo_improvement_aggregates_workflow_runner_outputs(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            target = {"target_id": "demo-target", "project_id": "teamos", "repo_root": str(repo), "repo_locator": "foo/bar"}
            bug_workflow = SimpleNamespace(workflow_id="bug-finding", lane="bug", phase="finding", enabled=True)
            feature_workflow = SimpleNamespace(workflow_id="feature-finding", lane="feature", phase="finding", enabled=True)
            allowed_policy = SimpleNamespace(allowed=True, reason="", active_window_start_hour=0, active_window_end_hour=24, max_continuous_runtime_minutes=0, current_local_hour=0, active_since="", now_iso="")

            bug_result = {
                "ok": True,
                "state": {
                    "tasks": {
                        "prepare_context": {"outputs": {"repo_context": {"repo_locator": "foo/bar"}}},
                        "materialize_plan": {
                            "outputs": {
                                "summary": "bug summary",
                                "current_version": "0.1.0",
                                "planned_version": "0.1.1",
                                "plan": {"findings": [{"lane": "bug"}], "ci_actions": ["pytest"], "notes": ["bug-note"]},
                                "records": [{"task_id": "BUG-1"}],
                                "pending_proposals": [],
                                "panel_sync": {"ok": True},
                                "bug_scan_policy": {"dormant": False},
                            }
                        },
                    }
                },
            }
            feature_result = {
                "ok": True,
                "state": {
                    "tasks": {
                        "materialize_plan": {
                            "outputs": {
                                "summary": "feature summary",
                                "current_version": "0.1.0",
                                "planned_version": "0.2.0",
                                "plan": {"findings": [{"lane": "feature"}], "ci_actions": [], "notes": ["feature-note"]},
                                "records": [],
                                "pending_proposals": [{"proposal_id": "PROP-1"}],
                                "panel_sync": {"ok": True},
                            }
                        }
                    }
                },
            }

            with mock.patch("app.domains.repo_improvement.proposal_runtime._resolve_target", return_value=target), mock.patch(
                "app.domains.repo_improvement.proposal_runtime.crewai_workflow_registry.list_workflows",
                return_value=(bug_workflow, feature_workflow),
            ), mock.patch(
                "app.domains.repo_improvement.proposal_runtime.crewai_workflow_registry.evaluate_workflow_runtime_policy",
                return_value=allowed_policy,
            ), mock.patch(
                "app.domains.repo_improvement.proposal_runtime.crewai_workflow_registry.update_workflow_runtime_state",
                return_value={},
            ), mock.patch(
                "app.engines.crewai.workflow_runner.run_workflow",
                side_effect=[bug_result, feature_result],
            ), mock.patch(
                "app.domains.repo_improvement.proposal_runtime._update_bug_lane_state",
                return_value={"status": "active"},
            ), mock.patch("app.domains.repo_improvement.proposal_runtime.improvement_store.save_report"):
                out = proposal_runtime.run_repo_improvement(
                    db=db,
                    spec=self._spec(repo),
                    actor="test",
                    run_id="run-1",
                    crewai_info={"importable": True},
                )

        self.assertTrue(out["ok"])
        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(len(out["pending_proposals"]), 1)
        self.assertEqual(out["bug_findings"], 1)
        self.assertEqual(out["planned_version"], "0.2.0")
        self.assertEqual(len(out["workflow_results"]), 2)
        self.assertIn("bug summary", out["summary"])
        self.assertIn("feature summary", out["summary"])

    def test_reconcile_feature_discussions_runs_discussion_workflows(self):
        feature_workflow = SimpleNamespace(workflow_id="feature-discussion", lane="feature", phase="discussion", enabled=True)
        quality_workflow = SimpleNamespace(workflow_id="quality-discussion", lane="quality", phase="discussion", enabled=True)
        allowed_policy = SimpleNamespace(allowed=True, reason="", active_window_start_hour=0, active_window_end_hour=24, max_continuous_runtime_minutes=0, current_local_hour=0, active_since="", now_iso="")

        feature_result = {
            "ok": True,
            "state": {
                "tasks": {
                    "claim_discussion": {"outputs": {"proposal": {"proposal_id": "P-1"}}},
                    "apply_discussion": {"outputs": {"updated": True}},
                }
            },
        }
        quality_result = {
            "ok": True,
            "state": {"tasks": {"claim_discussion": {"outputs": {"proposal": None}}, "apply_discussion": {"outputs": {"updated": False}}}},
        }

        with mock.patch(
            "app.domains.repo_improvement.proposal_runtime.crewai_workflow_registry.list_workflows",
            return_value=(feature_workflow, quality_workflow),
        ), mock.patch(
            "app.domains.repo_improvement.proposal_runtime.crewai_workflow_registry.evaluate_workflow_runtime_policy",
            return_value=allowed_policy,
        ), mock.patch(
            "app.domains.repo_improvement.proposal_runtime.crewai_workflow_registry.update_workflow_runtime_state",
            return_value={},
        ), mock.patch(
            "app.engines.crewai.workflow_runner.run_workflow",
            side_effect=[feature_result, quality_result],
        ):
            out = proposal_runtime.reconcile_feature_discussions(db=_FakeDB(), actor="test", project_id="teamos", target_id="demo-target")

        self.assertEqual(out["scanned"], 1)
        self.assertEqual(out["updated"], 1)
        self.assertEqual(out["replied"], 1)
        self.assertEqual(out["errors"], 0)

    def test_crewai_llm_marks_openrouter_models_as_litellm(self):
        captured: dict[str, object] = {}

        class _FakeLLM:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

        fake_module = SimpleNamespace(LLM=_FakeLLM)
        with mock.patch.dict(
            os.environ,
            {
                "TEAMOS_LLM_MODEL": "openrouter/openai/gpt-5.4",
                "TEAMOS_LLM_BASE_URL": "https://openrouter.ai/api/v1",
                "TEAMOS_LLM_API_KEY": "sk-test",
                "TEAMOS_CREWAI_AUTH_MODE": "",
            },
            clear=False,
        ), mock.patch("app.domains.repo_improvement.proposal_runtime.crewai_runtime.require_crewai_importable", return_value={"importable": True}), mock.patch(
            "app.domains.repo_improvement.proposal_runtime.codex_llm.codex_login_status",
            return_value=(False, {}),
        ), mock.patch.dict(sys.modules, {"crewai.llm": fake_module}):
            proposal_runtime._crewai_llm()

        self.assertEqual(captured["model"], "openrouter/openai/gpt-5.4")
        self.assertTrue(captured["is_litellm"])


if __name__ == "__main__":
    unittest.main()
