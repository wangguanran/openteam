import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class HubPipelinesTests(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _run(self, script: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        cmd = ["python3", str(self._repo_root() / ".team-os" / "scripts" / "pipelines" / script)] + args
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, check=False)

    def test_hub_init_defaults_enable_redis_local_bind(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ)
            env["HOME"] = td
            repo = str(self._repo_root())
            p = self._run("hub_init.py", ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws")], env)
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertTrue(out["redis"]["enabled"])

            env_path = Path(td) / ".teamos" / "hub" / "env" / ".env"
            self.assertTrue(env_path.exists())
            txt = env_path.read_text(encoding="utf-8")
            self.assertIn("HUB_REDIS_ENABLED=1", txt)
            self.assertIn("REDIS_BIND_IP=127.0.0.1", txt)

    def test_hub_expose_rejects_public_bind_ip(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ)
            env["HOME"] = td
            repo = str(self._repo_root())
            p = self._run(
                "hub_expose.py",
                ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws"), "--bind-ip", "0.0.0.0", "--allow-cidrs", "10.0.0.0/24"],
                env,
            )
            self.assertNotEqual(p.returncode, 0)

    def test_hub_expose_dry_run_works(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ)
            env["HOME"] = td
            repo = str(self._repo_root())
            p0 = self._run("hub_init.py", ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws")], env)
            self.assertEqual(p0.returncode, 0, p0.stderr)
            p = self._run(
                "hub_expose.py",
                [
                    "--repo-root",
                    repo,
                    "--workspace-root",
                    str(Path(td) / "ws"),
                    "--bind-ip",
                    "10.10.0.3",
                    "--allow-cidrs",
                    "10.10.0.0/24",
                    "--dry-run",
                ],
                env,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertEqual(out["bind_ip"], "10.10.0.3")


if __name__ == "__main__":
    unittest.main()
