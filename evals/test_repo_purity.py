import json
import subprocess
import sys
import unittest
from pathlib import Path


class RepoPurityEvals(unittest.TestCase):
    def test_repo_is_pure_no_project_truth_sources_inside_repo(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / ".team-os" / "scripts" / "governance" / "check_repo_purity.py"
        self.assertTrue(script.exists(), f"missing checker: {script}")

        p = subprocess.run(
            [sys.executable, str(script), "--repo-root", str(repo_root), "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        out = (p.stdout or "").strip()
        self.assertTrue(out, f"empty output from repo purity checker; stderr={p.stderr[:200]}")
        data = json.loads(out)

        if not data.get("ok", False):
            violations = data.get("violations") or []
            sample = violations[:10]
            self.fail(
                "repo_purity FAIL. Run:\n"
                "  teamos workspace init\n"
                "  teamos workspace migrate --from-repo   # dry-run\n"
                "  teamos workspace migrate --from-repo --force   # apply (high risk)\n"
                f"\nviolations(sample)={json.dumps(sample, ensure_ascii=False, indent=2)[:2000]}"
            )


if __name__ == "__main__":
    unittest.main()

