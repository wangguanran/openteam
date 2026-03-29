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

from app import panel_github_sync  # noqa: E402
from app import workspace_store  # noqa: E402
from app.domains.delivery_studio import runtime as delivery_runtime  # noqa: E402
from app.runtime_db import RuntimeDB  # noqa: E402


class DeliveryStudioPanelProjectionTests(unittest.TestCase):
    def test_delivery_request_projects_to_single_main_card(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            os.environ["OPENTEAM_RUNTIME_ROOT"] = str(Path(td) / "runtime")
            os.environ["OPENTEAM_WORKSPACE_ROOT"] = str(Path(td) / "workspace")
            workspace_store.ensure_project_scaffold("demo")
            req = delivery_runtime.create_request(
                project_id="demo",
                title="Booking app",
                text="Need app, admin, backend.",
                created_by="user",
            )

            mapping = panel_github_sync.MappingDoc(
                path=Path(td) / "mapping.yaml",
                sha256="test",
                data={"projects": {"demo": {"fields": {}}}},
            )
            items = panel_github_sync._delivery_request_items(project_id="demo", mapping=mapping, db=RuntimeDB(":memory:"))

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].key, req["request_id"])
            self.assertEqual(items[0].field_values["stage"], "Discussing")
            self.assertEqual(items[0].field_values["needs_you"], "No")
            self.assertEqual(items[0].field_values["request_id"], req["request_id"])


if __name__ == "__main__":
    unittest.main()
