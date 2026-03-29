import os
import sys
import tempfile
import unittest
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_template_app_to_syspath() -> None:
    app_dir = _repo_root() / "scaffolds" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


_add_template_app_to_syspath()

from app.domains.delivery_studio import runtime as delivery_runtime  # noqa: E402
from app import workspace_store  # noqa: E402


class DeliveryStudioRuntimeTests(unittest.TestCase):
    def test_create_request_persists_under_workspace_and_logs_intake(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["OPENTEAM_RUNTIME_ROOT"] = str(Path(td) / "runtime")
            os.environ["OPENTEAM_WORKSPACE_ROOT"] = str(Path(td) / "workspace")
            workspace_store.ensure_project_scaffold("demo")

            req = delivery_runtime.create_request(
                project_id="demo",
                title="Build a booking product",
                text="Need mobile, admin, backend, and UI options.",
                created_by="user",
            )

            self.assertEqual(req["stage"], "Discussing")
            self.assertTrue(req["needs_you"] is False)
            self.assertTrue(Path(req["request_path"]).exists())
            self.assertTrue("workspace/projects/demo/state/delivery_studio/requests" in req["request_path"])
            self.assertTrue(Path(req["artifacts"]["raw_record"]).exists())

    def test_approval_locks_request_and_creates_approval_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["OPENTEAM_RUNTIME_ROOT"] = str(Path(td) / "runtime")
            os.environ["OPENTEAM_WORKSPACE_ROOT"] = str(Path(td) / "workspace")
            workspace_store.ensure_project_scaffold("demo")
            req = delivery_runtime.create_request(
                project_id="demo",
                title="Booking app",
                text="Need three UI options before coding.",
                created_by="user",
            )

            delivery_runtime.mark_awaiting_approval(
                project_id="demo",
                request_id=req["request_id"],
                final_proposal="Option B is recommended.",
            )
            locked = delivery_runtime.approve_request(
                project_id="demo",
                request_id=req["request_id"],
                approved_by="user",
                selected_option="option-b",
            )

            self.assertEqual(locked["stage"], "Locked")
            self.assertFalse(locked["needs_you"])
            self.assertEqual(locked["selected_option"], "option-b")
            self.assertTrue(Path(locked["artifacts"]["approval_record"]).exists())

    def test_change_request_creates_new_request_linked_to_parent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["OPENTEAM_RUNTIME_ROOT"] = str(Path(td) / "runtime")
            os.environ["OPENTEAM_WORKSPACE_ROOT"] = str(Path(td) / "workspace")
            workspace_store.ensure_project_scaffold("demo")
            req = delivery_runtime.create_request(
                project_id="demo",
                title="Booking app",
                text="Base scope",
                created_by="user",
            )
            child = delivery_runtime.create_change_request(
                project_id="demo",
                parent_request_id=req["request_id"],
                text="Add a waitlist management screen.",
                created_by="user",
            )

            self.assertNotEqual(child["request_id"], req["request_id"])
            self.assertEqual(child["change_request_of"], req["request_id"])
            self.assertEqual(child["stage"], "Discussing")


if __name__ == "__main__":
    unittest.main()
