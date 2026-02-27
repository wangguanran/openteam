import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RepoPurityEvals(unittest.TestCase):
    def test_repo_purity_passes_for_current_repo(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "governance" / "check_repo_purity.py"
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
        self.assertTrue(bool(data.get("ok")), msg=f"expected repo purity pass, got: {json.dumps(data, ensure_ascii=False)[:2000]}")

    def test_repo_purity_fails_when_legacy_team_os_dir_exists(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "governance" / "check_repo_purity.py"
        self.assertTrue(script.exists(), f"missing checker: {script}")

        with tempfile.TemporaryDirectory() as td:
            fake = Path(td)
            # minimal allowlist root markers
            (fake / "docs").mkdir(parents=True, exist_ok=True)
            (fake / "scripts").mkdir(parents=True, exist_ok=True)
            (fake / ".team-os").mkdir(parents=True, exist_ok=True)

            p = subprocess.run(
                [sys.executable, str(script), "--repo-root", str(fake), "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            out = (p.stdout or "").strip()
            self.assertTrue(out, f"empty output from repo purity checker; stderr={p.stderr[:200]}")
            data = json.loads(out)
            self.assertFalse(bool(data.get("ok")), msg=f"expected repo purity failure, got: {json.dumps(data, ensure_ascii=False)[:2000]}")
        kinds = {str(v.get("kind") or "") for v in (data.get("violations") or [])}
        self.assertIn("LEGACY_TEAM_OS_DIR", kinds)


if __name__ == "__main__":
    unittest.main()
