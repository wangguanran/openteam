import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class HubPipelinesTests(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _env(self, td: str) -> tuple[dict[str, str], Path]:
        env = dict(os.environ)
        env["HOME"] = td
        runtime_root = Path(td) / "team-os-runtime"
        env["TEAMOS_RUNTIME_ROOT"] = str(runtime_root)
        return env, runtime_root

    def _run(self, script: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        cmd = ["python3", str(self._repo_root() / "scripts" / "pipelines" / script)] + args
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, check=False)

    def test_hub_init_uses_runtime_root_and_enables_redis(self):
        with tempfile.TemporaryDirectory() as td:
            env, runtime_root = self._env(td)
            repo = str(self._repo_root())
            p = self._run("hub_init.py", ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws")], env)
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertTrue(out["redis"]["enabled"])
            self.assertEqual(out["hub_root"], str((runtime_root / "hub").resolve()))

            env_path = runtime_root / "hub" / "env" / ".env"
            self.assertTrue(env_path.exists())
            txt = env_path.read_text(encoding="utf-8")
            self.assertIn("HUB_REDIS_ENABLED=1", txt)
            self.assertIn("REDIS_BIND_IP=127.0.0.1", txt)
            compose = (runtime_root / "hub" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
            self.assertIn("postgres:", compose)
            self.assertIn("redis:", compose)

    def test_hub_expose_rejects_public_bind_ip(self):
        with tempfile.TemporaryDirectory() as td:
            env, _runtime_root = self._env(td)
            repo = str(self._repo_root())
            p = self._run(
                "hub_expose.py",
                ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws"), "--bind-ip", "0.0.0.0", "--allow-cidrs", "10.0.0.0/24"],
                env,
            )
            self.assertNotEqual(p.returncode, 0)

    def test_hub_expose_dry_run_works(self):
        with tempfile.TemporaryDirectory() as td:
            env, _runtime_root = self._env(td)
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

    def test_hub_status_fails_if_redis_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            env, runtime_root = self._env(td)
            repo = str(self._repo_root())
            p0 = self._run("hub_init.py", ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws")], env)
            self.assertEqual(p0.returncode, 0, p0.stderr)

            env_path = runtime_root / "hub" / "env" / ".env"
            txt = env_path.read_text(encoding="utf-8")
            env_path.write_text(txt.replace("HUB_REDIS_ENABLED=1", "HUB_REDIS_ENABLED=0"), encoding="utf-8")

            p = self._run("hub_status.py", ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws")], env)
            self.assertNotEqual(p.returncode, 0)
            out = json.loads(p.stdout)
            self.assertIn("redis is mandatory", out.get("error", ""))

    def test_hub_export_config_fails_if_redis_config_missing(self):
        with tempfile.TemporaryDirectory() as td:
            env, runtime_root = self._env(td)
            repo = str(self._repo_root())
            p0 = self._run("hub_init.py", ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws")], env)
            self.assertEqual(p0.returncode, 0, p0.stderr)

            env_path = runtime_root / "hub" / "env" / ".env"
            lines = [ln for ln in env_path.read_text(encoding="utf-8").splitlines() if not ln.startswith("REDIS_PASSWORD=")]
            env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

            p = self._run("hub_export_config.py", ["--repo-root", repo, "--workspace-root", str(Path(td) / "ws")], env)
            self.assertNotEqual(p.returncode, 0)
            out = json.loads(p.stdout)
            self.assertIn("missing required redis config", out.get("error", ""))


if __name__ == "__main__":
    unittest.main()
