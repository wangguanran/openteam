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

from app import crewai_workflow_registry  # noqa: E402


class CrewAIWorkflowRegistryTests(unittest.TestCase):
    def test_feature_workflow_requires_approval_and_confirmation(self):
        spec = crewai_workflow_registry.workflow_for_lane("feature")

        self.assertTrue(spec.uses_proposal)
        self.assertTrue(spec.requires_user_confirmation)
        self.assertTrue(spec.materialize_requires_approval)
        self.assertEqual(spec.default_version_bump, "minor")
        self.assertEqual(spec.default_baseline_action("minor"), "new_baseline")
        self.assertEqual(spec.default_baseline_action("patch"), "feature_followup")
        self.assertFalse(spec.should_materialize(status="COLLECTING", due=True))
        self.assertTrue(spec.should_materialize(status="APPROVED", due=True))

    def test_bug_workflow_directly_materializes(self):
        spec = crewai_workflow_registry.workflow_for_lane("bug")

        self.assertFalse(spec.uses_proposal)
        self.assertEqual(spec.default_version_bump, "patch")
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
        with mock.patch.dict(os.environ, {"TEAMOS_SELF_UPGRADE_QUALITY_COOLDOWN_HOURS": "6"}, clear=False):
            spec = crewai_workflow_registry.workflow_for_lane("quality")
            self.assertEqual(spec.cooldown_hours(), 6)

    def test_team_workflow_override_can_disable_feature_lane(self):
        with mock.patch(
            "app.crewai_workflow_registry.crewai_spec_loader.team_doc",
            return_value={
                "team_id": "repo-improvement",
                "workflow_ids": ["feature-improvement", "bug-fix", "quality-improvement", "process-improvement"],
                "workflow_settings": {
                    "feature-improvement": {
                        "enabled": False,
                        "disabled_reason": "disabled_for_repo",
                    }
                },
            },
        ):
            spec = crewai_workflow_registry.workflow_for_lane("feature")

        self.assertFalse(spec.enabled)
        self.assertEqual(spec.disabled_reason, "disabled_for_repo")
        self.assertFalse(spec.should_materialize(status="APPROVED", due=True))


if __name__ == "__main__":
    unittest.main()
