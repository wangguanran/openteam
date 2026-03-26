from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CiWorkflowTests(unittest.TestCase):
    def test_main_ci_does_not_swallow_failures(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("|| true", text)

    def test_runtime_ci_references_existing_runtime_auto_update_test(self) -> None:
        text = (ROOT / ".github" / "workflows" / "runtime-ci.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_runtime_auto_update", text)
        self.assertNotIn("tests.test_crewai_self_upgrade", text)


if __name__ == "__main__":
    unittest.main()
