import os
import sys
import unittest


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import crewai_spec_loader  # noqa: E402
from app import crewai_role_registry  # noqa: E402


class _FakeDB:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._n = 0

    def register_agent(self, **kwargs):
        self._n += 1
        self.calls.append(kwargs)
        return f"agent-{self._n}"


class CrewAIRoleRegistryTests(unittest.TestCase):
    def test_register_team_blueprint_registers_all_members(self):
        db = _FakeDB()
        blueprint = crewai_role_registry.planning_team_blueprint()

        out = crewai_role_registry.register_team_blueprint(
            db=db,
            blueprint=blueprint,
            project_id="teamos",
            workstream_id="general",
            task_id="run-1",
        )

        self.assertEqual(set(out.keys()), set(crewai_role_registry.role_ids_for_team(blueprint)))
        self.assertEqual(len(db.calls), len(blueprint.members))
        self.assertIn(crewai_role_registry.ROLE_TEST_CASE_GAP_AGENT, crewai_role_registry.role_ids_for_team(blueprint))

    def test_delivery_team_blueprint_includes_dynamic_roles(self):
        blueprint = crewai_role_registry.delivery_team_blueprint(
            owner_role="Bugfix-Coding-Agent",
            review_role="Review-Agent",
            qa_role="QA-Agent",
            documentation_role="Documentation-Agent",
        )

        self.assertIn("Bugfix-Coding-Agent", crewai_role_registry.role_ids_for_team(blueprint))
        self.assertIn(crewai_role_registry.ROLE_BUG_REPRO_AGENT, crewai_role_registry.role_ids_for_team(blueprint))
        self.assertIn(crewai_role_registry.ROLE_BUG_TESTCASE_AGENT, crewai_role_registry.role_ids_for_team(blueprint))
        self.assertIn(crewai_role_registry.ROLE_SCHEDULER_AGENT, crewai_role_registry.role_ids_for_team(blueprint))
        self.assertIn(crewai_role_registry.ROLE_RELEASE_AGENT, crewai_role_registry.role_ids_for_team(blueprint))
        self.assertEqual(blueprint.team_id, crewai_role_registry.STAGE_DELIVERY)

    def test_loaded_role_spec_exposes_display_name_from_yaml(self):
        spec = crewai_role_registry.get_role_spec(crewai_role_registry.ROLE_REVIEW_AGENT)

        self.assertEqual(spec.display_name_zh, "评审 Agent")
        self.assertEqual(spec.tool_profile, "read")

    def test_get_role_spec_supports_custom_role_with_fallback(self):
        spec = crewai_role_registry.get_role_spec("Custom-Review-Agent", fallback_role_id=crewai_role_registry.ROLE_REVIEW_AGENT)

        self.assertEqual(spec.role_id, "Custom-Review-Agent")
        self.assertEqual(spec.display_name_zh, "评审 Agent")
        self.assertEqual(spec.tool_profile, "read")
        self.assertTrue(spec.goal)

    def test_issue_audit_role_uses_qa_tool_profile(self):
        spec = crewai_role_registry.get_role_spec(crewai_role_registry.ROLE_ISSUE_AUDIT_AGENT)

        self.assertEqual(spec.tool_profile, "qa")
        self.assertIn("reject work", spec.goal)

    def test_bug_validation_roles_load_from_yaml(self):
        repro = crewai_role_registry.get_role_spec(crewai_role_registry.ROLE_BUG_REPRO_AGENT)
        testcase = crewai_role_registry.get_role_spec(crewai_role_registry.ROLE_BUG_TESTCASE_AGENT)

        self.assertEqual(repro.tool_profile, "qa")
        self.assertEqual(testcase.tool_profile, "write")
        self.assertIn("reproducible", repro.goal)
        self.assertIn("failing automated test", testcase.goal)

    def test_repo_improvement_team_doc_loads_from_nested_path(self):
        doc = crewai_spec_loader.team_doc(crewai_role_registry.TEAM_REPO_IMPROVEMENT)

        self.assertEqual(doc.get("team_id"), crewai_role_registry.TEAM_REPO_IMPROVEMENT)
        self.assertIn(crewai_role_registry.WORKFLOW_FEATURE_IMPROVEMENT, doc.get("workflow_ids") or [])
        self.assertIn(crewai_role_registry.ROLE_TEST_CASE_GAP_AGENT, doc.get("role_pool") or [])

    def test_team_stage_and_workflow_docs_load_from_nested_path(self):
        stage = crewai_spec_loader.team_stage_doc(crewai_role_registry.TEAM_REPO_IMPROVEMENT, crewai_role_registry.STAGE_PLANNING)
        workflow = crewai_spec_loader.team_workflow_doc(crewai_role_registry.TEAM_REPO_IMPROVEMENT, crewai_role_registry.WORKFLOW_BUG_FIX)

        self.assertEqual(stage.get("stage_id"), crewai_role_registry.STAGE_PLANNING)
        self.assertTrue(stage.get("members"))
        self.assertEqual(workflow.get("workflow_id"), crewai_role_registry.WORKFLOW_BUG_FIX)
        self.assertEqual(workflow.get("stages"), [crewai_role_registry.STAGE_PLANNING, crewai_role_registry.STAGE_DELIVERY])
        self.assertIn(crewai_role_registry.ROLE_TEST_CASE_GAP_AGENT, [str(x.get("role_id") or "") for x in (stage.get("members") or []) if isinstance(x, dict)])

    def test_test_case_gap_role_loads_from_yaml(self):
        spec = crewai_role_registry.get_role_spec(crewai_role_registry.ROLE_TEST_CASE_GAP_AGENT)

        self.assertEqual(spec.display_name_zh, "测试缺口分析 Agent")
        self.assertIn("black-box", spec.goal)


if __name__ == "__main__":
    unittest.main()
