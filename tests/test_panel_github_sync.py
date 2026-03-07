import os
import sys
import unittest


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "templates", "runtime", "orchestrator")
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


if __name__ == "__main__":
    unittest.main()
