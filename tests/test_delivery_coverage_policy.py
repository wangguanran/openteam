from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeliveryCoveragePolicyTests(unittest.TestCase):
    def test_policy_rejects_core_module_below_full_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "coverage.json"
            report.write_text(
                json.dumps(
                    {
                        "files": {
                            "scaffolds/runtime/orchestrator/app/domains/delivery_studio/models.py": {"summary": {"percent_covered": 99.0}},
                            "scaffolds/runtime/orchestrator/app/domains/delivery_studio/runtime.py": {"summary": {"percent_covered": 92.0}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                ["python3", str(ROOT / "scripts" / "ci" / "check_delivery_coverage.py"), "--report", str(report)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("models.py", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
