import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


class InstallerKnowledgeSingleNodeTests(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _run(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        cmd = ["python3", str(self._repo_root() / "scripts" / "pipelines" / "installer_knowledge.py")] + args
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, check=False)

    def test_parallel_upserts_preserve_all_keys(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime"
            workspace_root = Path(td) / "workspace"
            env = dict(os.environ)
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)

            def _upsert(i: int) -> None:
                proc = self._run(
                    [
                        "--repo-root",
                        str(self._repo_root()),
                        "--workspace-root",
                        str(workspace_root),
                        "--runtime-root",
                        str(runtime_root),
                        "--json",
                        "upsert",
                        f"installer:test:{i}",
                        "--value-json",
                        json.dumps({"index": i}, ensure_ascii=False),
                    ],
                    env,
                )
                if proc.returncode != 0:
                    raise AssertionError(proc.stderr)

            with ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(_upsert, range(30)))

            stored = json.loads((runtime_root / "state" / "audit" / "installer_knowledge.json").read_text(encoding="utf-8"))
            self.assertEqual(len(stored), 30)
            for i in range(30):
                self.assertEqual((stored.get(f"installer:test:{i}") or {}).get("index"), i)


if __name__ == "__main__":
    unittest.main()
