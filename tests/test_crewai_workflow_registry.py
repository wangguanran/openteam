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
    def setUp(self) -> None:
        self._orig_env = dict(os.environ)
        for key in list(os.environ):
            if key.startswith("TEAMOS_REPO_IMPROVEMENT_BUG_"):
                os.environ.pop(key, None)
            if key.startswith("TEAMOS_REPO_IMPROVEMENT_FEATURE_"):
                os.environ.pop(key, None)
            if key.startswith("TEAMOS_REPO_IMPROVEMENT_QUALITY_"):
                os.environ.pop(key, None)
            if key.startswith("TEAMOS_REPO_IMPROVEMENT_PROCESS_"):
                os.environ.pop(key, None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)

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

    def test_quality_workflow_uses_env_override_for_cooldown(self):
        with mock.patch.dict(os.environ, {"TEAMOS_REPO_IMPROVEMENT_QUALITY_COOLDOWN_HOURS": "6"}, clear=False):
            spec = crewai_workflow_registry.workflow_for_lane("quality")
            self.assertEqual(spec.cooldown_hours(), 6)

    def test_project_workflow_override_can_disable_feature_lane(self):
        with mock.patch(
            "app.crewai_workflow_registry.project_config_store.load_project_config",
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
        ):
            spec = crewai_workflow_registry.workflow_for_lane("feature", project_id="demo")

        self.assertFalse(spec.enabled)
        self.assertEqual(spec.disabled_reason, "disabled_for_repo")
        self.assertFalse(spec.should_materialize(status="APPROVED", due=True))

    def test_project_workflow_override_can_limit_feature_candidates(self):
        with mock.patch(
            "app.crewai_workflow_registry.project_config_store.load_project_config",
            return_value={
                "repo_improvement": {
                    "workflow_settings": {
                        "feature-improvement": {
                            "max_candidates": 2,
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
                "repo_improvement": {
                    "workflow_settings": {
                        "bug-fix": {
                            "dormant_after_zero_scans": 1,
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
                "repo_improvement": {
                    "workflow_settings": {
                        "bug-fix": {
                            "active_window_start_hour": 9,
                            "active_window_end_hour": 18,
                            "max_continuous_runtime_minutes": 60,
                        }
                    }
                }
            },
        ):
            spec = crewai_workflow_registry.workflow_for_lane("bug", project_id="demo")

        self.assertEqual(spec.active_window_start_hour(), 9)
        self.assertEqual(spec.active_window_end_hour(), 18)
        self.assertEqual(spec.max_continuous_runtime_minutes(), 60)

    def test_lane_named_env_can_disable_bug_workflow(self):
        with mock.patch.dict(os.environ, {"TEAMOS_REPO_IMPROVEMENT_BUG_ENABLED": "0"}, clear=False):
            spec = crewai_workflow_registry.workflow_for_lane("bug")

        self.assertFalse(spec.enabled)
        self.assertEqual(spec.disabled_reason, "workflow_disabled_by_env")

    def test_lane_named_env_can_override_quality_runtime_window(self):
        with mock.patch.dict(
            os.environ,
            {
                "TEAMOS_REPO_IMPROVEMENT_QUALITY_ACTIVE_WINDOW_START_HOUR": "9",
                "TEAMOS_REPO_IMPROVEMENT_QUALITY_ACTIVE_WINDOW_END_HOUR": "18",
                "TEAMOS_REPO_IMPROVEMENT_QUALITY_MAX_CONTINUOUS_RUNTIME_MINUTES": "45",
            },
            clear=False,
        ):
            spec = crewai_workflow_registry.workflow_for_lane("quality")

        self.assertEqual(spec.active_window_start_hour(), 9)
        self.assertEqual(spec.active_window_end_hour(), 18)
        self.assertEqual(spec.max_continuous_runtime_minutes(), 45)

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
