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

    def test_retro_reads_and_writes_under_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime-root"
            task_id = "task-20260327-retro"
            logs_dir = runtime_root / "state" / "logs" / "tasks" / task_id
            logs_dir.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["HOME"] = td
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)

            out = _run_script("scripts/tasks/retro.sh", task_id, env=env)
            self.assertEqual(out.returncode, 0, msg=out.stderr)

            kv = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
            retro_log = Path(kv["retro_log"])
            self.assertTrue(retro_log.exists(), msg=out.stdout)
            self.assertEqual(retro_log, logs_dir / "07_retro.md")
            self.assertTrue(str(retro_log).startswith(str(runtime_root / "state" / "logs" / "tasks")))
            self.assertNotIn("/.openteam/", str(retro_log))

    def test_issue_pending_draft_writes_under_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime-root"
            fake_bin = Path(td) / "bin"
            fake_bin.mkdir(parents=True, exist_ok=True)
            fake_gh = fake_bin / "gh"
            fake_gh.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            fake_gh.chmod(0o755)

            entry_path = Path(td) / "entry.md"
            entry_path.write_text("# Runtime-state pending issue\n\nBody\n", encoding="utf-8")

            env = os.environ.copy()
            env["HOME"] = td
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

            out = _run_script("scripts/issues/open.sh", str(entry_path), env=env)
            self.assertEqual(out.returncode, 0, msg=out.stderr)

            kv = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
            pending_draft = Path(kv["pending_issue_draft"])
            self.assertTrue(pending_draft.exists(), msg=out.stdout)
            self.assertTrue(str(pending_draft).startswith(str(runtime_root / "state" / "ledger" / "openteam_issues_pending")))
            self.assertNotIn("/.openteam/", str(pending_draft))


if __name__ == "__main__":
    unittest.main()
