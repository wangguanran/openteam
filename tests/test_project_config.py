import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestProjectConfig(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

    def test_init_set_validate(self) -> None:
        repo = self._repo_root()
        script = repo / ".team-os" / "scripts" / "pipelines" / "project_config.py"
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir(parents=True, exist_ok=True)

            # init
            p1 = self._run(
                [
                    "python3",
                    str(script),
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(ws),
                    "--project",
                    "demo",
                    "init",
                ]
            )
            self.assertEqual(p1.returncode, 0, msg=p1.stderr)
            out1 = json.loads(p1.stdout)
            self.assertTrue(out1.get("changed"))

            cfg_path = ws / "projects" / "demo" / "state" / "config" / "project.yaml"
            self.assertTrue(cfg_path.exists())

            # init again is idempotent
            p2 = self._run(
                [
                    "python3",
                    str(script),
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(ws),
                    "--project",
                    "demo",
                    "init",
                ]
            )
            self.assertEqual(p2.returncode, 0, msg=p2.stderr)
            out2 = json.loads(p2.stdout)
            self.assertFalse(out2.get("changed"))

            # set + validate
            p3 = self._run(
                [
                    "python3",
                    str(script),
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(ws),
                    "--project",
                    "demo",
                    "set",
                    "--key",
                    "panel.enabled",
                    "--value",
                    "false",
                ]
            )
            self.assertEqual(p3.returncode, 0, msg=p3.stderr)
            out3 = json.loads(p3.stdout)
            self.assertTrue(out3.get("changed"))

            pv = self._run(
                [
                    "python3",
                    str(script),
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(ws),
                    "--project",
                    "demo",
                    "validate",
                ]
            )
            self.assertEqual(pv.returncode, 0, msg=pv.stderr)
            outv = json.loads(pv.stdout)
            self.assertTrue(outv.get("ok"))

    def test_set_rejects_unknown_keys(self) -> None:
        repo = self._repo_root()
        script = repo / ".team-os" / "scripts" / "pipelines" / "project_config.py"

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir(parents=True, exist_ok=True)

            self._run(
                [
                    "python3",
                    str(script),
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(ws),
                    "--project",
                    "demo",
                    "init",
                ]
            )

            # Unknown key should fail schema validation.
            p = self._run(
                [
                    "python3",
                    str(script),
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(ws),
                    "--project",
                    "demo",
                    "set",
                    "--key",
                    "unknown.key",
                    "--value",
                    "1",
                ]
            )
            self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()

