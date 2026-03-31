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
        required_paths = [
            team_dir / "specs" / "team.yaml",
            team_dir / "specs" / "workflows",
            team_dir / "specs" / "stages",
            team_dir / "specs" / "roles",
            team_dir / "specs" / "skills",
            team_dir / "specs" / "workflows" / "delivery-studio-discuss.yaml",
            team_dir / "specs" / "workflows" / "delivery-studio-coding.yaml",
            team_dir / "specs" / "workflows" / "delivery-studio-review.yaml",
            team_dir / "specs" / "stages" / "discussion.yaml",
            team_dir / "specs" / "stages" / "planning.yaml",
            team_dir / "specs" / "stages" / "delivery.yaml",
            team_dir / "specs" / "stages" / "verification.yaml",
            team_dir / "specs" / "roles" / "moderator.yaml",
            team_dir / "specs" / "roles" / "product-architect.yaml",
            team_dir / "specs" / "roles" / "senior-engineer-skeptic.yaml",
            team_dir / "specs" / "roles" / "app-ui-designer.yaml",
            team_dir / "specs" / "roles" / "admin-web-designer.yaml",
            team_dir / "specs" / "roles" / "backend-architect.yaml",
            team_dir / "specs" / "roles" / "test-architect.yaml",
            team_dir / "specs" / "roles" / "review-moderator.yaml",
            team_dir / "specs" / "skills" / "delivery-studio-discuss.yaml",
            team_dir / "specs" / "skills" / "delivery-studio-review.yaml",
        ]

        self.assertTrue(team_dir.is_dir())
        self.assertEqual(sorted(str(path.relative_to(team_dir)) for path in team_dir.rglob("*.py")), [])
        for path in required_paths:
            self.assertTrue(path.exists(), str(path))

    def test_delivery_studio_stage_docs_match_planned_shape(self) -> None:
        discussion = spec_loader.team_stage_doc("delivery-studio", "discussion")
        planning = spec_loader.team_stage_doc("delivery-studio", "planning")
        delivery = spec_loader.team_stage_doc("delivery-studio", "delivery")
        verification = spec_loader.team_stage_doc("delivery-studio", "verification")

        self.assertEqual(discussion.get("display_name_zh"), "前台讨论")
        self.assertTrue(discussion.get("summary"))
        self.assertEqual(planning.get("display_name_zh"), "文档与计划冻结")
        self.assertTrue(planning.get("summary"))
        self.assertEqual(delivery.get("display_name_zh"), "多线实施")
        self.assertTrue(delivery.get("summary"))
        self.assertEqual(verification.get("display_name_zh"), "评审与验证")
        self.assertTrue(verification.get("summary"))

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
        self.assertTrue(moderator.get("backstory"))

        self.assertEqual(product_architect.get("display_name_zh"), "产品架构师")
        self.assertEqual(product_architect.get("tool_profile"), "read")
        self.assertTrue(product_architect.get("goal"))

        self.assertEqual(skeptic.get("display_name_zh"), "资深工程反方")
        self.assertEqual(skeptic.get("tool_profile"), "read")
        self.assertTrue(skeptic.get("goal"))

        self.assertEqual(app_ui.get("display_name_zh"), "App 设计师")
        self.assertEqual(app_ui.get("tool_profile"), "read")
        self.assertTrue(app_ui.get("goal"))

        self.assertEqual(admin_web.get("display_name_zh"), "管理后台设计师")
        self.assertEqual(admin_web.get("tool_profile"), "read")
        self.assertTrue(admin_web.get("goal"))

        self.assertEqual(backend_architect.get("display_name_zh"), "后端架构师")
        self.assertEqual(backend_architect.get("tool_profile"), "read")
        self.assertTrue(backend_architect.get("goal"))

        self.assertEqual(test_architect.get("display_name_zh"), "测试架构师")
        self.assertEqual(test_architect.get("tool_profile"), "qa")
        self.assertTrue(test_architect.get("goal"))

        self.assertEqual(review_moderator.get("display_name_zh"), "评审主持人")
        self.assertEqual(review_moderator.get("tool_profile"), "read")
        self.assertTrue(review_moderator.get("goal"))

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
        for agent in discuss.get("agents") or []:
            if isinstance(agent, dict):
                self.assertNotIn("openrouter/", str(agent.get("model") or ""))
        self.assertEqual(coding.get("phase"), "coding")
        self.assertEqual(coding.get("stages"), ["delivery"])
        self.assertEqual(review.get("phase"), "coding")
        self.assertEqual(review.get("task_source"), "direct_task")
        for agent in review.get("agents") or []:
            if isinstance(agent, dict):
                self.assertNotIn("openrouter/", str(agent.get("model") or ""))


if __name__ == "__main__":
    unittest.main()
