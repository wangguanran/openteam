import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SingleNodeCleanupTests(unittest.TestCase):
    def test_removed_hub_cluster_scripts_and_templates_are_absent(self):
        removed_paths = [
            "scripts/pipelines/hub_backup.py",
            "scripts/pipelines/hub_common.py",
            "scripts/pipelines/hub_down.py",
            "scripts/pipelines/hub_export_config.py",
            "scripts/pipelines/hub_expose.py",
            "scripts/pipelines/hub_init.py",
            "scripts/pipelines/hub_logs.py",
            "scripts/pipelines/hub_migrate.py",
            "scripts/pipelines/hub_push_config.py",
            "scripts/pipelines/hub_restore.py",
            "scripts/pipelines/hub_status.py",
            "scripts/pipelines/hub_up.py",
            "scripts/pipelines/cluster_election.py",
            "scripts/pipelines/remote_node_bootstrap.py",
            "scripts/cluster/bootstrap_remote_node.sh",
            "scripts/cluster/join_node.sh",
            "scripts/cluster/print_join_oneliner.sh",
            "scripts/doctor.sh",
            "scripts/runtime/doctor.sh",
            "scripts/runtime/init.sh",
            "scripts/runtime/up_image.sh",
            "scripts/runtime_init.sh",
            "scripts/runtime_up_image.sh",
            "scripts/pipelines/_db.py",
            "scaffolds/hub/README.md.j2",
            "scaffolds/hub/docker-compose.yml.j2",
            "scaffolds/hub/pg_hba.conf.j2",
            "scaffolds/runtime/.env.example",
            "scaffolds/runtime/Makefile",
            "scaffolds/runtime/docker-compose.override.yaml",
            "scaffolds/runtime/docker-compose.yml",
            "tooling/cluster/config.yaml",
            "tooling/docker/Dockerfile",
            "tooling/migrations/0001_init.sql",
            "tooling/migrations/0002_hub_locks_approvals_execution.sql",
            "tooling/migrations/0003_task_leases.sql",
        ]

        for rel_path in removed_paths:
            with self.subTest(path=rel_path):
                self.assertFalse((ROOT / rel_path).exists(), rel_path)

    def test_locks_runtime_db_and_feasibility_drop_multi_node_semantics(self):
        locks_text = (ROOT / "scripts" / "pipelines" / "locks.py").read_text(encoding="utf-8")
        runtime_db_text = (ROOT / "scaffolds" / "runtime" / "orchestrator" / "app" / "runtime_db.py").read_text(
            encoding="utf-8"
        )
        feasibility_text = (
            ROOT / "scaffolds" / "runtime" / "orchestrator" / "app" / "feasibility.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("OPENTEAM_DB_URL", locks_text)
        self.assertNotIn("db_advisory", locks_text)
        self.assertNotIn("prefer_db", locks_text)
        self.assertNotIn("Postgres advisory lock", locks_text)

        self.assertNotIn("class PostgresRuntimeDB", runtime_db_text)
        self.assertNotIn("class NodeRow", runtime_db_text)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS nodes", runtime_db_text)
        self.assertNotIn("OPENTEAM_DB_URL", runtime_db_text)

        self.assertNotIn("PostgreSQL (OPENTEAM_DB_URL / psycopg)", feasibility_text)
        self.assertNotIn("Redis (optional)", feasibility_text)
        self.assertNotIn("Docker runtime", feasibility_text)

    def test_workspace_store_drops_cluster_helper(self):
        text = (ROOT / "scaffolds" / "runtime" / "orchestrator" / "app" / "workspace_store.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("def cluster_dir(", text)

    def test_governance_and_proposal_runtime_drop_hub_module_language(self):
        purity = (ROOT / "scripts" / "governance" / "check_repo_purity.py").read_text(encoding="utf-8")
        proposal = (
            ROOT / "scaffolds" / "runtime" / "orchestrator" / "app" / "domains" / "team_workflow" / "proposal_runtime.py"
        ).read_text(encoding="utf-8")
        installer = (ROOT / "scripts" / "pipelines" / "installer_failure_classifier.py").read_text(encoding="utf-8")
        cli_entry = (ROOT / "openteam").read_text(encoding="utf-8")

        self.assertNotIn("IN_REPO_DYNAMIC_HUB_PATH", purity)
        self.assertNotIn("hub dynamic root must be outside repo", purity)
        self.assertNotIn('"hub": "Hub"', proposal)
        self.assertNotIn('(("postgres", "redis", "hub"), "Hub")', proposal)
        self.assertNotIn(" CLI, Hub, Release,", proposal)
        self.assertNotIn('"hub", "sqlite"', proposal)
        self.assertNotIn('"postgres", "redis", "sqlite"', proposal)
        self.assertNotIn("missing hub env", installer)
        self.assertNotIn("missing required postgres config", installer)
        self.assertNotIn("missing required redis config", installer)
        self.assertNotIn("hub/cluster/node entrypoints", cli_entry)

    def test_python_dependency_manifests_drop_postgres_and_redis(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        runtime_requirements = (
            ROOT / "scaffolds" / "runtime" / "orchestrator" / "requirements.txt"
        ).read_text(encoding="utf-8")
        bootstrap = (ROOT / "scripts" / "bootstrap_and_run.py").read_text(encoding="utf-8")

        self.assertNotIn('    "psycopg[binary]>=3.2.0",', pyproject)
        self.assertNotIn('    "redis",', pyproject)
        self.assertNotIn("psycopg[binary]>=3.2.0", runtime_requirements)
        self.assertNotIn("redis", runtime_requirements)
        self.assertNotIn("require_redis", bootstrap)
        self.assertNotIn("require_psycopg", bootstrap)
        self.assertNotIn("sqlite/postgres", (ROOT / "integrations" / "github_projects" / "mapping.yaml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
