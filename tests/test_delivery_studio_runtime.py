import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_template_app_to_syspath() -> None:
    app_dir = _repo_root() / "scaffolds" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


_add_template_app_to_syspath()


def _install_main_import_stubs() -> None:
    if "agents" not in sys.modules:
        agents_module = types.ModuleType("agents")

        class Agent:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

        agents_module.Agent = Agent
        sys.modules["agents"] = agents_module

    if "fastapi" not in sys.modules:
        fastapi_module = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail=None) -> None:
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class FastAPI:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

            def exception_handler(self, *args, **kwargs):
                def decorator(fn):
                    return fn

                return decorator

            def get(self, *args, **kwargs):
                def decorator(fn):
                    return fn

                return decorator

            def post(self, *args, **kwargs):
                def decorator(fn):
                    return fn

                return decorator

            def __getattr__(self, name):
                def decorator_factory(*args, **kwargs):
                    _ = args, kwargs, name

                    def decorator(fn):
                        return fn

                    return decorator

                return decorator_factory

        def Query(default=None, **kwargs):
            _ = kwargs
            return default

        class Request:
            pass

        class Response:
            pass

        fastapi_module.FastAPI = FastAPI
        fastapi_module.HTTPException = HTTPException
        fastapi_module.Query = Query
        fastapi_module.Request = Request
        fastapi_module.Response = Response
        fastapi_module.status = types.SimpleNamespace()
        sys.modules["fastapi"] = fastapi_module

        responses_module = types.ModuleType("fastapi.responses")

        class StreamingResponse:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

        class JSONResponse:
            def __init__(self, *args, **kwargs) -> None:
                self.args = args
                self.kwargs = kwargs

        responses_module.StreamingResponse = StreamingResponse
        responses_module.JSONResponse = JSONResponse
        sys.modules["fastapi.responses"] = responses_module


_install_main_import_stubs()

from fastapi import HTTPException  # noqa: E402
from app.domains.delivery_studio import runtime as delivery_runtime  # noqa: E402
from app import main as app_main  # noqa: E402
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

    def test_approving_with_differently_cased_request_id_reuses_same_artifact_tree(self) -> None:
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

            intake_task_dir = Path(req["artifacts"]["raw_record"]).parents[1]
            lower_request_id = str(req["request_id"]).lower()

            delivery_runtime.mark_awaiting_approval(
                project_id="demo",
                request_id=lower_request_id,
                final_proposal="Option B is recommended.",
            )
            locked = delivery_runtime.approve_request(
                project_id="demo",
                request_id=lower_request_id,
                approved_by="user",
                selected_option="option-b",
            )

            approval_task_dir = Path(locked["artifacts"]["approval_record"]).parents[1]
            self.assertEqual(locked["request_id"], req["request_id"])
            self.assertEqual(approval_task_dir, intake_task_dir)

    def test_approve_route_maps_missing_request_to_404(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["OPENTEAM_RUNTIME_ROOT"] = str(Path(td) / "runtime")
            os.environ["OPENTEAM_WORKSPACE_ROOT"] = str(Path(td) / "workspace")
            workspace_store.ensure_project_scaffold("demo")

            with self.assertRaises(HTTPException) as ctx:
                app_main.v1_team_request_approve(
                    "delivery-studio",
                    "REQ-MISSING",
                    app_main.DeliveryApprovalIn(project_id="demo", selected_option="option-b"),
                )

            self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
