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
            "scaffolds/hub/README.md.j2",
            "scaffolds/hub/docker-compose.yml.j2",
            "scaffolds/hub/pg_hba.conf.j2",
        ]

        for rel_path in removed_paths:
            with self.subTest(path=rel_path):
                self.assertFalse((ROOT / rel_path).exists(), rel_path)


if __name__ == "__main__":
    unittest.main()
