import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import yaml


class ApprovalsSingleNodeTests(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _run(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        cmd = ["python3", str(self._repo_root() / "scripts" / "pipelines" / "approvals.py")] + args
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, check=False)

    def _make_repo_root(self, root: Path, *, deny_categories: list[str]) -> Path:
        (root / "scripts" / "pipelines").mkdir(parents=True, exist_ok=True)
        (root / "schemas").mkdir(parents=True, exist_ok=True)
        (root / "specs" / "policies").mkdir(parents=True, exist_ok=True)
        (root / "OPENTEAM.md").write_text("single-node\n", encoding="utf-8")
        (root / "specs" / "policies" / "approvals.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "record_db_required": True,
                    "require_manual_when_single": True,
                    "auto_approve_high_risk_categories": [],
                    "always_deny_categories": deny_categories,
                },
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return root

    def test_decide_rejects_missing_approval_id(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ)
            env["OPENTEAM_WORKSPACE_ROOT"] = str(Path(td) / "workspace")
            proc = self._run(
                [
                    "--repo-root",
                    str(self._repo_root()),
                    "--workspace-root",
                    str(Path(td) / "workspace"),
                    "--json",
                    "decide",
                    "missing-approval",
                    "--decision",
                    "APPROVE",
                ],
                env,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("approval_id not found", proc.stderr)

    def test_decide_cannot_override_policy_denied_request(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "workspace"
            repo_root = self._make_repo_root(Path(td) / "repo", deny_categories=["SYSTEM_CONFIG"])
            env = dict(os.environ)
            env["OPENTEAM_WORKSPACE_ROOT"] = str(workspace)

            requested = self._run(
                [
                    "--repo-root",
                    str(repo_root),
                    "--workspace-root",
                    str(workspace),
                    "--json",
                    "request",
                    "--action-kind",
                    "systemd_service_write",
                    "--summary",
                    "write systemd unit",
                ],
                env,
            )
            self.assertEqual(requested.returncode, 2, requested.stderr)
            out = json.loads(requested.stdout)
            self.assertEqual((out.get("record") or {}).get("status"), "DENIED")
            approval_id = str(out.get("approval_id") or "")
            self.assertTrue(approval_id)

            decided = self._run(
                [
                    "--repo-root",
                    str(repo_root),
                    "--workspace-root",
                    str(workspace),
                    "--json",
                    "decide",
                    approval_id,
                    "--decision",
                    "APPROVE",
                ],
                env,
            )
            self.assertNotEqual(decided.returncode, 0)
            self.assertIn("cannot approve denied request", decided.stderr)

    def test_decide_stays_rejected_after_policy_file_relaxes(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "workspace"
            repo_root = self._make_repo_root(Path(td) / "repo", deny_categories=["SYSTEM_CONFIG"])
            env = dict(os.environ)
            env["OPENTEAM_WORKSPACE_ROOT"] = str(workspace)

            requested = self._run(
                [
                    "--repo-root",
                    str(repo_root),
                    "--workspace-root",
                    str(workspace),
                    "--json",
                    "request",
                    "--action-kind",
                    "systemd_service_write",
                    "--summary",
                    "write systemd unit",
                ],
                env,
            )
            self.assertEqual(requested.returncode, 2, requested.stderr)
            approval_id = str((json.loads(requested.stdout).get("approval_id")) or "")
            self.assertTrue(approval_id)

            self._make_repo_root(repo_root, deny_categories=[])

            decided = self._run(
                [
                    "--repo-root",
                    str(repo_root),
                    "--workspace-root",
                    str(workspace),
                    "--json",
                    "decide",
                    approval_id,
                    "--decision",
                    "APPROVE",
                ],
                env,
            )
            self.assertNotEqual(decided.returncode, 0)
            self.assertIn("cannot approve denied", decided.stderr)
