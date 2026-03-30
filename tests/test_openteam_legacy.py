import argparse
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openteam_legacy


class OpenTeamLegacyTests(unittest.TestCase):
    def test_cmd_cluster_status_uses_single_node_status_view(self) -> None:
        args = argparse.Namespace(profile=None)
        stdout = io.StringIO()

        with (
            mock.patch.object(openteam_legacy, "_base_url", return_value=("http://cp.local", {"name": "local"})),
            mock.patch.object(
                openteam_legacy,
                "_team_status_doc",
                return_value={
                    "instance_id": "local-1",
                    "pending_decisions": [{"type": "APPROVAL", "task_id": "T-1"}],
                },
            ),
            contextlib.redirect_stdout(stdout),
        ):
            openteam_legacy.cmd_cluster_status(args)

        out = stdout.getvalue()
        self.assertIn("profile=local base_url=http://cp.local", out)
        self.assertIn("leader.instance_id=local-1", out)
        self.assertIn("leader.backend=local", out)
        self.assertIn("PENDING_DECISIONS=1", out)
        self.assertNotIn("nodes=", out)


if __name__ == "__main__":
    unittest.main()
