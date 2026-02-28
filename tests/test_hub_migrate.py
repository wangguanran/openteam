import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _add_pipelines_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = os.path.join(repo_root, "scripts", "pipelines")
    if p not in sys.path:
        sys.path.insert(0, p)


_add_pipelines_to_syspath()

import hub_migrate  # noqa: E402


class _Conn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class HubMigrateTests(unittest.TestCase):
    def test_hub_migrate_creates_missing_db_then_retries(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "migrations").mkdir(parents=True, exist_ok=True)
            (repo / "migrations" / "0001_init.sql").write_text("-- test\n", encoding="utf-8")

            env = {
                "POSTGRES_DB": "teamos",
                "POSTGRES_USER": "teamos",
                "POSTGRES_PASSWORD": "pw",
                "PG_BIND_IP": "127.0.0.1",
                "PG_PORT": "5432",
                "HUB_REDIS_ENABLED": "1",
                "REDIS_BIND_IP": "127.0.0.1",
                "REDIS_PORT": "6379",
                "REDIS_PASSWORD": "rpw",
            }
            c2 = _Conn()

            with mock.patch.object(hub_migrate, "resolve_repo_root", return_value=repo), mock.patch.object(
                hub_migrate, "hub_root", return_value=(Path(td) / "hub")
            ), mock.patch.object(hub_migrate, "load_hub_env_required", return_value=env), mock.patch.object(
                hub_migrate, "validate_hub_compose_required", return_value=None
            ), mock.patch.object(hub_migrate, "enforce_hub_env_config_security", return_value=None), mock.patch.object(
                hub_migrate, "connect", side_effect=[Exception('database "teamos" does not exist'), c2]
            ) as m_connect, mock.patch.object(hub_migrate, "_ensure_target_db_exists", return_value=None) as m_ensure, mock.patch.object(
                hub_migrate, "apply_migrations", return_value={"ok": True, "applied": ["0001"]}
            ) as m_apply:
                rc = hub_migrate.main(["--repo-root", str(repo), "--workspace-root", str(Path(td) / "ws")])

            self.assertEqual(rc, 0)
            self.assertEqual(m_connect.call_count, 2)
            m_ensure.assert_called_once()
            m_apply.assert_called_once()
            self.assertTrue(c2.closed)


if __name__ == "__main__":
    unittest.main()
