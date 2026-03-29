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
        spec = workflow_registry.workflow_spec("repo-review", team_id="repo-improvement", project_id="openteam")

        self.assertEqual(spec.workflow_id, "repo-review")
        self.assertEqual(spec.lane, "review")
        self.assertEqual(spec.phase, "finding")
        self.assertTrue(spec.enabled)

    def test_workflow_spec_returns_delivery_studio_discuss_workflow(self):
        spec = workflow_registry.workflow_spec("delivery-studio-discuss", team_id="delivery-studio", project_id="openteam")

        self.assertEqual(spec.workflow_id, "delivery-studio-discuss")
        self.assertEqual(spec.lane, "discussion")
        self.assertEqual(spec.phase, "discussion")
        self.assertEqual(spec.display_name_zh, "需求讨论流程")
        self.assertTrue(spec.enabled)
        self.assertEqual(spec.task_source, "direct_task")
        self.assertTrue(any(agent.agent_id == "moderator" for agent in spec.agents))
        self.assertTrue(any(agent.agent_id == "product_architect" for agent in spec.agents))
        self.assertTrue(any(agent.agent_id == "skeptic" for agent in spec.agents))
        self.assertTrue(any(task.task_id == "moderate_requirement" for task in spec.tasks))
        self.assertTrue(any(task.skill_id == "team.delivery-studio-discuss" for task in spec.tasks))

    def test_list_workflows_loads_delivery_studio_agents_tasks_and_loop_config(self):
        workflows = workflow_registry.list_workflows(team_id="delivery-studio", project_id="openteam")
        ids = {spec.workflow_id for spec in workflows}

        self.assertIn("delivery-studio-discuss", ids)
        self.assertIn("delivery-studio-coding", ids)
        self.assertIn("delivery-studio-review", ids)

        discuss = next(spec for spec in workflows if spec.workflow_id == "delivery-studio-discuss")
        self.assertEqual(discuss.phase, workflow_registry.PHASE_DISCUSSION)
        self.assertEqual(discuss.display_name_zh, "需求讨论流程")
        self.assertEqual(discuss.task_source, "direct_task")
        self.assertTrue(any(agent.agent_id == "moderator" for agent in discuss.agents))
        self.assertTrue(any(agent.agent_id == "product_architect" for agent in discuss.agents))
        self.assertTrue(any(agent.agent_id == "skeptic" for agent in discuss.agents))
        self.assertIn("discussion", discuss.stages)
        self.assertTrue(any(task.task_id == "moderate_requirement" for task in discuss.tasks))

        coding = next(spec for spec in workflows if spec.workflow_id == "delivery-studio-coding")
        self.assertEqual(coding.phase, workflow_registry.PHASE_CODING)
        self.assertEqual(coding.display_name_zh, "交付实施流程")
        self.assertEqual(coding.stages, ("delivery",))
        self.assertEqual(
            [agent.agent_id for agent in coding.agents],
            ["delivery_lead", "mobile_owner", "admin_owner", "backend_owner", "docs_owner", "qa_owner"],
        )
        self.assertEqual(
            [agent.role_id for agent in coding.agents],
            ["Scheduler-Agent", "Coding-Agent", "Coding-Agent", "Coding-Agent", "Documentation-Agent", "QA-Agent"],
        )

        review = next(spec for spec in workflows if spec.workflow_id == "delivery-studio-review")
        self.assertEqual(review.phase, workflow_registry.PHASE_CODING)
        self.assertEqual(review.task_source, "direct_task")
        self.assertEqual(
            [agent.agent_id for agent in review.agents],
            ["review_moderator", "reviewer_a", "reviewer_b", "reviewer_c"],
        )
        self.assertEqual(
            [agent.role_id for agent in review.agents],
            ["Review-Moderator", "Review-Agent", "Review-Agent", "Review-Agent"],
        )
        self.assertTrue(any(task.task_id == "review_gate" for task in review.tasks))
        self.assertTrue(any(task.skill_id == "team.delivery-studio-review" for task in review.tasks))
        self.assertIn("verification", review.stages)

    def test_workflow_for_lane_phase_prefers_exact_match_for_delivery_lane(self):
        spec = workflow_registry.workflow_for_lane_phase("delivery", workflow_registry.PHASE_CODING, team_id="delivery-studio", project_id="openteam")

        self.assertEqual(spec.workflow_id, "delivery-studio-coding")
        self.assertEqual(spec.lane, "delivery")
        self.assertEqual(spec.phase, workflow_registry.PHASE_CODING)

    def test_workflow_for_lane_phase_returns_review_workflow_for_review_lane_coding_phase(self):
        spec = workflow_registry.workflow_for_lane_phase("review", workflow_registry.PHASE_CODING, team_id="delivery-studio", project_id="openteam")

        self.assertEqual(spec.workflow_id, "delivery-studio-review")
        self.assertEqual(spec.lane, "review")
        self.assertEqual(spec.phase, workflow_registry.PHASE_CODING)

    def test_delivery_studio_workflow_docs_do_not_include_runtime_policy_or_loop_blocks(self):
        for workflow_id in ("delivery-studio-discuss", "delivery-studio-coding", "delivery-studio-review"):
            raw = workflow_registry.spec_loader.team_workflow_doc("delivery-studio", workflow_id)
            self.assertNotIn("runtime_policy", raw)
            self.assertNotIn("loop", raw)

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
            spec = workflow_registry.workflow_spec("repo-review", team_id="repo-improvement", project_id="demo")

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
            spec = workflow_registry.workflow_spec("repo-review", team_id="repo-improvement", project_id="demo")

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
            spec = workflow_registry.workflow_spec("repo-review", team_id="repo-improvement", project_id="demo")

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
            spec = workflow_registry.workflow_spec("repo-review", team_id="repo-improvement", project_id="demo")

        self.assertEqual(spec.active_window_start_hour(), 9)
        self.assertEqual(spec.active_window_end_hour(), 18)
        self.assertEqual(spec.max_continuous_runtime_minutes(), 60)

    def test_list_workflows_loads_agents_tasks_and_loop_config(self):
        workflows = workflow_registry.list_workflows(team_id="repo-improvement", project_id="openteam")
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
