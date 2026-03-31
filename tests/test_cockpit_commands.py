import unittest
from unittest import mock

from openteam_cli import cockpit_state
from openteam_cli import cockpit_commands


class CockpitCommandTests(unittest.TestCase):
    def test_load_request_uses_delivery_request_endpoint(self) -> None:
        with mock.patch("openteam_cli.cockpit_commands._http_json", return_value={"request_id": "REQ-1"}) as http:
            out = cockpit_commands.load_request(
                base_url="http://127.0.0.1:8787",
                team_id="delivery-studio",
                request_id="REQ-1",
            )

        self.assertEqual(out["request_id"], "REQ-1")
        http.assert_called_once_with(
            "GET",
            "http://127.0.0.1:8787/v1/teams/delivery-studio/requests/REQ-1",
        )

    def test_execute_propose_posts_final_proposal(self) -> None:
        routed = cockpit_state.route_input("/propose 这是最终提案")
        with mock.patch(
            "openteam_cli.cockpit_commands._http_json",
            return_value={"request_id": "REQ-1", "stage": "Awaiting Approval"},
        ) as http:
            out = cockpit_commands.execute_input(
                base_url="http://127.0.0.1:8787",
                team_id="delivery-studio",
                project_id="demo",
                request_id="REQ-1",
                explicit_run_id="",
                routed=routed,
            )

        self.assertEqual(out["kind"], "request")
        self.assertEqual(out["request"]["stage"], "Awaiting Approval")
        http.assert_called_once_with(
            "POST",
            "http://127.0.0.1:8787/v1/teams/delivery-studio/requests/REQ-1/awaiting-approval",
            {"project_id": "demo", "final_proposal": "这是最终提案"},
        )

    def test_execute_approve_posts_selected_option(self) -> None:
        routed = cockpit_state.route_input("/approve 方案B")
        with mock.patch(
            "openteam_cli.cockpit_commands._http_json",
            return_value={"request_id": "REQ-1", "stage": "Locked"},
        ) as http:
            out = cockpit_commands.execute_input(
                base_url="http://127.0.0.1:8787",
                team_id="delivery-studio",
                project_id="demo",
                request_id="REQ-1",
                explicit_run_id="",
                routed=routed,
            )

        self.assertEqual(out["kind"], "request")
        self.assertEqual(out["request"]["stage"], "Locked")
        http.assert_called_once_with(
            "POST",
            "http://127.0.0.1:8787/v1/teams/delivery-studio/requests/REQ-1/approve",
            {"project_id": "demo", "selected_option": "方案B"},
        )

    def test_execute_review_block_builds_three_reviewer_outputs(self) -> None:
        routed = cockpit_state.route_input("/review block 缺少契约测试")
        with mock.patch(
            "openteam_cli.cockpit_commands._http_json",
            return_value={"request_id": "REQ-1", "stage": "Changes Requested"},
        ) as http:
            out = cockpit_commands.execute_input(
                base_url="http://127.0.0.1:8787",
                team_id="delivery-studio",
                project_id="demo",
                request_id="REQ-1",
                explicit_run_id="",
                routed=routed,
            )

        self.assertEqual(out["kind"], "request")
        payload = http.call_args.args[2]
        self.assertEqual(payload["project_id"], "demo")
        self.assertEqual(len(payload["reviewer_outputs"]), 3)
        self.assertEqual(payload["reviewer_outputs"][1]["decision"], "BLOCK")
        self.assertEqual(payload["reviewer_outputs"][1]["blocking_issues"], ["缺少契约测试"])
        self.assertTrue(payload["reviewer_outputs"][0]["test_complete"])

    def test_execute_watch_resolves_run_id(self) -> None:
        routed = cockpit_state.route_input("/watch 45")
        with mock.patch("openteam_cli.cockpit_commands._resolve_team_watch_run_id", return_value="run-123") as resolve:
            out = cockpit_commands.execute_input(
                base_url="http://127.0.0.1:8787",
                team_id="delivery-studio",
                project_id="demo",
                request_id="REQ-1",
                explicit_run_id="",
                routed=routed,
            )

        self.assertEqual(out["kind"], "watch")
        self.assertEqual(out["run_id"], "run-123")
        self.assertEqual(out["timeout_sec"], 45)
        resolve.assert_called_once()

    def test_execute_agent_message_posts_chat_message(self) -> None:
        routed = cockpit_state.route_input("@reviewer-b 请重点检查测试完整性")
        with mock.patch("openteam_cli.cockpit_commands._http_json", return_value={"ok": True}) as http:
            out = cockpit_commands.execute_input(
                base_url="http://127.0.0.1:8787",
                team_id="delivery-studio",
                project_id="demo",
                request_id="REQ-1",
                explicit_run_id="run-123",
                routed=routed,
            )

        self.assertEqual(out["kind"], "message")
        self.assertEqual(out["message"]["target_agent"], "reviewer-b")
        http.assert_called_once_with(
            "POST",
            "http://127.0.0.1:8787/v1/chat",
            {
                "project_id": "demo",
                "run_id": "run-123",
                "message": "@reviewer-b 请重点检查测试完整性",
                "message_type": "GENERAL",
            },
        )


if __name__ == "__main__":
    unittest.main()
