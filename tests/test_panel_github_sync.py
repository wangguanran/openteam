import os
import sys
import unittest


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import panel_github_sync  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
