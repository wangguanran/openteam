import os
import sys
import tempfile
import unittest
from pathlib import Path


def _add_pipelines_to_syspath():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    p = os.path.join(repo_root, ".team-os", "scripts", "pipelines")
    if p not in sys.path:
        sys.path.insert(0, p)


_add_pipelines_to_syspath()

import locks  # noqa: E402


class ClusterHubLocksTests(unittest.TestCase):
    def test_cluster_lock_file_backend(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".team-os" / "state" / "locks").mkdir(parents=True, exist_ok=True)
            h = locks.acquire_cluster_lock(repo_root=repo, wait_sec=0.2, poll_sec=0.05, prefer_db=False)
            try:
                self.assertEqual(h.backend, "file")
                self.assertTrue((repo / ".team-os" / "state" / "locks" / "cluster.lock").exists())
            finally:
                locks.release_lock(h)

    def test_hub_lock_file_backend(self):
        with tempfile.TemporaryDirectory() as td:
            hub = Path(td) / ".teamos" / "hub"
            h = locks.acquire_hub_lock(hub_root=hub, wait_sec=0.2, poll_sec=0.05, prefer_db=False)
            try:
                self.assertEqual(h.backend, "file")
                self.assertTrue((hub / "state" / "locks" / "hub.lock").exists())
            finally:
                locks.release_lock(h)


if __name__ == "__main__":
    unittest.main()
