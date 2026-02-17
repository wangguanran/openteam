import os
import sys
import unittest


def _add_template_app_to_syspath():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, ".team-os", "templates", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import cluster_manager  # noqa: E402


class CentralModelAllowlistTests(unittest.TestCase):
    def test_load_allowlist_not_empty(self):
        allow = cluster_manager.load_central_model_allowlist()
        self.assertTrue(isinstance(allow, list))
        self.assertTrue(len(allow) >= 1)

    def test_qualify_denies_missing_model_id(self):
        allow = ["gpt-5"]
        out = cluster_manager.qualify_leader(allowlist=allow, profile={"provider": "codex", "model_id": "", "auth_mode": "oauth"})
        self.assertFalse(out["qualified"])
        self.assertEqual(out["reason"], "missing_model_id")

    def test_qualify_allows_listed_model(self):
        allow = ["gpt-5"]
        out = cluster_manager.qualify_leader(allowlist=allow, profile={"provider": "codex", "model_id": "gpt-5", "auth_mode": "oauth"})
        self.assertTrue(out["qualified"])
        self.assertEqual(out["reason"], "allowed")


if __name__ == "__main__":
    unittest.main()

