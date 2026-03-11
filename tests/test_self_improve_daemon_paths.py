import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


class SelfImproveDaemonPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parents[1]
        p = str(repo_root / "scripts" / "pipelines")
        if p not in sys.path:
            sys.path.insert(0, p)
        mod_path = repo_root / "scripts" / "pipelines" / "self_improve_daemon.py"
        loader = importlib.machinery.SourceFileLoader("self_improve_daemon_test_module", str(mod_path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = mod
        loader.exec_module(mod)
        cls.mod = mod

    def test_paths_use_runtime_root_not_repo_team_os(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "team-os"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = (Path(td) / "team-os-runtime").resolve()
            old = os.environ.get("TEAMOS_RUNTIME_ROOT")
            os.environ["TEAMOS_RUNTIME_ROOT"] = str(runtime)
            try:
                pid = self.mod._pid_path(repo)
                state = self.mod._state_path(repo)
                log = self.mod._log_path(repo)
            finally:
                if old is None:
                    os.environ.pop("TEAMOS_RUNTIME_ROOT", None)
                else:
                    os.environ["TEAMOS_RUNTIME_ROOT"] = old

            self.assertEqual(pid, runtime / "state" / "self_improve_daemon.pid")
            self.assertEqual(state, runtime / "state" / "self_improve_state.json")
            self.assertEqual(log, runtime / "state" / "logs" / "self_improve_daemon.log")


if __name__ == "__main__":
    unittest.main()
