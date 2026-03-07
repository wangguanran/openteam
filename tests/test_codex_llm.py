import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "templates", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import codex_llm  # noqa: E402


class CodexLLMTests(unittest.TestCase):
    def test_codex_login_status_falls_back_to_auth_json_when_cli_missing(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"CODEX_HOME": td}, clear=False):
            auth_path = Path(td) / "auth.json"
            auth_path.write_text(
                '{"auth_mode":"chatgpt","tokens":{"access_token":"x","refresh_token":"y"}}',
                encoding="utf-8",
            )
            with mock.patch("app.codex_llm._run", side_effect=FileNotFoundError("codex")):
                ok, msg = codex_llm.codex_login_status()

        self.assertTrue(ok)
        self.assertIn("auth.json", msg)


if __name__ == "__main__":
    unittest.main()
