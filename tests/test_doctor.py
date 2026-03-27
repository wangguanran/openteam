import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    pipelines_dir = repo_root / "scripts" / "pipelines"
    sys.path.insert(0, str(pipelines_dir))
    try:
        script = pipelines_dir / "doctor.py"
        loader = importlib.machinery.SourceFileLoader("doctor_test_module", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


class DoctorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_llm_config_accepts_codex_oauth_without_api_key(self):
        with mock.patch.object(self.mod, "_codex_status", return_value=(True, "Logged in using ChatGPT")), mock.patch.dict(
            os.environ,
            {"OPENTEAM_LLM_MODEL": "openai-codex/gpt-5.4"},
            clear=True,
        ):
            cfg = self.mod._llm_config_check()

        self.assertTrue(bool(cfg.get("ok")))
        self.assertTrue(bool(cfg.get("codex_oauth_ready")))
        self.assertEqual(cfg.get("auth_strategy"), "codex_oauth")
        self.assertEqual(cfg.get("model"), "openai-codex/gpt-5.4")


if __name__ == "__main__":
    unittest.main()
