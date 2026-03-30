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
    def test_help_no_longer_lists_hub_cluster_or_node_commands(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
            openteam_legacy.main(["--help"])

        self.assertEqual(ctx.exception.code, 0)
        out = stdout.getvalue()
        self.assertNotIn(" hub ", out)
        self.assertNotIn(" cluster ", out)
        self.assertNotIn(" node ", out)

    def test_removed_hub_cluster_and_node_commands_are_invalid(self) -> None:
        for command in ("hub", "cluster", "node"):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
                openteam_legacy.main([command, "--help"])

            self.assertEqual(ctx.exception.code, 2)
            err = stderr.getvalue()
            self.assertIn("invalid choice", err)
            self.assertIn(command, err)


if __name__ == "__main__":
    unittest.main()
