import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _add_pipelines_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = os.path.join(repo_root, "scripts", "pipelines")
    if p not in sys.path:
        sys.path.insert(0, p)


_add_pipelines_to_syspath()

from installer_failure_classifier import classify_failure  # noqa: E402


class InstallerFailureClassifierTests(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _run(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        cmd = ["python3", str(self._repo_root() / "scripts" / "pipelines" / "installer_failure_classifier.py")] + args
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, check=False)

    def test_classify_auth_error_deterministic(self):
        out = classify_failure(
            component="node_add.bootstrap",
            stage="bootstrap_remote_node",
            stdout="",
            stderr="Permission denied (publickey,password).",
            ok=False,
        )
        self.assertEqual(out.get("category"), "SSH_AUTH_FAILED")
        self.assertFalse(bool(out.get("retryable")))

    def test_classify_brain_config_missing_uses_single_node_remediation(self):
        out = classify_failure(
            component="bootstrap",
            stage="startup",
            stdout="",
            stderr="missing runtime db config",
            ok=False,
        )

        self.assertEqual(out.get("category"), "BRAIN_CONFIG_MISSING")
        self.assertNotIn("openteam hub init", str(out.get("remediation") or ""))

    def test_record_fallback_to_runtime_audit_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._repo_root()
            runtime_root = Path(td) / "runtime"
            env = dict(os.environ)
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)
            env.pop("OPENTEAM_DB_URL", None)

            payload = {
                "component": "node_add.push_hub_config",
                "stage": "scp_env",
                "stdout": "",
                "stderr": "ssh: connect to host 10.0.0.8 port 22: Connection timed out",
                "target_host": "10.0.0.8",
                "ok": False,
            }
            p = self._run(
                [
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(Path(td) / "ws"),
                    "--input-json",
                    json.dumps(payload, ensure_ascii=False),
                    "record",
                ],
                env,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            out = json.loads(p.stdout)
            self.assertFalse(bool((out.get("db") or {}).get("enabled")))
            fallback_path = Path(str(out.get("fallback_path") or "")).resolve()
            self.assertTrue(fallback_path.exists())
            self.assertEqual(fallback_path, (runtime_root / "state" / "audit" / "installer_runs.jsonl").resolve())

            lines = fallback_path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines)
            event = json.loads(lines[-1])
            self.assertEqual(event.get("event"), "INSTALLER_RUN")
            run = event.get("run") or {}
            self.assertEqual(run.get("target_host"), "10.0.0.8")
            self.assertEqual(run.get("category"), "NETWORK_UNREACHABLE")
            cls = event.get("classification") or {}
            self.assertTrue(bool(cls.get("retryable")))

    def test_record_updates_single_node_knowledge_store_and_get_uses_same_runtime_root(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._repo_root()
            runtime_root = Path(td) / "runtime"
            env = dict(os.environ)
            env["OPENTEAM_RUNTIME_ROOT"] = str(runtime_root)
            env.pop("OPENTEAM_DB_URL", None)

            payload = {
                "component": "node_add.push_hub_config",
                "stage": "scp_env",
                "stdout": "",
                "stderr": "ssh: connect to host 10.0.0.8 port 22: Connection timed out",
                "target_host": "10.0.0.8",
                "ok": False,
            }
            recorded = self._run(
                [
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(Path(td) / "ws"),
                    "--input-json",
                    json.dumps(payload, ensure_ascii=False),
                    "record",
                ],
                env,
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)

            knowledge = subprocess.run(
                [
                    "python3",
                    str(repo / "scripts" / "pipelines" / "installer_knowledge.py"),
                    "--repo-root",
                    str(repo),
                    "--workspace-root",
                    str(Path(td) / "ws"),
                    "--json",
                    "get",
                    "installer:node_add.push_hub_config:network_unreachable",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(knowledge.returncode, 0, knowledge.stderr)
            out = json.loads(knowledge.stdout)
            self.assertEqual(out.get("backend"), "fallback")
            expected_path = (runtime_root / "state" / "audit" / "installer_knowledge.json").resolve()
            self.assertEqual(Path(str(out.get("path") or "")).resolve(), expected_path)
            item = out.get("item") or {}
            value = item.get("value") or {}
            self.assertEqual(item.get("key"), "installer:node_add.push_hub_config:network_unreachable")
            self.assertEqual(value.get("category"), "NETWORK_UNREACHABLE")
            self.assertEqual(value.get("last_stage"), "scp_env")
            self.assertEqual(value.get("last_target_host"), "10.0.0.8")


if __name__ == "__main__":
    unittest.main()
