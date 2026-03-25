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

    def test_team_spec_exposes_team_metadata_from_yaml(self) -> None:
        spec = team_registry.team_spec("repo-improvement")

        self.assertEqual(spec.team_id, "repo-improvement")
        self.assertIn("repo-review", spec.workflow_ids)
        self.assertIn("repo-coding", spec.workflow_ids)
        self.assertIn("planning", spec.stage_ids)
        self.assertIn("Test-Manager", spec.role_pool)

    def test_list_teams_reads_team_specs_from_config_tree(self) -> None:
        team_ids = {spec.team_id for spec in team_registry.list_teams()}

        self.assertIn("repo-improvement", team_ids)


if __name__ == "__main__":
    unittest.main()
