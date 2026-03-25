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
from app import task_registry  # noqa: E402
from app.task_models import DeliveryBugReproResult, DeliveryBugTestCaseResult, DeliveryReviewResult  # noqa: E402


class CrewAITaskRegistryTests(unittest.TestCase):
    def test_delivery_review_task_spec_uses_review_model(self):
        spec = task_registry.DELIVERY_REVIEW_TASK_SPEC

        self.assertEqual(spec.output_model, DeliveryReviewResult)
        self.assertIn("code_approved", spec.render_description(payload="{}"))
        self.assertEqual(spec.task_name, "review_team_task")

    def test_registered_task_renders_payload(self):
        spec = task_registry.DELIVERY_AUDIT_TASK_SPEC

        text = spec.render_description(payload='{"task_id":"T-1"}')

        self.assertIn('{"task_id":"T-1"}', text)
        self.assertIn("docs_required", text)
        self.assertIn("reproduction_steps", text)
        self.assertIn("test_case_files", text)
        self.assertIn("verification_steps", text)

    def test_get_task_spec_loads_yaml_backed_model_mapping(self):
        spec = task_registry.get_task_spec("document_team_task")

        self.assertEqual(spec.output_model.__name__, "DeliveryDocumentationResult")
        self.assertIn("documentation_policy.allowed_paths", spec.render_description(payload="{}"))

    def test_bug_validation_task_specs_load(self):
        repro = task_registry.get_task_spec("reproduce_bug_before_fix")
        testcase = task_registry.get_task_spec("bootstrap_bug_testcase")

        self.assertEqual(repro.output_model, DeliveryBugReproResult)
        self.assertEqual(testcase.output_model, DeliveryBugTestCaseResult)
        self.assertIn("reproduction_commands", repro.render_description(payload="{}"))
        self.assertIn("test_case_files", testcase.render_description(payload="{}"))

    def test_get_task_spec_prefers_team_local_task_doc(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            default_tasks = root / "teams" / "aaa_default" / "specs" / "tasks"
            team_tasks = root / "teams" / "demo_team" / "specs" / "tasks"
            default_tasks.mkdir(parents=True, exist_ok=True)
            team_tasks.mkdir(parents=True, exist_ok=True)
            (default_tasks / "shared-task.yaml").write_text(
                "\n".join(
                    [
                        "task_name: shared_task",
                        "expected_output: global output",
                        "description_template: Global {payload}",
                        "output_model: DeliveryReviewResult",
                    ]
                ),
                encoding="utf-8",
            )
            (team_tasks / "shared-task.yaml").write_text(
                "\n".join(
                    [
                        "task_name: shared_task",
                        "expected_output: team output",
                        "description_template: Team {payload}",
                        "output_model: DeliveryReviewResult",
                    ]
                ),
                encoding="utf-8",
            )
            with mock.patch.object(spec_loader, "teams_root", return_value=root / "teams"):
                spec_loader.clear_spec_caches()
                team_spec = task_registry.get_task_spec("shared_task", team_id="demo-team")
                global_spec = task_registry.get_task_spec("shared_task")

            self.assertIn("Team {}", team_spec.render_description(payload="{}"))
            self.assertIn("Global {}", global_spec.render_description(payload="{}"))
        spec_loader.clear_spec_caches()


if __name__ == "__main__":
    unittest.main()
