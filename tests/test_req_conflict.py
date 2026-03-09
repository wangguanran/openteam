import os
import sys
import unittest


def _add_template_app_to_syspath():
    # Import conflict detector from the runtime template (source of truth for control-plane logic).
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app.req_conflict import detect_conflicts, detect_duplicate, infer_workstreams  # noqa: E402


class ReqConflictTests(unittest.TestCase):
    def test_duplicate_detects_high_similarity(self):
        existing = [
            {"req_id": "REQ-0001", "text": "必须默认使用 OAuth 作为认证方式，不得回退成 API KEY。", "status": "ACTIVE"},
        ]
        dup = detect_duplicate(existing, "必须默认使用 OAuth 作为认证方式，不得回退成 API KEY。")
        self.assertEqual(dup, "REQ-0001")

    def test_conflict_detects_must_vs_must_not(self):
        existing = [
            {"req_id": "REQ-0001", "text": "必须默认使用 Codex CLI 的 ChatGPT OAuth（codex login）作为认证方式。", "status": "ACTIVE"},
        ]
        findings = detect_conflicts(existing, "禁止 OAuth；必须使用 API key。")
        self.assertTrue(any(f.req_id == "REQ-0001" and f.topic == "auth.oauth" for f in findings))

    def test_workstream_inference(self):
        ws = infer_workstreams("实现一个 web 前端页面，并提供后端 API。")
        self.assertIn("web", ws)
        self.assertIn("backend", ws)


if __name__ == "__main__":
    unittest.main()
