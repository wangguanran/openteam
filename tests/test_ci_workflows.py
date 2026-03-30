from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_repo_understanding_gate():
    pipelines_dir = ROOT / "scripts" / "pipelines"
    sys.path.insert(0, str(pipelines_dir))
    try:
        script = pipelines_dir / "repo_understanding_gate.py"
        loader = importlib.machinery.SourceFileLoader("repo_understanding_gate_test_module", str(script))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


class CiWorkflowTests(unittest.TestCase):
    def test_main_ci_does_not_swallow_failures(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("|| true", text)

    def test_ci_workflow_runs_delivery_coverage_gate(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("check_delivery_coverage.py", workflow)
        self.assertIn("coverage run -m pytest", workflow)
        self.assertIn("tests/test_cockpit_state.py", workflow)
        self.assertIn("tests/test_openteam_repl.py", workflow)

    def test_runtime_ci_runs_delivery_studio_suite(self) -> None:
        text = (ROOT / ".github" / "workflows" / "runtime-ci.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_runtime_auto_update", text)
        self.assertIn("tests.test_improvement_store", text)
        self.assertIn("tests.test_openclaw_reporter", text)
        self.assertIn("tests.test_delivery_studio_runtime", text)
        self.assertIn("tests.test_delivery_studio_panel_projection", text)
        self.assertIn("tests.test_delivery_studio_review_gate", text)
        self.assertIn("tests.test_cockpit_state", text)
        self.assertIn("tests.test_openteam_repl", text)
        self.assertNotIn("tests.test_crewai_self_upgrade", text)

    def test_runtime_requirements_keep_coverage_for_task_5_contract(self) -> None:
        text = (ROOT / "scaffolds" / "runtime" / "orchestrator" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("coverage>=7.6", text)

    def test_runtime_dockerfile_is_removed_for_single_node_cutover(self) -> None:
        self.assertFalse((ROOT / "tooling" / "docker" / "Dockerfile").exists())

    def test_repo_understanding_gate_uses_runtime_state_for_openteam_task_artifacts(self) -> None:
        mod = _load_repo_understanding_gate()
        overview = mod._arch_overview()

        self.assertIn("~/.openteam/runtime/default/state/ledger", overview)
        self.assertIn("~/.openteam/runtime/default/state/logs", overview)
        self.assertNotIn("`.openteam/ledger`", overview)
        self.assertNotIn("`.openteam/logs`", overview)

    def test_repo_improvement_prompt_uses_runtime_state_paths(self) -> None:
        text = (ROOT / "specs" / "prompts" / "REPO_IMPROVEMENT.md").read_text(encoding="utf-8")

        self.assertIn("~/.openteam/runtime/default/state/logs/tasks/<TASK_ID>/07_retro.md", text)
        self.assertIn("~/.openteam/runtime/default/state/ledger/openteam_issues_pending/", text)
        self.assertNotIn("`.openteam/logs/tasks/<TASK_ID>/07_retro.md`", text)
        self.assertNotIn("`.openteam/ledger/openteam_issues_pending/`", text)

    def test_single_node_docs_make_delivery_studio_the_primary_story(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        openteam_md = (ROOT / "OPENTEAM.md").read_text(encoding="utf-8")

        self.assertIn("single-node local system", readme)
        self.assertIn("./openteam cockpit --team delivery-studio --project <project_id>", readme)
        self.assertIn("~/.openteam/runtime/default/state/runtime.db", readme)
        self.assertIn("local control plane", readme)
        self.assertIn("~/.openteam/runtime/default/state/audit/", readme)
        self.assertNotIn("state/audits/", readme)
        self.assertNotIn("openteam hub init", readme)
        self.assertNotIn("GET /v1/hub/status", readme)
        self.assertNotIn("Docker named volumes", readme)

        self.assertIn("Delivery Studio", openteam_md)
        self.assertIn("single-node", openteam_md)
        self.assertNotIn("Postgres/Hub", openteam_md)
        self.assertNotIn("Multi-node Cluster", openteam_md)
        self.assertNotIn("Docker named volumes", openteam_md)

    def test_runtime_and_execution_docs_drop_hub_cluster_runtime_story(self) -> None:
        runtime_readme = (ROOT / "scaffolds" / "runtime" / "README.md").read_text(encoding="utf-8")
        execution = (ROOT / "docs" / "runbooks" / "EXECUTION_RUNBOOK.md").read_text(encoding="utf-8")
        contract = (ROOT / "OPENTEAM.md").read_text(encoding="utf-8")

        for text in (runtime_readme, execution, contract):
            self.assertIn("~/.openteam/runtime/default/state/runtime.db", text)
            self.assertIn("delivery-studio", text)
            self.assertIn("state/audit/", text)
            self.assertNotIn("state/audits/", text)
            self.assertNotIn("/v1/hub/", text)
            self.assertNotIn("openteam hub ", text)
            self.assertNotIn("openteam node ", text)
            self.assertNotIn("docker compose", text.lower())
            self.assertNotIn("postgres", text.lower())
            self.assertNotIn("redis", text.lower())

    def test_governance_security_and_repo_understanding_drop_obsolete_operator_guidance(self) -> None:
        governance = (ROOT / "docs" / "product" / "GOVERNANCE.md").read_text(encoding="utf-8")
        security = (ROOT / "docs" / "product" / "SECURITY.md").read_text(encoding="utf-8")
        repo_understanding = (ROOT / "docs" / "product" / "openteam" / "REPO_UNDERSTANDING.md").read_text(encoding="utf-8")

        self.assertNotIn("openteam hub expose", governance)
        self.assertNotIn("openteam hub restore", governance)
        self.assertNotIn("openteam hub push-config", governance)
        self.assertNotIn("openteam node add", governance)
        self.assertIn("state/audit/", governance)
        self.assertNotIn("state/audits/", governance)

        self.assertNotIn("openteam hub expose", security)
        self.assertNotIn("Postgres + Redis enabled", security)

        self.assertIn("single-node local system", repo_understanding)
        self.assertIn("runtime/default/state/runtime.db", repo_understanding)
        self.assertNotIn("/v1/hub/status", repo_understanding)
        self.assertNotIn("/v1/cluster/status", repo_understanding)

    def test_obsolete_hub_cluster_runbooks_are_removed(self) -> None:
        self.assertFalse((ROOT / "docs" / "runbooks" / "HUB_RUNBOOK.md").exists())
        self.assertFalse((ROOT / "docs" / "runbooks" / "CLUSTER_RUNBOOK.md").exists())
        self.assertFalse((ROOT / "docs" / "runbooks" / "NODE_BOOTSTRAP.md").exists())

    def test_single_node_docs_do_not_leak_local_absolute_paths(self) -> None:
        doc_paths = [
            ROOT / "README.md",
            ROOT / "scaffolds" / "runtime" / "README.md",
            ROOT / "docs" / "runbooks" / "EXECUTION_RUNBOOK.md",
            ROOT / "docs" / "runbooks" / "DELIVERY_STUDIO.md",
            ROOT / "docs" / "product" / "GOVERNANCE.md",
            ROOT / "docs" / "product" / "SECURITY.md",
            ROOT / "docs" / "product" / "openteam" / "REPO_UNDERSTANDING.md",
        ]

        for path in doc_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/home/", text, path.as_posix())
            self.assertNotIn("/Users/", text, path.as_posix())
            self.assertNotIn(".worktrees/single-node-cutover", text, path.as_posix())


if __name__ == "__main__":
    unittest.main()
