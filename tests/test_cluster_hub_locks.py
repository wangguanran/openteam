import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _add_pipelines_to_syspath():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = os.path.join(repo_root, "scripts", "pipelines")
    if p not in sys.path:
        sys.path.insert(0, p)


_add_pipelines_to_syspath()

import locks  # noqa: E402


class ClusterHubLocksTests(unittest.TestCase):
    def _fake_handle(self, backend: str) -> locks.LockHandle:
        return locks.LockHandle(lock_key=f"{backend}:test", backend=backend, holder={}, acquired_at="now", expires_at="")

    def test_cluster_lock_file_backend(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"OPENTEAM_HOME": str(Path(td) / ".openteam-home"), "OPENTEAM_RUNTIME_ROOT": ""}, clear=False):
            repo = Path(td)
            h = locks.acquire_cluster_lock(repo_root=repo, wait_sec=0.2, poll_sec=0.05, prefer_db=False)
            try:
                self.assertEqual(h.backend, "file")
                self.assertTrue((Path(td) / ".openteam-home" / "runtime" / "default" / "state" / "locks" / "cluster.lock").exists())
            finally:
                locks.release_lock(h)

    def test_hub_lock_file_backend(self):
        with tempfile.TemporaryDirectory() as td:
            hub = Path(td) / ".openteam" / "hub"
            h = locks.acquire_hub_lock(hub_root=hub, wait_sec=0.2, poll_sec=0.05, prefer_db=False)
            try:
                self.assertEqual(h.backend, "file")
                self.assertTrue((hub / "state" / "locks" / "hub.lock").exists())
            finally:
                locks.release_lock(h)

    def test_cluster_lock_prefers_db_when_url_set(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"OPENTEAM_DB_URL": "postgresql://example.local/openteam"}):
            repo = Path(td)
            db_handle = self._fake_handle("db_advisory")
            with mock.patch.object(locks, "_acquire_db_advisory_lock", return_value=db_handle) as db_acquire, mock.patch.object(locks, "_acquire_file_lock") as file_acquire:
                got = locks.acquire_cluster_lock(repo_root=repo, wait_sec=0.2, poll_sec=0.05)
            self.assertIs(got, db_handle)
            db_acquire.assert_called_once()
            file_acquire.assert_not_called()

    def test_cluster_lock_falls_back_to_file_on_db_unavailable(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"OPENTEAM_DB_URL": "postgresql://example.local/openteam"}):
            repo = Path(td)
            file_handle = self._fake_handle("file")
            with mock.patch.object(locks, "_acquire_db_advisory_lock", side_effect=locks.DbUnavailable("db down")) as db_acquire, mock.patch.object(
                locks, "_acquire_file_lock", return_value=file_handle
            ) as file_acquire:
                got = locks.acquire_cluster_lock(repo_root=repo, wait_sec=0.2, poll_sec=0.05)
            self.assertIs(got, file_handle)
            db_acquire.assert_called_once()
            file_acquire.assert_called_once()

    def test_cluster_lock_busy_from_db_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"OPENTEAM_DB_URL": "postgresql://example.local/openteam"}):
            repo = Path(td)
            busy = locks.LockBusy(lock_key="cluster:global", backend="db_advisory", holder={}, waited_sec=0.2)
            with mock.patch.object(locks, "_acquire_db_advisory_lock", side_effect=busy) as db_acquire, mock.patch.object(locks, "_acquire_file_lock") as file_acquire:
                with self.assertRaises(locks.LockBusy):
                    _ = locks.acquire_cluster_lock(repo_root=repo, wait_sec=0.2, poll_sec=0.05)
            db_acquire.assert_called_once()
            file_acquire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
