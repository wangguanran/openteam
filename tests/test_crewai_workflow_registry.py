import os
import sys
import unittest
import datetime as dt
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import crewai_workflow_registry  # noqa: E402


class CrewAIWorkflowRegistryTests(unittest.TestCase):
    def test_feature_workflow_requires_approval_and_confirmation(self):
        spec = crewai_workflow_registry.workflow_for_lane("feature")

        self.assertTrue(spec.uses_proposal)
        self.assertTrue(spec.requires_user_confirmation)
        self.assertTrue(spec.materialize_requires_approval)
        self.assertEqual(spec.max_candidates(), 5)
        self.assertEqual(spec.default_version_bump, "minor")
        self.assertEqual(spec.default_baseline_action("minor"), "new_baseline")
        self.assertEqual(spec.default_baseline_action("patch"), "feature_followup")
        self.assertFalse(spec.should_materialize(status="COLLECTING", due=True))
        self.assertTrue(spec.should_materialize(status="APPROVED", due=True))
        self.assertTrue(spec.loop.enabled)
        self.assertEqual(spec.phase, crewai_workflow_registry.PHASE_FINDING)
        self.assertTrue(spec.tasks)

    def test_bug_workflow_directly_materializes(self):
        spec = crewai_workflow_registry.workflow_for_lane("bug")

        self.assertFalse(spec.uses_proposal)
        self.assertEqual(spec.default_version_bump, "patch")
        self.assertEqual(spec.max_candidates(), 0)
        self.assertEqual(spec.dormant_after_zero_scans(), 3)
        self.assertEqual(spec.default_baseline_action("patch"), "patch_release")
        self.assertTrue(spec.should_materialize(status="", due=False))

    def test_process_workflow_materializes_when_due_and_not_blocked(self):
        spec = crewai_workflow_registry.workflow_for_lane("process")

        self.assertTrue(spec.uses_proposal)
        self.assertFalse(spec.requires_user_confirmation)
        self.assertTrue(spec.should_materialize(status="COLLECTING", due=True))
        self.assertFalse(spec.should_materialize(status="HOLD", due=True))
        self.assertFalse(spec.should_materialize(status="APPROVED", due=False))

    def test_project_workflow_override_can_disable_feature_lane(self):
        with mock.patch(
            "app.crewai_workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "feature-finding": {
                                "enabled": False,
                                "disabled_reason": "disabled_for_repo",
                            }
                        }
                    }
                }
            },
        ):
            spec = crewai_workflow_registry.workflow_for_lane("feature", project_id="demo")

        self.assertFalse(spec.enabled)
        self.assertEqual(spec.disabled_reason, "disabled_for_repo")
        self.assertFalse(spec.should_materialize(status="APPROVED", due=True))

    def test_project_workflow_override_can_limit_feature_candidates(self):
        with mock.patch(
            "app.crewai_workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "feature-finding": {
                                "runtime_policy": {"max_candidates": 2},
                            }
                        }
                    }
                }
            },
        ):
            spec = crewai_workflow_registry.workflow_for_lane("feature", project_id="demo")

        self.assertEqual(spec.max_candidates(), 2)

    def test_project_workflow_override_can_tune_bug_dormancy_threshold(self):
        with mock.patch(
            "app.crewai_workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "bug-finding": {
                                "runtime_policy": {"dormant_after_zero_scans": 1},
                            }
                        }
                    }
                }
            },
        ):
            spec = crewai_workflow_registry.workflow_for_lane("bug", project_id="demo")

        self.assertEqual(spec.dormant_after_zero_scans(), 1)

    def test_project_workflow_override_can_tune_runtime_window_and_runtime_budget(self):
        with mock.patch(
            "app.crewai_workflow_registry.project_config_store.load_project_config",
            return_value={
                "teams": {
                    "repo-improvement": {
                        "workflow_settings": {
                            "bug-finding": {
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
            spec = crewai_workflow_registry.workflow_for_lane("bug", project_id="demo")

        self.assertEqual(spec.active_window_start_hour(), 9)
        self.assertEqual(spec.active_window_end_hour(), 18)
        self.assertEqual(spec.max_continuous_runtime_minutes(), 60)

    def test_list_workflows_loads_agents_tasks_and_loop_config(self):
        workflows = crewai_workflow_registry.list_workflows(project_id="openteam")
        ids = {spec.workflow_id for spec in workflows}

        self.assertIn("bug-finding", ids)
        self.assertIn("feature-discussion", ids)
        self.assertIn("coding", ids)

        bug_finding = next(spec for spec in workflows if spec.workflow_id == "bug-finding")
        self.assertEqual(bug_finding.loop.interval_sec, 300)
        self.assertEqual(bug_finding.loop.concurrency, 1)
        self.assertTrue(any(agent.agent_id == "scanner" for agent in bug_finding.agents))
        self.assertTrue(any(task.task_id == "materialize_plan" for task in bug_finding.tasks))
        self.assertTrue(any(task.skill_id == "team.materialize-findings" for task in bug_finding.tasks))

        coding = next(spec for spec in workflows if spec.workflow_id == "coding")
        self.assertEqual(coding.phase, crewai_workflow_registry.PHASE_CODING)
        self.assertEqual(coding.lane, "shared")
        self.assertTrue(any(agent.role_id == "Coding-Agent" for agent in coding.agents))

    def test_unified_coding_workflow_resolves_for_any_lane(self):
        bug_coding = crewai_workflow_registry.workflow_for_lane_phase("bug", crewai_workflow_registry.PHASE_CODING)
        feature_coding = crewai_workflow_registry.workflow_for_lane_phase("feature", crewai_workflow_registry.PHASE_CODING)

        self.assertEqual(bug_coding.workflow_id, "coding")
        self.assertEqual(feature_coding.workflow_id, "coding")

    def test_workflow_policy_blocks_outside_active_window(self):
        spec = crewai_workflow_registry.workflow_for_lane("bug")
        spec = crewai_workflow_registry.replace(
            spec,
            default_active_window_start_hour=9,
            default_active_window_end_hour=18,
        )
        policy = spec.evaluate_run_policy(
            now=dt.datetime(2026, 3, 11, 20, 0, tzinfo=dt.timezone.utc),
        )

        self.assertFalse(policy.allowed)
        self.assertEqual(policy.reason, "outside_active_window")

    def test_workflow_policy_blocks_after_max_continuous_runtime(self):
        spec = crewai_workflow_registry.workflow_for_lane("bug")
        spec = crewai_workflow_registry.replace(
            spec,
            default_active_window_start_hour=9,
            default_active_window_end_hour=18,
            default_max_continuous_runtime_minutes=60,
        )
        policy = spec.evaluate_run_policy(
            state={"active_since": "2026-03-11T09:00:00+08:00"},
            now=dt.datetime(2026, 3, 11, 10, 5, tzinfo=dt.timezone.utc),
        )

        self.assertFalse(policy.allowed)
        self.assertEqual(policy.reason, "max_continuous_runtime_exceeded")


if __name__ == "__main__":
    unittest.main()
