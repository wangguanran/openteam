import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import spec_loader  # noqa: E402
from app import role_registry  # noqa: E402
from app import team_registry  # noqa: E402


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
        blueprint = role_registry.planning_team_blueprint()

        out = role_registry.register_team_blueprint(
            db=db,
            blueprint=blueprint,
            project_id="openteam",
            workstream_id="general",
            task_id="run-1",
        )

        self.assertEqual(set(out.keys()), set(role_registry.role_ids_for_team(blueprint)))
        self.assertEqual(len(db.calls), len(blueprint.members))
        self.assertIn(role_registry.ROLE_TEST_CASE_GAP_AGENT, role_registry.role_ids_for_team(blueprint))

    def test_delivery_team_blueprint_includes_dynamic_roles(self):
        blueprint = role_registry.delivery_team_blueprint(
            owner_role="Coding-Agent",
            review_role="Review-Agent",
            qa_role="QA-Agent",
            documentation_role="Documentation-Agent",
        )

        self.assertIn("Coding-Agent", role_registry.role_ids_for_team(blueprint))
        self.assertIn(role_registry.ROLE_BUG_REPRO_AGENT, role_registry.role_ids_for_team(blueprint))
        self.assertIn(role_registry.ROLE_BUG_TESTCASE_AGENT, role_registry.role_ids_for_team(blueprint))
        self.assertIn(role_registry.ROLE_SCHEDULER_AGENT, role_registry.role_ids_for_team(blueprint))
        self.assertIn(role_registry.ROLE_RELEASE_AGENT, role_registry.role_ids_for_team(blueprint))
        self.assertEqual(blueprint.team_id, role_registry.STAGE_DELIVERY)

    def test_generic_coding_role_loads_from_yaml(self):
        spec = role_registry.get_role_spec(role_registry.ROLE_CODING_AGENT)

        self.assertEqual(spec.display_name_zh, "通用编码 Agent")
        self.assertEqual(spec.tool_profile, "write")

    def test_loaded_role_spec_exposes_display_name_from_yaml(self):
        spec = role_registry.get_role_spec(role_registry.ROLE_REVIEW_AGENT)

        self.assertEqual(spec.display_name_zh, "评审 Agent")
        self.assertEqual(spec.tool_profile, "read")

    def test_get_role_spec_supports_custom_role_with_fallback(self):
        spec = role_registry.get_role_spec("Custom-Review-Agent", fallback_role_id=role_registry.ROLE_REVIEW_AGENT)

        self.assertEqual(spec.role_id, "Custom-Review-Agent")
        self.assertEqual(spec.display_name_zh, "评审 Agent")
        self.assertEqual(spec.tool_profile, "read")
        self.assertTrue(spec.goal)

    def test_issue_audit_role_uses_qa_tool_profile(self):
        spec = role_registry.get_role_spec(role_registry.ROLE_ISSUE_AUDIT_AGENT)

        self.assertEqual(spec.tool_profile, "qa")
        self.assertIn("reject work", spec.goal)

    def test_bug_validation_roles_load_from_yaml(self):
        repro = role_registry.get_role_spec(role_registry.ROLE_BUG_REPRO_AGENT)
        testcase = role_registry.get_role_spec(role_registry.ROLE_BUG_TESTCASE_AGENT)

        self.assertEqual(repro.tool_profile, "qa")
        self.assertEqual(testcase.tool_profile, "write")
        self.assertIn("reproducible", repro.goal)
        self.assertIn("failing automated test", testcase.goal)

    def test_repo_improvement_team_doc_loads_from_nested_path(self):
        team_id = team_registry.default_team_id()
        doc = spec_loader.team_doc(team_id)

        self.assertEqual(doc.get("team_id"), team_id)
        self.assertIn(role_registry.WORKFLOW_FEATURE_FINDING, doc.get("workflow_ids") or [])
        self.assertIn(role_registry.WORKFLOW_CODING, doc.get("workflow_ids") or [])
        self.assertIn(role_registry.ROLE_TEST_CASE_GAP_AGENT, doc.get("role_pool") or [])
        self.assertIn(role_registry.ROLE_CODING_AGENT, doc.get("role_pool") or [])

    def test_team_stage_and_workflow_docs_load_from_nested_path(self):
        team_id = team_registry.default_team_id()
        stage = spec_loader.team_stage_doc(team_id, role_registry.STAGE_PLANNING)
        workflow = spec_loader.team_workflow_doc(team_id, role_registry.WORKFLOW_CODING)

        self.assertEqual(stage.get("stage_id"), role_registry.STAGE_PLANNING)
        self.assertTrue(stage.get("members"))
        self.assertEqual(workflow.get("workflow_id"), role_registry.WORKFLOW_CODING)
        self.assertEqual(workflow.get("stages"), [role_registry.STAGE_DELIVERY])
        self.assertIn(role_registry.ROLE_TEST_CASE_GAP_AGENT, [str(x.get("role_id") or "") for x in (stage.get("members") or []) if isinstance(x, dict)])

    def test_test_case_gap_role_loads_from_yaml(self):
        spec = role_registry.get_role_spec(role_registry.ROLE_TEST_CASE_GAP_AGENT)

        self.assertEqual(spec.display_name_zh, "测试缺口分析 Agent")
        self.assertIn("black-box", spec.goal)

    def test_get_role_spec_prefers_team_local_role_doc(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            global_roles = root / "role_library" / "specs"
            team_roles = root / "teams" / "demo_team" / "specs" / "roles"
            global_roles.mkdir(parents=True, exist_ok=True)
            team_roles.mkdir(parents=True, exist_ok=True)
            (global_roles / "shared-role.yaml").write_text(
                "\n".join(
                    [
                        "role_id: Shared-Agent",
                        "display_name_zh: 全局角色",
                        "goal: global goal",
                        "backstory: global backstory",
                        "tool_profile: read",
                    ]
                ),
                encoding="utf-8",
            )
            (team_roles / "shared-role.yaml").write_text(
                "\n".join(
                    [
                        "role_id: Shared-Agent",
                        "display_name_zh: 团队角色",
                        "goal: team goal",
                        "backstory: team backstory",
                        "tool_profile: write",
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch.object(spec_loader, "role_specs_root", return_value=global_roles), mock.patch.object(
                spec_loader,
                "teams_root",
                return_value=root / "teams",
            ):
                spec_loader.clear_spec_caches()
                team_spec = role_registry.get_role_spec("Shared-Agent", team_id="demo-team")
                global_spec = role_registry.get_role_spec("Shared-Agent")

            self.assertEqual(team_spec.display_name_zh, "团队角色")
            self.assertEqual(team_spec.tool_profile, "write")
            self.assertEqual(global_spec.display_name_zh, "全局角色")
            self.assertEqual(global_spec.tool_profile, "read")
        spec_loader.clear_spec_caches()


if __name__ == "__main__":
    unittest.main()
