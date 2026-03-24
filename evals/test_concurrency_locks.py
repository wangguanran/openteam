import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPE = REPO_ROOT / "scripts" / "pipelines"


def _run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)


class ConcurrencyLocksTests(unittest.TestCase):
    def test_two_req_add_processes_do_not_corrupt_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td).resolve()
            env = os.environ.copy()
            env["OPENTEAM_WORKSPACE_ROOT"] = str(ws)
            env["OPENTEAM_REQUIREMENTS_SEMANTIC_CHECK"] = "0"
            # Force file-lock backend for deterministic local tests (avoid depending on an external DB).
            env["OPENTEAM_DB_URL"] = ""

            cmd1 = [
                sys.executable,
                str(PIPE / "requirements_raw_first.py"),
                "--repo-root",
                str(REPO_ROOT),
                "--workspace-root",
                str(ws),
                "add",
                "--scope",
                "project:demo",
                "--text",
                "并发需求 A",
                "--workstream",
                "general",
                "--priority",
                "P2",
                "--source",
                "cli",
                "--user",
                "tester1",
            ]
            cmd2 = [
                sys.executable,
                str(PIPE / "requirements_raw_first.py"),
                "--repo-root",
                str(REPO_ROOT),
                "--workspace-root",
                str(ws),
                "add",
                "--scope",
                "project:demo",
                "--text",
                "并发需求 B",
                "--workstream",
                "general",
                "--priority",
                "P2",
                "--source",
                "cli",
                "--user",
                "tester2",
            ]

            p1 = subprocess.Popen(cmd1, env=env, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            p2 = subprocess.Popen(cmd2, env=env, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out1, err1 = p1.communicate(timeout=60)
            out2, err2 = p2.communicate(timeout=60)
            self.assertEqual(p1.returncode, 0, msg=f"p1 failed rc={p1.returncode} stderr={err1[-500:]}")
            self.assertEqual(p2.returncode, 0, msg=f"p2 failed rc={p2.returncode} stderr={err2[-500:]}")

            req_dir = ws / "projects" / "demo" / "state" / "requirements"
            y = yaml.safe_load((req_dir / "requirements.yaml").read_text(encoding="utf-8")) or {}
            reqs = y.get("requirements") or []
            texts = [str(r.get("text") or "") for r in reqs]
            self.assertIn("并发需求 A", "\n".join(texts))
            self.assertIn("并发需求 B", "\n".join(texts))

            raw = req_dir / "raw_inputs.jsonl"
            raw_lines = [ln for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(raw_lines), 2)

    def test_prompt_compile_waits_for_scope_lock(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td).resolve()
            env = os.environ.copy()
            env["OPENTEAM_WORKSPACE_ROOT"] = str(ws)
            env["OPENTEAM_REQUIREMENTS_SEMANTIC_CHECK"] = "0"
            env["OPENTEAM_DB_URL"] = ""

            req_dir = ws / "projects" / "demo" / "state" / "requirements"
            req_dir.mkdir(parents=True, exist_ok=True)

            hold_cmd = [
                sys.executable,
                "-c",
                "\n".join(
                    [
                        "import os,sys,time",
                        "from pathlib import Path",
                        f"sys.path.insert(0, {repr(str(PIPE))})",
                        "import locks",
                        f"ws=Path({repr(str(ws))})",
                        f"repo=Path({repr(str(REPO_ROOT))})",
                        f"req=ws/'projects'/'demo'/'state'/'requirements'",
                        "h=locks.acquire_scope_lock('project:demo', repo_root=repo, workspace_root=ws, req_dir=req, ttl_sec=10, wait_sec=2, poll_sec=0.1)",
                        "time.sleep(2.0)",
                        "locks.release_lock(h)",
                    ]
                ),
            ]
            holder = subprocess.Popen(hold_cmd, env=env, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(0.2)  # give the holder a head start

            prompt_cmd = [
                sys.executable,
                str(PIPE / "prompt_compile.py"),
                "--repo-root",
                str(REPO_ROOT),
                "--workspace-root",
                str(ws),
                "--scope",
                "project:demo",
            ]
            t0 = time.time()
            res = _run(prompt_cmd, env=env)
            dt = time.time() - t0
            _ = holder.communicate(timeout=30)

            self.assertEqual(res.returncode, 0, msg=res.stderr[-500:])
            self.assertGreaterEqual(dt, 1.2)

    def test_stale_file_lock_is_recovered_after_ttl(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td).resolve()
            env = os.environ.copy()
            env["OPENTEAM_WORKSPACE_ROOT"] = str(ws)
            # Force file-lock backend for deterministic local tests.
            old_dsn = os.environ.get("OPENTEAM_DB_URL")
            os.environ["OPENTEAM_DB_URL"] = ""

            locks_dir = ws / "projects" / "demo" / "state" / "locks"
            locks_dir.mkdir(parents=True, exist_ok=True)
            lock_path = locks_dir / "scope_project_demo.lock"
            stale = {
                "schema_version": 1,
                "lock_key": "scope:project:demo",
                "backend": "file",
                "holder": {"pid": 99999, "hostname": "stale-host"},
                "acquired_at": "2000-01-01T00:00:00Z",
                "heartbeat_at": "2000-01-01T00:00:00Z",
                "expires_at": "2000-01-01T00:00:01Z",
            }
            lock_path.write_text(
                __import__("json").dumps(stale, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            try:
                sys.path.insert(0, str(PIPE))
                import locks  # noqa: E402

                h = locks.acquire_scope_lock("project:demo", repo_root=REPO_ROOT, workspace_root=ws, req_dir=ws / "projects" / "demo" / "state" / "requirements", ttl_sec=5, wait_sec=1, poll_sec=0.1)
                try:
                    self.assertTrue(lock_path.exists())
                    stale_files = list(locks_dir.glob("scope_project_demo.lock.stale*"))
                    self.assertTrue(stale_files)
                finally:
                    locks.release_lock(h)
            finally:
                if old_dsn is None:
                    os.environ.pop("OPENTEAM_DB_URL", None)
                else:
                    os.environ["OPENTEAM_DB_URL"] = old_dsn

    def test_lock_busy_returns_holder_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td).resolve()
            (ws / "projects" / "demo" / "state" / "requirements").mkdir(parents=True, exist_ok=True)
            old_dsn = os.environ.get("OPENTEAM_DB_URL")
            os.environ["OPENTEAM_DB_URL"] = ""

            try:
                sys.path.insert(0, str(PIPE))
                import locks  # noqa: E402

                h1 = locks.acquire_scope_lock("project:demo", repo_root=REPO_ROOT, workspace_root=ws, req_dir=ws / "projects" / "demo" / "state" / "requirements", ttl_sec=10, wait_sec=1, poll_sec=0.1)
                try:
                    with self.assertRaises(locks.LockBusy) as ctx:
                        _ = locks.acquire_scope_lock("project:demo", repo_root=REPO_ROOT, workspace_root=ws, req_dir=ws / "projects" / "demo" / "state" / "requirements", ttl_sec=10, wait_sec=0.2, poll_sec=0.05)
                    holder = ctx.exception.holder or {}
                    self.assertTrue(holder.get("pid"))
                    self.assertTrue(holder.get("hostname"))
                finally:
                    locks.release_lock(h1)
            finally:
                if old_dsn is None:
                    os.environ.pop("OPENTEAM_DB_URL", None)
                else:
                    os.environ["OPENTEAM_DB_URL"] = old_dsn


if __name__ == "__main__":
    unittest.main()
