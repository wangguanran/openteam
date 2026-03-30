import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    pipelines_dir = repo_root / "scripts" / "pipelines"
    sys.path.insert(0, str(pipelines_dir))
    try:
        script = pipelines_dir / "runtime_root.py"
        loader = importlib.machinery.SourceFileLoader("runtime_root_test_module", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


class RuntimeRootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_ensure_runtime_dirs_no_longer_creates_hub_root(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime"

            out = self.mod._ensure_runtime_dirs(runtime_root)

            self.assertNotIn("hub", out)
            self.assertFalse((runtime_root / "hub").exists())
            self.assertTrue((runtime_root / "state" / "audit").exists())
            self.assertTrue((runtime_root / "workspace" / "projects").exists())


if __name__ == "__main__":
    unittest.main()
