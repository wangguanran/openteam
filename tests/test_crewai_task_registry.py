import os
import sys
import unittest


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import crewai_task_registry  # noqa: E402
from app.crewai_task_models import DeliveryReviewResult  # noqa: E402


class CrewAITaskRegistryTests(unittest.TestCase):
    def test_delivery_review_task_spec_uses_review_model(self):
        spec = crewai_task_registry.DELIVERY_REVIEW_TASK_SPEC

        self.assertEqual(spec.output_model, DeliveryReviewResult)
        self.assertIn("code_approved", spec.render_description(payload="{}"))
        self.assertEqual(spec.task_name, "review_self_upgrade_task")

    def test_registered_task_renders_payload(self):
        spec = crewai_task_registry.DELIVERY_AUDIT_TASK_SPEC

        text = spec.render_description(payload='{"task_id":"T-1"}')

        self.assertIn('{"task_id":"T-1"}', text)
        self.assertIn("docs_required", text)
        self.assertIn("reproduction_steps", text)
        self.assertIn("test_case_files", text)
        self.assertIn("verification_steps", text)

    def test_get_task_spec_loads_yaml_backed_model_mapping(self):
        spec = crewai_task_registry.get_task_spec("document_self_upgrade_task")

        self.assertEqual(spec.output_model.__name__, "DeliveryDocumentationResult")
        self.assertIn("documentation_policy.allowed_paths", spec.render_description(payload="{}"))


if __name__ == "__main__":
    unittest.main()
