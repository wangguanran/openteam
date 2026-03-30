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

from app import panel_github_sync  # noqa: E402
from app import workspace_store  # noqa: E402
from app.domains.delivery_studio import runtime as delivery_runtime  # noqa: E402
from app.runtime_db import RuntimeDB  # noqa: E402


class PanelGitHubSyncTests(unittest.TestCase):
    def test_panel_item_title_reuses_issue_style_titles(self):
        self.assertEqual(
            panel_github_sync._panel_item_title("[Bug][Runtime] 修复启动回归", kind="BUG", lane="bug", module="Runtime"),
            "[Bug][Runtime] 修复启动回归",
        )

    def test_panel_item_title_formats_plain_titles_with_type_and_module(self):
        self.assertEqual(
            panel_github_sync._panel_item_title("增加提案闭环", kind="PROCESS", lane="process", module="Self-Upgrade"),
            "[Process][Self-Upgrade] 增加提案闭环",
        )

    def test_panel_item_title_formats_quality_titles(self):
        self.assertEqual(
            panel_github_sync._panel_item_title("删除未引用的旧适配文件", kind="CODE_QUALITY", lane="quality", module="Runtime"),
            "[Quality][Runtime] 删除未引用的旧适配文件",
        )

    def test_panel_milestone_title_uses_release_issue_style(self):
        self.assertEqual(
            panel_github_sync._panel_item_title("跟踪 v0.1.1 版本发布", kind="PROCESS", lane="process", module="Release"),
            "[Process][Release] 跟踪 v0.1.1 版本发布",
        )

    def test_milestone_status_key_maps_release_candidate_to_in_review(self):
        self.assertEqual(panel_github_sync._milestone_status_key("release-candidate"), "IN_REVIEW")
        self.assertEqual(panel_github_sync._milestone_status_key("active"), "IN_PROGRESS")
        self.assertEqual(panel_github_sync._milestone_status_key("blocked"), "BLOCKED")

    def test_sync_updates_delivery_request_fields_from_field_values(self):
        mapping = panel_github_sync.MappingDoc(
            path=Path("mapping.yaml"),
            sha256="test",
            data={
                "github": {"graphql_api_url": "https://example.test/graphql"},
                "projects": {
                    "demo": {
                        "project_id": "demo",
                        "owner_type": "USER",
                        "owner": "demo-user",
                        "project_node_id": "PROJECT-1",
                        "fields": {
                            "task_id": {"name": "Task ID", "type": "TEXT", "field_id": ""},
                            "request_id": {"name": "Request ID", "type": "TEXT", "field_id": ""},
                            "stage": {
                                "name": "Stage",
                                "type": "SINGLE_SELECT",
                                "field_id": "",
                                "options": {
                                    "DISCUSSING": {"name": "Discussing", "option_id": ""},
                                    "AWAITING_APPROVAL": {"name": "Awaiting Approval", "option_id": ""},
                                },
                            },
                            "needs_you": {
                                "name": "Needs You",
                                "type": "SINGLE_SELECT",
                                "field_id": "",
                                "options": {
                                    "YES": {"name": "Yes", "option_id": ""},
                                    "NO": {"name": "No", "option_id": ""},
                                },
                            },
                        },
                    }
                },
            },
        )
        field_updates: list[dict[str, object]] = []

        class FakeGraphQL:
            def __init__(self, *, token: str, api_url: str) -> None:
                self.token = token
                self.api_url = api_url

            def graphql(self, query: str, variables: dict[str, object]) -> dict[str, object]:
                if query == panel_github_sync.PROJECT_FIELDS_QUERY:
                    return {
                        "node": {
                            "fields": {
                                "nodes": [
                                    {"id": "F-TASK-ID", "name": "Task ID", "dataType": "TEXT", "__typename": "ProjectV2Field"},
                                    {"id": "F-REQUEST-ID", "name": "Request ID", "dataType": "TEXT", "__typename": "ProjectV2Field"},
                                    {
                                        "id": "F-STAGE",
                                        "name": "Stage",
                                        "dataType": "SINGLE_SELECT",
                                        "__typename": "ProjectV2SingleSelectField",
                                        "options": [
                                            {"id": "OPT-STAGE-DISCUSSING", "name": "Discussing"},
                                            {"id": "OPT-STAGE-AWAITING", "name": "Awaiting Approval"},
                                        ],
                                    },
                                    {
                                        "id": "F-NEEDS-YOU",
                                        "name": "Needs You",
                                        "dataType": "SINGLE_SELECT",
                                        "__typename": "ProjectV2SingleSelectField",
                                        "options": [
                                            {"id": "OPT-YES", "name": "Yes"},
                                            {"id": "OPT-NO", "name": "No"},
                                        ],
                                    },
                                ]
                            }
                        }
                    }
                if query == panel_github_sync.PROJECT_ITEMS_QUERY:
                    return {
                        "node": {
                            "items": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                if query == panel_github_sync.ADD_DRAFT_ISSUE_MUTATION:
                    return {"addProjectV2DraftIssue": {"projectItem": {"id": "ITEM-1"}}}
                if query == panel_github_sync.UPDATE_ITEM_FIELD_MUTATION:
                    field_updates.append(variables)
                    return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": str(variables["itemId"])}}}
                raise AssertionError(f"unexpected query: {query[:60]}")

        desired_items = [
            panel_github_sync.DesiredItem(
                key="REQ-1234",
                kind="REQUEST",
                title="[REQ] REQ-1234 Booking app",
                body="Request ID: REQ-1234\n",
                field_values={
                    "request_id": "REQ-1234",
                    "stage": "Discussing",
                    "needs_you": "No",
                },
            )
        ]

        with (
            mock.patch.object(panel_github_sync, "load_mapping", return_value=mapping),
            mock.patch.object(panel_github_sync, "resolve_github_token", return_value="token"),
            mock.patch.object(panel_github_sync, "GitHubGraphQL", FakeGraphQL),
            mock.patch.object(panel_github_sync, "_desired_items", return_value=desired_items),
        ):
            result = panel_github_sync.GitHubProjectsPanelSync(db=RuntimeDB(":memory:")).sync(
                project_id="demo",
                mode="incremental",
                dry_run=False,
            )

        self.assertEqual(result["stats"]["created"], 1)
        self.assertEqual(result["stats"]["errors"], 0)
        updates_by_field = {str(call["fieldId"]): call["value"] for call in field_updates}
        self.assertEqual(updates_by_field["F-REQUEST-ID"], {"text": "REQ-1234"})
        self.assertEqual(updates_by_field["F-STAGE"], {"singleSelectOptionId": "OPT-STAGE-DISCUSSING"})
        self.assertEqual(updates_by_field["F-NEEDS-YOU"], {"singleSelectOptionId": "OPT-NO"})

    def test_sync_delivery_request_uses_mapping_option_names_for_single_select_fields(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["OPENTEAM_RUNTIME_ROOT"] = str(Path(td) / "runtime")
            os.environ["OPENTEAM_WORKSPACE_ROOT"] = str(Path(td) / "workspace")
            db = RuntimeDB(str(Path(td) / "runtime.db"))
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
                data={
                    "github": {"graphql_api_url": "https://example.test/graphql"},
                    "projects": {
                        "demo": {
                            "project_id": "demo",
                            "owner_type": "USER",
                            "owner": "demo-user",
                            "project_node_id": "PROJECT-1",
                            "fields": {
                                "request_id": {"name": "Request ID", "type": "TEXT", "field_id": ""},
                                "stage": {
                                    "name": "Stage",
                                    "type": "SINGLE_SELECT",
                                    "field_id": "",
                                    "options": {
                                        "DISCUSSING": {"name": "讨论中", "option_id": ""},
                                        "AWAITING_APPROVAL": {"name": "等待确认", "option_id": ""},
                                    },
                                },
                                "needs_you": {
                                    "name": "Needs You",
                                    "type": "SINGLE_SELECT",
                                    "field_id": "",
                                    "options": {
                                        "YES": {"name": "需要你", "option_id": ""},
                                        "NO": {"name": "无需你", "option_id": ""},
                                    },
                                },
                            },
                        }
                    },
                },
            )
            field_updates: list[dict[str, object]] = []

            class FakeGraphQL:
                def __init__(self, *, token: str, api_url: str) -> None:
                    self.token = token
                    self.api_url = api_url

                def graphql(self, query: str, variables: dict[str, object]) -> dict[str, object]:
                    if query == panel_github_sync.PROJECT_FIELDS_QUERY:
                        return {
                            "node": {
                                "fields": {
                                    "nodes": [
                                        {"id": "F-REQUEST-ID", "name": "Request ID", "dataType": "TEXT", "__typename": "ProjectV2Field"},
                                        {
                                            "id": "F-STAGE",
                                            "name": "Stage",
                                            "dataType": "SINGLE_SELECT",
                                            "__typename": "ProjectV2SingleSelectField",
                                            "options": [
                                                {"id": "OPT-STAGE-DISCUSSING", "name": "讨论中"},
                                                {"id": "OPT-STAGE-AWAITING", "name": "等待确认"},
                                            ],
                                        },
                                        {
                                            "id": "F-NEEDS-YOU",
                                            "name": "Needs You",
                                            "dataType": "SINGLE_SELECT",
                                            "__typename": "ProjectV2SingleSelectField",
                                            "options": [
                                                {"id": "OPT-YES", "name": "需要你"},
                                                {"id": "OPT-NO", "name": "无需你"},
                                            ],
                                        },
                                    ]
                                }
                            }
                        }
                    if query == panel_github_sync.PROJECT_ITEMS_QUERY:
                        return {
                            "node": {
                                "items": {
                                    "nodes": [],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                }
                            }
                        }
                    if query == panel_github_sync.ADD_DRAFT_ISSUE_MUTATION:
                        return {"addProjectV2DraftIssue": {"projectItem": {"id": "ITEM-1"}}}
                    if query == panel_github_sync.UPDATE_ITEM_FIELD_MUTATION:
                        field_updates.append(variables)
                        return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": str(variables["itemId"])}}}
                    raise AssertionError(f"unexpected query: {query[:60]}")

            with (
                mock.patch.object(panel_github_sync, "load_mapping", return_value=mapping),
                mock.patch.object(panel_github_sync, "resolve_github_token", return_value="token"),
                mock.patch.object(panel_github_sync, "GitHubGraphQL", FakeGraphQL),
            ):
                result = panel_github_sync.GitHubProjectsPanelSync(db=db).sync(
                    project_id="demo",
                    mode="incremental",
                    dry_run=False,
                )

            self.assertEqual(result["stats"]["created"], 1)
            self.assertEqual(result["stats"]["errors"], 0)
            updates_by_field = {str(call["fieldId"]): call["value"] for call in field_updates}
            self.assertEqual(updates_by_field["F-REQUEST-ID"], {"text": str(req["request_id"])})
            self.assertEqual(updates_by_field["F-STAGE"], {"singleSelectOptionId": "OPT-STAGE-DISCUSSING"})
            self.assertEqual(updates_by_field["F-NEEDS-YOU"], {"singleSelectOptionId": "OPT-NO"})


if __name__ == "__main__":
    unittest.main()
