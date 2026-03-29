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

    def test_delivery_studio_stage_docs_match_planned_shape(self) -> None:
        discussion = spec_loader.team_stage_doc("delivery-studio", "discussion")
        planning = spec_loader.team_stage_doc("delivery-studio", "planning")
        delivery = spec_loader.team_stage_doc("delivery-studio", "delivery")
        verification = spec_loader.team_stage_doc("delivery-studio", "verification")

        self.assertEqual(discussion.get("display_name_zh"), "前台讨论")
        self.assertEqual(discussion.get("summary"), "Moderator-led panel discussion with optional designer and backend architect expansion.")
        self.assertEqual(planning.get("display_name_zh"), "文档与计划冻结")
        self.assertEqual(planning.get("summary"), "Approval record, docs, changelog, design package, contract baseline, and master plan are prepared before coding.")
        self.assertEqual(delivery.get("display_name_zh"), "多线实施")
        self.assertEqual(delivery.get("summary"), "Delivery Lead coordinates parallel mobile, admin, backend, and contract workstreams.")
        self.assertEqual(verification.get("display_name_zh"), "评审与验证")
        self.assertEqual(verification.get("summary"), "Review veto, test completeness, GitHub Checks, CI, and merge readiness are enforced here.")

    def test_delivery_studio_role_docs_match_planned_shape(self) -> None:
        moderator = spec_loader.role_doc("Moderator", team_id="delivery-studio")
        product_architect = spec_loader.role_doc("Product-Architect", team_id="delivery-studio")
        skeptic = spec_loader.role_doc("Senior-Engineer-Skeptic", team_id="delivery-studio")
        app_ui = spec_loader.role_doc("App-UI-Designer", team_id="delivery-studio")
        admin_web = spec_loader.role_doc("Admin-Web-Designer", team_id="delivery-studio")
        backend_architect = spec_loader.role_doc("Backend-Architect", team_id="delivery-studio")
        test_architect = spec_loader.role_doc("Test-Architect", team_id="delivery-studio")
        review_moderator = spec_loader.role_doc("Review-Moderator", team_id="delivery-studio")

        self.assertEqual(moderator.get("display_name_zh"), "主持人")
        self.assertEqual(moderator.get("tool_profile"), "read")
        self.assertIn("disciplined delivery facilitator", moderator.get("backstory") or "")

        self.assertEqual(product_architect.get("display_name_zh"), "产品架构师")
        self.assertEqual(product_architect.get("tool_profile"), "read")
        self.assertIn("Convert user intent", product_architect.get("goal") or "")

        self.assertEqual(skeptic.get("display_name_zh"), "资深工程反方")
        self.assertEqual(skeptic.get("tool_profile"), "read")
        self.assertIn("Challenge vague scope", skeptic.get("goal") or "")

        self.assertEqual(app_ui.get("display_name_zh"), "App 设计师")
        self.assertEqual(app_ui.get("tool_profile"), "read")
        self.assertIn("app UI", app_ui.get("goal") or "")

        self.assertEqual(admin_web.get("display_name_zh"), "管理后台设计师")
        self.assertEqual(admin_web.get("tool_profile"), "read")
        self.assertIn("admin-web UI", admin_web.get("goal") or "")

        self.assertEqual(backend_architect.get("display_name_zh"), "后端架构师")
        self.assertEqual(backend_architect.get("tool_profile"), "read")
        self.assertIn("contract baselines", backend_architect.get("goal") or "")

        self.assertEqual(test_architect.get("display_name_zh"), "测试架构师")
        self.assertEqual(test_architect.get("tool_profile"), "qa")
        self.assertIn("test matrix", test_architect.get("goal") or "")

        self.assertEqual(review_moderator.get("display_name_zh"), "评审主持人")
        self.assertEqual(review_moderator.get("tool_profile"), "read")
        self.assertIn("formal blocking gate", review_moderator.get("goal") or "")

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
        self.assertEqual(discuss.get("task_source"), "direct_task")
        agent_ids = {str(agent.get("agent_id") or "") for agent in discuss.get("agents") or [] if isinstance(agent, dict)}
        self.assertIn("moderator", agent_ids)
        self.assertIn("product_architect", agent_ids)
        self.assertIn("skeptic", agent_ids)
        self.assertEqual(coding.get("phase"), "coding")
        self.assertEqual(coding.get("stages"), ["delivery"])
        self.assertEqual(review.get("phase"), "coding")
        self.assertEqual(review.get("task_source"), "direct_task")


if __name__ == "__main__":
    unittest.main()
