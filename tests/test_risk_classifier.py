import os
import sys
import unittest


def _add_pipelines_to_syspath():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = os.path.join(repo_root, "scripts", "pipelines")
    if p not in sys.path:
        sys.path.insert(0, p)


_add_pipelines_to_syspath()

from approvals import risk_classify  # noqa: E402


class RiskClassifierTests(unittest.TestCase):
    def test_known_high_risk_kind(self):
        out = risk_classify(action_kind="repo_create", action_summary="gh repo create x", payload={})
        self.assertEqual(out["risk_level"], "HIGH")
        self.assertEqual(out["category"], "GITHUB_REPO_CREATE")

    def test_hub_expose_is_high_risk(self):
        out = risk_classify(action_kind="hub_expose_remote_access", action_summary="openteam hub expose ...", payload={})
        self.assertEqual(out["risk_level"], "HIGH")
        self.assertEqual(out["category"], "PUBLIC_PORT")

    def test_known_low_risk_kind(self):
        out = risk_classify(action_kind="doctor", action_summary="openteam doctor", payload={})
        self.assertEqual(out["risk_level"], "LOW")

    def test_unknown_defaults_high(self):
        out = risk_classify(action_kind="something_new", action_summary="x", payload={})
        self.assertEqual(out["risk_level"], "HIGH")
        self.assertEqual(out["category"], "UNKNOWN")
        self.assertIn("unknown_kind", out.get("reasons") or [])


if __name__ == "__main__":
    unittest.main()
