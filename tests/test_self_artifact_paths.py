from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_script(rel_path: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / rel_path), *args],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class SelfArtifactPathTests(unittest.TestCase):
    def test_new_task_writes_under_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime-root"
            env = os.environ.copy()
            env["HOME"] = td
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)

            out = _run_script("scripts/tasks/new.sh", "Boundary convergence task", env=env)
            self.assertEqual(out.returncode, 0, msg=out.stderr)

            kv = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
            ledger = Path(kv["ledger"])
            logs_dir = Path(kv["logs_dir"])

            self.assertTrue(ledger.exists(), msg=out.stdout)
            self.assertTrue(logs_dir.exists(), msg=out.stdout)
            self.assertTrue(str(ledger).startswith(str(runtime_root / "state" / "ledger" / "tasks")))
            self.assertTrue(str(logs_dir).startswith(str(runtime_root / "state" / "logs" / "tasks")))
            self.assertNotIn("/.openteam/", str(ledger))

    def test_skill_boot_writes_under_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime-root"
            env = os.environ.copy()
            env["HOME"] = td
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)

            out = _run_script("scripts/skills/boot.sh", "Researcher", "Runtime Paths", env=env)
            self.assertEqual(out.returncode, 0, msg=out.stderr)

            kv = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
            src_path = Path(kv["created_source_summary"])
            skill_path = Path(kv["created_skill_card"])
            mem_index = Path(kv["updated_memory_index"])

            self.assertTrue(src_path.exists(), msg=out.stdout)
            self.assertTrue(skill_path.exists(), msg=out.stdout)
            self.assertTrue(mem_index.exists(), msg=out.stdout)
            self.assertTrue(str(src_path).startswith(str(runtime_root / "state" / "openteam" / "kb" / "sources")))
            self.assertTrue(str(skill_path).startswith(str(runtime_root / "state" / "openteam" / "kb" / "roles")))
            self.assertTrue(str(mem_index).startswith(str(runtime_root / "state" / "openteam" / "memory" / "roles")))
            self.assertNotIn("/.openteam/", str(skill_path))


if __name__ == "__main__":
    unittest.main()
