import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "templates", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import crewai_runtime  # noqa: E402


class CrewAIRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = dict(os.environ)
        self._orig_syspath = list(sys.path)
        crewai_runtime._probe_crewai_cached.cache_clear()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)
        sys.path[:] = self._orig_syspath
        crewai_runtime._probe_crewai_cached.cache_clear()

    def test_probe_prefers_env_path_and_exposes_import_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            pkg = src / "crewai"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text("__version__='0.test'\n", encoding="utf-8")
            os.environ["TEAMOS_CREWAI_SRC_PATH"] = str(src)

            fake_mod = SimpleNamespace(__version__="0.test", __file__=str(pkg / "__init__.py"))
            with mock.patch("app.crewai_runtime.importlib.import_module", return_value=fake_mod) as mocked_import:
                info = crewai_runtime.probe_crewai(refresh=True)

            self.assertTrue(info["configured"])
            self.assertEqual(info["source_path"], str(src.resolve()))
            self.assertTrue(info["importable"])
            self.assertEqual(info["version"], "0.test")
            mocked_import.assert_called_once_with("crewai")

    def test_require_crewai_importable_raises_with_context_when_import_fails(self):
        os.environ["TEAMOS_CREWAI_SRC_PATH"] = "/tmp/path-that-does-not-exist"
        with mock.patch(
            "app.crewai_runtime.importlib.import_module",
            side_effect=ModuleNotFoundError("No module named 'crewai'"),
        ):
            info = crewai_runtime.probe_crewai(refresh=True)
            self.assertFalse(info["importable"])
            with self.assertRaises(crewai_runtime.CrewAIRuntimeError) as ctx:
                crewai_runtime.require_crewai_importable(refresh=True)

        msg = str(ctx.exception)
        self.assertIn("crewai import failed", msg)
        self.assertIn("candidates=", msg)


if __name__ == "__main__":
    unittest.main()
