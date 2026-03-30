import importlib.machinery
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "policy_check.py"
    loader = importlib.machinery.SourceFileLoader("policy_check_single_node_test_module", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


class PolicyCheckSingleNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_run_checks_uses_current_governance_and_runbook_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / ".gitignore").write_text(".env\n.codex/\nauth.json\n*_token*\n*credentials*\n.secrets/\nsshpass*\n", encoding="utf-8")
            (repo_root / "README.md").write_text("single-node local system\ndelivery-studio\nruntime.db\n", encoding="utf-8")
            (repo_root / "OPENTEAM.md").write_text("single-node local system\ndelivery-studio\nruntime.db\n", encoding="utf-8")
            (repo_root / "AGENTS.md").write_text(
                "./openteam task new --scope openteam\n./openteam task close TASK-1\n",
                encoding="utf-8",
            )
            (repo_root / "docs" / "product").mkdir(parents=True, exist_ok=True)
            (repo_root / "docs" / "runbooks").mkdir(parents=True, exist_ok=True)
            (repo_root / "docs" / "product" / "GOVERNANCE.md").write_text(
                "./openteam task close TASK-1\n",
                encoding="utf-8",
            )
            (repo_root / "docs" / "runbooks" / "EXECUTION_RUNBOOK.md").write_text(
                "./openteam task new --scope openteam\n./openteam task close TASK-1\n",
                encoding="utf-8",
            )
            (repo_root / "scaffolds" / "runtime").mkdir(parents=True, exist_ok=True)
            (repo_root / "scaffolds" / "runtime" / "README.md").write_text(
                "单节点本地系统\n~/.openteam/workspace\n127.0.0.1:8787\n",
                encoding="utf-8",
            )

            with mock.patch.object(self.mod, "_git_ls_files", return_value=[]), mock.patch.object(
                self.mod, "_run_repo_purity", return_value={"ok": True, "violations": []}
            ):
                result = self.mod.run_checks(repo_root=repo_root)

            self.assertTrue(result.ok)
            self.assertEqual(result.failures, [])

    def test_run_checks_no_longer_warns_about_missing_docker_compose_template(self):
        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td)
            (repo_root / ".gitignore").write_text(".env\n.codex/\nauth.json\n*_token*\n*credentials*\n.secrets/\nsshpass*\n", encoding="utf-8")
            (repo_root / "README.md").write_text("single-node local system\ndelivery-studio\nruntime.db\n", encoding="utf-8")
            (repo_root / "OPENTEAM.md").write_text("single-node local system\ndelivery-studio\nruntime.db\n", encoding="utf-8")
            (repo_root / "AGENTS.md").write_text(
                "./openteam task new --scope openteam\n./openteam task close TASK-1\n",
                encoding="utf-8",
            )
            (repo_root / "docs" / "product").mkdir(parents=True, exist_ok=True)
            (repo_root / "docs" / "runbooks").mkdir(parents=True, exist_ok=True)
            (repo_root / "docs" / "product" / "GOVERNANCE.md").write_text("./openteam task close\n", encoding="utf-8")
            (repo_root / "docs" / "runbooks" / "EXECUTION_RUNBOOK.md").write_text(
                "./openteam task new\n./openteam task close\n",
                encoding="utf-8",
            )
            (repo_root / "scaffolds" / "runtime").mkdir(parents=True, exist_ok=True)
            (repo_root / "scaffolds" / "runtime" / "README.md").write_text(
                "单节点本地系统\n~/.openteam/workspace\n127.0.0.1:8787\n",
                encoding="utf-8",
            )

            with mock.patch.object(self.mod, "_git_ls_files", return_value=[]), mock.patch.object(
                self.mod, "_run_repo_purity", return_value={"ok": True, "violations": []}
            ):
                result = self.mod.run_checks(repo_root=repo_root)

            self.assertNotIn(
                "runtime template docker-compose.yml missing (cannot verify workspace mount policy)",
                result.warnings,
            )


if __name__ == "__main__":
    unittest.main()
