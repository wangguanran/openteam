import os
import sys
import tempfile
import unittest
from pathlib import Path


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app.runtime_db import RuntimeDB  # noqa: E402
from app.runtime_db import SQLiteRuntimeDB  # noqa: E402


class RuntimeDBTaskLeaseTests(unittest.TestCase):
    def test_runtime_db_ignores_legacy_postgres_env_and_uses_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "runtime.db"
            old = os.environ.get("OPENTEAM_DB_URL")
            os.environ["OPENTEAM_DB_URL"] = "postgresql://obsolete-user:obsolete-pass@127.0.0.1:15432/openteam"
            try:
                db = RuntimeDB(str(db_path))
            finally:
                if old is None:
                    os.environ.pop("OPENTEAM_DB_URL", None)
                else:
                    os.environ["OPENTEAM_DB_URL"] = old

            self.assertIsInstance(db._impl, SQLiteRuntimeDB)

    def test_claim_renew_release_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "runtime.db"
            db = RuntimeDB(str(db_path))

            claimed = db.claim_task_lease(
                lease_scope="repo_improvement_delivery",
                lease_key="repo_improvement_delivery:openteam:TASK-1",
                project_id="openteam",
                task_id="TASK-1",
                holder_instance_id="node-a",
                holder_actor="worker-a",
                lease_ttl_sec=120,
                holder_meta={"attempt": 1},
            )
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.holder_instance_id, "node-a")
            self.assertEqual(claimed.holder_meta.get("attempt"), 1)

            denied = db.claim_task_lease(
                lease_scope="repo_improvement_delivery",
                lease_key="repo_improvement_delivery:openteam:TASK-1",
                project_id="openteam",
                task_id="TASK-1",
                holder_instance_id="node-b",
                holder_actor="worker-b",
                lease_ttl_sec=120,
                holder_meta={},
            )
            self.assertIsNone(denied)

            renewed = db.renew_task_lease(
                lease_key="repo_improvement_delivery:openteam:TASK-1",
                holder_instance_id="node-a",
                lease_ttl_sec=180,
            )
            self.assertIsNotNone(renewed)
            self.assertEqual(renewed.lease_ttl_sec, 180)
            self.assertGreaterEqual(renewed.lease_version, 2)

            released = db.release_task_lease(
                lease_key="repo_improvement_delivery:openteam:TASK-1",
                holder_instance_id="node-a",
            )
            self.assertTrue(released)
            self.assertIsNone(db.get_task_lease(lease_key="repo_improvement_delivery:openteam:TASK-1"))

            takeover = db.claim_task_lease(
                lease_scope="repo_improvement_delivery",
                lease_key="repo_improvement_delivery:openteam:TASK-1",
                project_id="openteam",
                task_id="TASK-1",
                holder_instance_id="node-b",
                holder_actor="worker-b",
                lease_ttl_sec=60,
                holder_meta={"attempt": 2},
            )
            self.assertIsNotNone(takeover)
            self.assertEqual(takeover.holder_instance_id, "node-b")


if __name__ == "__main__":
    unittest.main()
