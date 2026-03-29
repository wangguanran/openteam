import os
import sys
import unittest


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import team_registry  # noqa: E402


class CrewAITeamRegistryTests(unittest.TestCase):
    def test_default_team_is_loaded_from_team_specs(self) -> None:
        self.assertEqual(team_registry.default_team_id(), "repo-improvement")

    def test_team_specs_include_repo_improvement_and_delivery_studio(self) -> None:
        team_ids = {spec.team_id for spec in team_registry.list_teams()}

        self.assertIn("repo-improvement", team_ids)
        self.assertIn("delivery-studio", team_ids)

    def test_team_spec_exposes_team_metadata_from_yaml(self) -> None:
        spec = team_registry.team_spec("repo-improvement")

        self.assertEqual(spec.team_id, "repo-improvement")
        self.assertIn("repo-review", spec.workflow_ids)
        self.assertIn("repo-coding", spec.workflow_ids)
        self.assertIn("planning", spec.stage_ids)
        self.assertIn("Test-Manager", spec.role_pool)
        self.assertIn("Scheduler-Agent", spec.role_pool)
        self.assertIn("Coding-Agent", spec.role_pool)
        self.assertIn("Review-Agent", spec.role_pool)
        self.assertIn("QA-Agent", spec.role_pool)
        self.assertIn("Documentation-Agent", spec.role_pool)
        self.assertIn("Release-Agent", spec.role_pool)

    def test_delivery_studio_team_spec_exposes_team_metadata_from_yaml(self) -> None:
        spec = team_registry.team_spec("delivery-studio")

        self.assertEqual(spec.team_id, "delivery-studio")
        self.assertIn("delivery-studio-discuss", spec.workflow_ids)
        self.assertIn("delivery-studio-coding", spec.workflow_ids)
        self.assertIn("delivery-studio-review", spec.workflow_ids)
        self.assertIn("discussion", spec.stage_ids)
        self.assertIn("planning", spec.stage_ids)
        self.assertIn("delivery", spec.stage_ids)
        self.assertIn("verification", spec.stage_ids)
        self.assertIn("Moderator", spec.role_pool)
        self.assertIn("Product-Architect", spec.role_pool)
        self.assertIn("Senior-Engineer-Skeptic", spec.role_pool)
        self.assertIn("App-UI-Designer", spec.role_pool)
        self.assertIn("Admin-Web-Designer", spec.role_pool)
        self.assertIn("Backend-Architect", spec.role_pool)
        self.assertIn("Test-Architect", spec.role_pool)
        self.assertIn("Review-Moderator", spec.role_pool)
        self.assertIn("Scheduler-Agent", spec.role_pool)
        self.assertIn("Coding-Agent", spec.role_pool)
        self.assertIn("Review-Agent", spec.role_pool)
        self.assertIn("QA-Agent", spec.role_pool)
        self.assertIn("Documentation-Agent", spec.role_pool)
        self.assertIn("Release-Agent", spec.role_pool)

    def test_list_teams_reads_team_specs_from_config_tree(self) -> None:
        team_ids = {spec.team_id for spec in team_registry.list_teams()}

        self.assertIn("repo-improvement", team_ids)
        self.assertIn("delivery-studio", team_ids)


if __name__ == "__main__":
    unittest.main()
