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

from app.domains.delivery_studio import review_gate  # noqa: E402


class DeliveryStudioReviewGateTests(unittest.TestCase):
    def test_block_decision_without_explicit_issues_still_blocks_gate(self) -> None:
        result = review_gate.evaluate_review_gate(
            reviewer_outputs=[
                {
                    "reviewer_id": "reviewer-a",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
                {
                    "reviewer_id": "reviewer-b",
                    "decision": "BLOCK",
                    "blocking_issues": [],
                    "test_complete": True,
                },
                {
                    "reviewer_id": "reviewer-c",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
            ]
        )
        self.assertEqual(result["review_gate"], "Blocked")
        self.assertEqual(result["blocking_gate"], "failure")
        self.assertIn("reviewer-b", result["blocked_reason"])

    def test_any_blocking_reviewer_fails_gate(self) -> None:
        result = review_gate.evaluate_review_gate(
            reviewer_outputs=[
                {
                    "reviewer_id": "reviewer-a",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
                {
                    "reviewer_id": "reviewer-b",
                    "decision": "BLOCK",
                    "blocking_issues": ["missing contract tests"],
                    "test_complete": False,
                },
                {
                    "reviewer_id": "reviewer-c",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
            ]
        )
        self.assertEqual(result["review_gate"], "Blocked")
        self.assertEqual(result["blocking_gate"], "failure")
        self.assertIn("missing contract tests", result["blocked_reason"])

    def test_missing_test_completeness_blocks_even_when_reviewer_text_is_soft(self) -> None:
        result = review_gate.evaluate_review_gate(
            reviewer_outputs=[
                {
                    "reviewer_id": "reviewer-a",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
                {
                    "reviewer_id": "reviewer-b",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": False,
                },
                {
                    "reviewer_id": "reviewer-c",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
            ]
        )
        self.assertEqual(result["review_gate"], "Blocked")
        self.assertIn("test completeness", result["blocked_reason"])

    def test_check_run_payload_names_are_stable(self) -> None:
        payloads = review_gate.build_check_runs(
            request_id="REQ-1234",
            reviewer_outputs=[
                {
                    "reviewer_id": "reviewer-a",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
                {
                    "reviewer_id": "reviewer-b",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
                {
                    "reviewer_id": "reviewer-c",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": True,
                },
            ],
        )
        names = [item["name"] for item in payloads]
        self.assertIn("panel-review/reviewer-a", names)
        self.assertIn("panel-review/reviewer-b", names)
        self.assertIn("panel-review/reviewer-c", names)
        self.assertIn("panel-review/blocking-gate", names)

    def test_incomplete_reviewer_check_fails_and_reports_test_gap(self) -> None:
        payloads = review_gate.build_check_runs(
            request_id="REQ-1234",
            reviewer_outputs=[
                {
                    "reviewer_id": "reviewer-a",
                    "decision": "PASS",
                    "blocking_issues": [],
                    "test_complete": False,
                }
            ],
        )

        reviewer_check = next(item for item in payloads if item["name"] == "panel-review/reviewer-a")
        self.assertEqual(reviewer_check["conclusion"], "failure")
        self.assertIn("test completeness", reviewer_check["output"]["summary"])

    def test_blocking_reviewer_without_issue_list_does_not_report_pass(self) -> None:
        payloads = review_gate.build_check_runs(
            request_id="REQ-1234",
            reviewer_outputs=[
                {
                    "reviewer_id": "reviewer-a",
                    "decision": "BLOCK",
                    "blocking_issues": [],
                    "test_complete": True,
                }
            ],
        )

        reviewer_check = next(item for item in payloads if item["name"] == "panel-review/reviewer-a")
        self.assertEqual(reviewer_check["conclusion"], "failure")
        self.assertNotEqual(reviewer_check["output"]["summary"], "PASS")
        self.assertIn("vetoed", reviewer_check["output"]["summary"])


if __name__ == "__main__":
    unittest.main()
