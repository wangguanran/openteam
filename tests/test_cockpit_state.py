import unittest

from openteam_cli import cockpit_state


class CockpitStateTests(unittest.TestCase):
    def test_snapshot_shapes_three_panes(self) -> None:
        snapshot = cockpit_state.build_snapshot(
            request={
                "request_id": "REQ-1001",
                "stage": "Discussing",
                "needs_you": False,
                "blocked": False,
                "review_gate": "Pending",
                "ci": "Pending",
                "pr": "",
                "workstreams": {"mobile": "idle", "admin": "idle", "backend": "idle"},
            },
            agents=[
                {"agent_id": "moderator", "role": "Moderator", "model": "GPT-5.4", "status": "discussing"},
                {"agent_id": "product_architect", "role": "Product-Architect", "model": "Opus 4.6", "status": "discussing"},
            ],
            messages=[
                {
                    "actor": "moderator",
                    "role": "Moderator",
                    "model": "GPT-5.4",
                    "stage": "Discussing",
                    "category": "Discussion",
                    "text": "先明确范围。",
                },
            ],
        )

        self.assertEqual(snapshot.left[0].agent_id, "moderator")
        self.assertEqual(snapshot.center[0].role, "Moderator")
        self.assertEqual(snapshot.right.request_id, "REQ-1001")

    def test_snapshot_workstreams_are_immutable(self) -> None:
        request = {
            "request_id": "REQ-1001",
            "stage": "Discussing",
            "needs_you": False,
            "blocked": False,
            "review_gate": "Pending",
            "ci": "Pending",
            "pr": "",
            "workstreams": {"mobile": "idle", "admin": "idle"},
        }
        snapshot = cockpit_state.build_snapshot(request=request, agents=[], messages=[])

        request["workstreams"]["mobile"] = "running"
        self.assertEqual(snapshot.right.workstreams["mobile"], "idle")

        with self.assertRaises(TypeError):
            snapshot.right.workstreams["mobile"] = "running"

    def test_route_input_supports_agent_mentions_and_commands(self) -> None:
        routed = cockpit_state.route_input("@app-ui-designer 给我 3 套方案")
        self.assertEqual(routed["mode"], "agent")
        self.assertEqual(routed["target"], "app-ui-designer")

        command = cockpit_state.route_input("/approve")
        self.assertEqual(command["mode"], "command")
        self.assertEqual(command["target"], "approve")

    def test_route_input_splits_command_arguments(self) -> None:
        command = cockpit_state.route_input("/watch 30")
        self.assertEqual(command["mode"], "command")
        self.assertEqual(command["target"], "watch")
        self.assertEqual(command["text"], "30")


if __name__ == "__main__":
    unittest.main()
