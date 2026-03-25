import os
import sys
import unittest
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app.engines.crewai import workflow_registry as workflow_registry  # noqa: E402


class WorkflowRegistryTests(unittest.TestCase):
    def test_workflow_spec_returns_known_workflow(self):
        spec = workflow_registry.workflow_spec("repo-review", project_id="openteam")

        self.assertEqual(spec.workflow_id, "repo-review")
        self.assertEqual(spec.lane, "review")
        self.assertEqual(spec.phase, "finding")
        self.assertTrue(spec.enabled)

    def test_project_workflow_override_can_disable_repo_review(self):
        with mock.patch(
            "app.workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "repo-review": {
                                "enabled": False,
                                "disabled_reason": "disabled_for_repo",
                            }
                        }
                    }
                }
            },
        ):
            spec = workflow_registry.workflow_spec("repo-review", project_id="demo")

        self.assertFalse(spec.enabled)
        self.assertEqual(spec.disabled_reason, "disabled_for_repo")

    def test_project_workflow_override_can_limit_review_candidates(self):
        with mock.patch(
            "app.workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "repo-review": {
                                "runtime_policy": {"max_candidates": 3},
                            }
                        }
                    }
                }
            },
        ):
            spec = workflow_registry.workflow_spec("repo-review", project_id="demo")

        self.assertEqual(spec.max_candidates(), 3)

    def test_project_workflow_override_can_tune_dormancy_threshold(self):
        with mock.patch(
            "app.workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "repo-review": {
                                "runtime_policy": {"dormant_after_zero_scans": 1},
                            }
                        }
                    }
                }
            },
        ):
            spec = workflow_registry.workflow_spec("repo-review", project_id="demo")

        self.assertEqual(spec.dormant_after_zero_scans(), 1)

    def test_project_workflow_override_can_tune_runtime_window_and_budget(self):
        with mock.patch(
            "app.workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "repo-review": {
                                "runtime_policy": {
                                    "active_window_start_hour": 9,
                                    "active_window_end_hour": 18,
                                    "max_continuous_runtime_minutes": 60,
                                }
                            }
                        }
                    }
                }
            },
        ):
            spec = workflow_registry.workflow_spec("repo-review", project_id="demo")

        self.assertEqual(spec.active_window_start_hour(), 9)
        self.assertEqual(spec.active_window_end_hour(), 18)
        self.assertEqual(spec.max_continuous_runtime_minutes(), 60)

    def test_list_workflows_loads_agents_tasks_and_loop_config(self):
        workflows = workflow_registry.list_workflows(project_id="openteam")
        ids = {spec.workflow_id for spec in workflows}

        self.assertIn("repo-review", ids)
        self.assertIn("repo-coding", ids)
        self.assertIn("repo-coding", ids)

        repo_review = next(spec for spec in workflows if spec.workflow_id == "repo-review")
        self.assertEqual(repo_review.loop.interval_sec, 300)
        self.assertEqual(repo_review.loop.concurrency, 1)
        self.assertTrue(any(agent.agent_id == "bug_scanner" for agent in repo_review.agents))
        self.assertTrue(any(agent.agent_id == "feature_scanner" for agent in repo_review.agents))
        self.assertTrue(any(task.task_id == "materialize_plan" for task in repo_review.tasks))
        self.assertTrue(any(task.skill_id == "team.materialize-findings" for task in repo_review.tasks))

        coding = next(spec for spec in workflows if spec.workflow_id == "repo-coding")
        self.assertEqual(coding.phase, workflow_registry.PHASE_CODING)
        self.assertEqual(coding.lane, "shared")
        self.assertTrue(any(agent.role_id == "Coding-Agent" for agent in coding.agents))


if __name__ == "__main__":
    unittest.main()
