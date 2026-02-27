import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestProjectAgentsInject(unittest.TestCase):
    def _repo_root(self) -> Path:
        # tests/ is under the team-os repo root
        return Path(__file__).resolve().parents[1]

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

    def test_inject_creates_and_is_idempotent(self) -> None:
        repo = self._repo_root()
        script = repo / "scripts" / "pipelines" / "project_agents_inject.py"
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            proj_repo = ws / "projects" / "demo" / "repo"
            proj_repo.mkdir(parents=True, exist_ok=True)

            # 1) Create when missing
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
                    "--repo-path",
                    str(proj_repo),
                    "--manual-version",
                    "v1",
                    "--no-leader-only",
                ]
            )
            self.assertEqual(p1.returncode, 0, msg=p1.stderr)
            out1 = json.loads(p1.stdout)
            self.assertTrue(out1.get("changed"))
            self.assertTrue(out1.get("wrote"))
            agents = (proj_repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("<!-- TEAMOS_MANUAL_START -->", agents)
            self.assertIn("<!-- TEAMOS_MANUAL_END -->", agents)

            # 2) Idempotent re-run: no rewrite and no content change.
            before = agents
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
                    "--repo-path",
                    str(proj_repo),
                    "--manual-version",
                    "v1",
                    "--no-leader-only",
                ]
            )
            self.assertEqual(p2.returncode, 0, msg=p2.stderr)
            out2 = json.loads(p2.stdout)
            self.assertFalse(out2.get("changed"))
            self.assertFalse(out2.get("wrote"))
            after = (proj_repo / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_inject_preserves_existing_content_and_replaces_block(self) -> None:
        repo = self._repo_root()
        script = repo / "scripts" / "pipelines" / "project_agents_inject.py"

        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            proj_repo = ws / "projects" / "demo" / "repo"
            proj_repo.mkdir(parents=True, exist_ok=True)

            agents_path = proj_repo / "AGENTS.md"
            agents_path.write_text(
                "\n".join(
                    [
                        "# Project AGENTS",
                        "",
                        "Custom intro.",
                        "",
                        "<!-- TEAMOS_MANUAL_START -->",
                        "OLD CONTENT",
                        "<!-- TEAMOS_MANUAL_END -->",
                        "",
                        "Custom tail.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

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
                    "--repo-path",
                    str(proj_repo),
                    "--manual-version",
                    "v1",
                    "--no-leader-only",
                ]
            )
            self.assertEqual(p.returncode, 0, msg=p.stderr)
            out = json.loads(p.stdout)
            self.assertTrue(out.get("changed"))
            self.assertTrue(out.get("wrote"))

            new_agents = agents_path.read_text(encoding="utf-8")
            self.assertIn("Custom intro.", new_agents)
            self.assertIn("Custom tail.", new_agents)
            self.assertNotIn("OLD CONTENT", new_agents)
            self.assertIn("manual_version: v1", new_agents)


if __name__ == "__main__":
    unittest.main()

