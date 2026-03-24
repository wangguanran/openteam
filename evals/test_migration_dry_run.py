import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class MigrationDryRunEvals(unittest.TestCase):
    def test_migrate_from_repo_dry_run_plans_but_does_not_move(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "governance" / "migrate_repo_projects.py"
        self.assertTrue(script.exists(), f"missing migration script: {script}")

        with tempfile.TemporaryDirectory() as td_repo, tempfile.TemporaryDirectory() as td_ws:
            fake_repo = Path(td_repo).resolve()
            fake_ws = Path(td_ws).resolve()

            # Create a minimal legacy in-repo layout that must be migrated.
            (fake_repo / "docs" / "requirements" / "demo").mkdir(parents=True, exist_ok=True)
            (fake_repo / "docs" / "requirements" / "demo" / "requirements.yaml").write_text(
                "schema_version: 1\nproject_id: demo\nnext_req_seq: 1\nrequirements: []\n", encoding="utf-8"
            )
            (fake_repo / "docs" / "plans" / "demo").mkdir(parents=True, exist_ok=True)
            (fake_repo / "docs" / "plans" / "demo" / "plan.yaml").write_text("schema_version: 1\nmilestones: []\n", encoding="utf-8")
            (fake_repo / ".openteam" / "ledger" / "conversations" / "demo").mkdir(parents=True, exist_ok=True)
            (fake_repo / ".openteam" / "ledger" / "conversations" / "demo" / "2026-01-01.jsonl").write_text("{\"msg\":\"hi\"}\n", encoding="utf-8")
            (fake_repo / ".openteam" / "ledger" / "tasks").mkdir(parents=True, exist_ok=True)
            (fake_repo / ".openteam" / "logs" / "tasks" / "DEMO-0001").mkdir(parents=True, exist_ok=True)
            (fake_repo / ".openteam" / "logs" / "tasks" / "DEMO-0001" / "00_intake.md").write_text("# intake\n", encoding="utf-8")
            (fake_repo / ".openteam" / "ledger" / "tasks" / "DEMO-0001.yaml").write_text(
                "id: DEMO-0001\nproject_id: demo\nstatus: intake\n", encoding="utf-8"
            )
            (fake_repo / "prompt-library" / "projects" / "demo").mkdir(parents=True, exist_ok=True)
            (fake_repo / "prompt-library" / "projects" / "demo" / "NEW_TASK.md").write_text("# prompt\n", encoding="utf-8")

            before = {
                "req": (fake_repo / "docs" / "requirements" / "demo" / "requirements.yaml").read_text(encoding="utf-8"),
                "plan": (fake_repo / "docs" / "plans" / "demo" / "plan.yaml").read_text(encoding="utf-8"),
                "conv": (fake_repo / ".openteam" / "ledger" / "conversations" / "demo" / "2026-01-01.jsonl").read_text(encoding="utf-8"),
                "task": (fake_repo / ".openteam" / "ledger" / "tasks" / "DEMO-0001.yaml").read_text(encoding="utf-8"),
                "log": (fake_repo / ".openteam" / "logs" / "tasks" / "DEMO-0001" / "00_intake.md").read_text(encoding="utf-8"),
                "prompt": (fake_repo / "prompt-library" / "projects" / "demo" / "NEW_TASK.md").read_text(encoding="utf-8"),
            }

            p = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--repo-root",
                    str(fake_repo),
                    "--workspace-root",
                    str(fake_ws),
                    "--dry-run",
                    "--json",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(p.returncode, 0, f"dry-run failed: {p.stderr[:200]}")
            data = json.loads((p.stdout or "").strip())
            self.assertGreater(int(data.get("planned_count") or 0), 0)
            self.assertTrue(data.get("dry_run", True))

            # Dry-run must not move or mutate any source files.
            after = {
                "req": (fake_repo / "docs" / "requirements" / "demo" / "requirements.yaml").read_text(encoding="utf-8"),
                "plan": (fake_repo / "docs" / "plans" / "demo" / "plan.yaml").read_text(encoding="utf-8"),
                "conv": (fake_repo / ".openteam" / "ledger" / "conversations" / "demo" / "2026-01-01.jsonl").read_text(encoding="utf-8"),
                "task": (fake_repo / ".openteam" / "ledger" / "tasks" / "DEMO-0001.yaml").read_text(encoding="utf-8"),
                "log": (fake_repo / ".openteam" / "logs" / "tasks" / "DEMO-0001" / "00_intake.md").read_text(encoding="utf-8"),
                "prompt": (fake_repo / "prompt-library" / "projects" / "demo" / "NEW_TASK.md").read_text(encoding="utf-8"),
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
