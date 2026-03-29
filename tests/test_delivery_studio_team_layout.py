import os
import sys
import unittest
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_template_app_to_syspath() -> None:
    app_dir = _repo_root() / "scaffolds" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


_add_template_app_to_syspath()

from app import spec_loader  # noqa: E402


class DeliveryStudioTeamLayoutTests(unittest.TestCase):
    def test_delivery_studio_team_directory_contains_expected_spec_files(self) -> None:
        team_dir = _repo_root() / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "delivery_studio"
        expected_files = {
            "specs/team.yaml",
            "specs/workflows/delivery-studio-discuss.yaml",
            "specs/workflows/delivery-studio-coding.yaml",
            "specs/workflows/delivery-studio-review.yaml",
            "specs/stages/discussion.yaml",
            "specs/stages/planning.yaml",
            "specs/stages/delivery.yaml",
            "specs/stages/verification.yaml",
            "specs/roles/moderator.yaml",
            "specs/roles/product-architect.yaml",
            "specs/roles/senior-engineer-skeptic.yaml",
            "specs/roles/app-ui-designer.yaml",
            "specs/roles/admin-web-designer.yaml",
            "specs/roles/backend-architect.yaml",
            "specs/roles/test-architect.yaml",
            "specs/roles/review-moderator.yaml",
        }

        self.assertTrue(team_dir.is_dir())
        self.assertEqual(sorted(str(path.relative_to(team_dir)) for path in team_dir.rglob("*.py")), [])
        self.assertEqual({str(path.relative_to(team_dir)) for path in team_dir.rglob("*.yaml")}, expected_files)

    def test_delivery_studio_team_spec_matches_registry_shape(self) -> None:
        doc = spec_loader.team_doc("delivery-studio")
        discuss = spec_loader.team_workflow_doc("delivery-studio", "delivery-studio-discuss")
        coding = spec_loader.team_workflow_doc("delivery-studio", "delivery-studio-coding")
        review = spec_loader.team_workflow_doc("delivery-studio", "delivery-studio-review")

        self.assertEqual(doc.get("team_id"), "delivery-studio")
        self.assertEqual(doc.get("workflow_ids"), [
            "delivery-studio-discuss",
            "delivery-studio-coding",
            "delivery-studio-review",
        ])
        self.assertEqual(doc.get("stage_ids"), ["discussion", "planning", "delivery", "verification"])
        self.assertEqual(discuss.get("phase"), "discussion")
        agent_ids = {str(agent.get("agent_id") or "") for agent in discuss.get("agents") or [] if isinstance(agent, dict)}
        self.assertIn("moderator", agent_ids)
        self.assertIn("product_architect", agent_ids)
        self.assertEqual(coding.get("phase"), "coding")
        self.assertEqual(review.get("phase"), "finding")


if __name__ == "__main__":
    unittest.main()
