import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
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
            self.assertGreaterEqual(mocked_import.call_count, 1)

    def test_probe_uses_installed_crewai_when_no_src_override_is_set(self):
        fake_mod = SimpleNamespace(__version__="1.10.0", __file__="/tmp/site-packages/crewai/__init__.py")
        with mock.patch("app.crewai_runtime.importlib.import_module", return_value=fake_mod):
            info = crewai_runtime.probe_crewai(refresh=True)

        self.assertFalse(info["configured"])
        self.assertEqual(info["source_path"], "")
        self.assertTrue(info["importable"])
        self.assertEqual(info["module_path"], "/tmp/site-packages/crewai/__init__.py")

    def test_probe_reloads_cached_crewai_when_existing_module_is_outside_selected_src(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)
            pkg = src / "crewai"
            pkg.mkdir(parents=True, exist_ok=True)
            init_py = pkg / "__init__.py"
            init_py.write_text("__version__='0.test'\n", encoding="utf-8")
            os.environ["TEAMOS_CREWAI_SRC_PATH"] = str(src)

            stale_mod = SimpleNamespace(__version__="0.old", __file__="/usr/local/lib/python3.11/site-packages/crewai/__init__.py")
            fresh_mod = SimpleNamespace(__version__="0.test", __file__=str(init_py))
            sys.modules["crewai"] = stale_mod
            sys.modules["crewai.foo"] = SimpleNamespace(__file__="/usr/local/lib/python3.11/site-packages/crewai/foo.py")

            def _fake_import(name: str):
                if name == "crewai":
                    self.assertNotIn("crewai.foo", sys.modules)
                    sys.modules["crewai"] = fresh_mod
                    return fresh_mod
                raise AssertionError(f"unexpected import: {name}")

            with mock.patch("app.crewai_runtime.importlib.import_module", side_effect=_fake_import):
                info = crewai_runtime.probe_crewai(refresh=True)

            self.assertEqual(info["module_path"], str(init_py))
            self.assertEqual(sys.modules["crewai"], fresh_mod)

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

    def test_suppress_crewai_first_time_tracing_prompt_marks_declined_preference(self):
        root = ModuleType("crewai")
        root.__path__ = []
        events = ModuleType("crewai.events")
        events.__path__ = []
        listeners = ModuleType("crewai.events.listeners")
        listeners.__path__ = []
        tracing = ModuleType("crewai.events.listeners.tracing")
        tracing.__path__ = []
        calls: list[tuple[str, object]] = []
        utils = ModuleType("crewai.events.listeners.tracing.utils")

        def _set_suppress(value: bool):
            calls.append(("suppress", value))
            return object()

        def _mark_done(*, user_consented: bool = False):
            calls.append(("mark", user_consented))

        utils.set_suppress_tracing_messages = _set_suppress
        utils.mark_first_execution_done = _mark_done

        with mock.patch.dict(
            sys.modules,
            {
                "crewai": root,
                "crewai.events": events,
                "crewai.events.listeners": listeners,
                "crewai.events.listeners.tracing": tracing,
                "crewai.events.listeners.tracing.utils": utils,
            },
            clear=False,
        ):
            self.assertTrue(crewai_runtime.suppress_crewai_first_time_tracing_prompt())

        self.assertEqual(calls, [("suppress", True), ("mark", False)])

    def test_prime_crewai_tracing_user_data_marks_first_execution_done(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir(parents=True, exist_ok=True)
            os.environ["HOME"] = str(home)
            os.environ["CREWAI_STORAGE_DIR"] = "teamos-test-crewai"

            self.assertTrue(crewai_runtime._prime_crewai_tracing_user_data())

            user_file = crewai_runtime._crewai_user_data_file()
            self.assertTrue(user_file.exists())
            payload = json.loads(user_file.read_text(encoding="utf-8"))
            self.assertTrue(payload["first_execution_done"])
            self.assertFalse(payload["trace_consent"])

    def test_suppress_proxy_for_codex_oauth_temporarily_unsets_proxy_envs(self):
        os.environ["HTTP_PROXY"] = "http://proxy.example"
        os.environ["HTTPS_PROXY"] = "http://proxy.example"
        os.environ["ALL_PROXY"] = "socks5://proxy.example"

        with crewai_runtime.suppress_proxy_for_codex_oauth(model="openai-codex/gpt-5.4", auth_mode="oauth_codex"):
            self.assertNotIn("HTTP_PROXY", os.environ)
            self.assertNotIn("HTTPS_PROXY", os.environ)
            self.assertNotIn("ALL_PROXY", os.environ)

        self.assertEqual(os.environ["HTTP_PROXY"], "http://proxy.example")
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://proxy.example")
        self.assertEqual(os.environ["ALL_PROXY"], "socks5://proxy.example")

    def test_suppress_proxy_for_codex_oauth_respects_disable_switch(self):
        os.environ["TEAMOS_CREWAI_DISABLE_PROXY_FOR_OAUTH_CODEX"] = "0"
        os.environ["HTTP_PROXY"] = "http://proxy.example"

        with crewai_runtime.suppress_proxy_for_codex_oauth(model="openai-codex/gpt-5.4", auth_mode="oauth_codex"):
            self.assertEqual(os.environ["HTTP_PROXY"], "http://proxy.example")


if __name__ == "__main__":
    unittest.main()
