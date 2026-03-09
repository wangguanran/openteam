import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


def _add_template_app_to_syspath():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

# Unit tests must be offline/fast: disable Codex semantic check (LLM).
os.environ["TEAMOS_REQUIREMENTS_SEMANTIC_CHECK"] = "0"

from app.requirements_store import add_requirement_system_update  # noqa: E402


class SystemRequirementsUpdateTests(unittest.TestCase):
    def test_system_update_does_not_write_raw_or_feasibility(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            # Baseline v1 must exist; system updates must not create baselines.
            (req_dir / "baseline").mkdir(parents=True, exist_ok=True)
            (req_dir / "baseline" / "original_description_v1.md").write_text("# baseline\n\nverbatim\n", encoding="utf-8")

            out = add_requirement_system_update(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="System update should not touch raw inputs.",
                source="SYSTEM_SELF_IMPROVE",
            )
            self.assertEqual(out.classification, "COMPATIBLE")
            self.assertTrue(out.req_id)

            raw = req_dir / "raw_inputs.jsonl"
            self.assertTrue(raw.exists())
            raw_lines = [ln for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(raw_lines), 0)

            assess = req_dir / "raw_assessments.jsonl"
            self.assertTrue(assess.exists())
            assess_lines = [ln for ln in assess.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(assess_lines), 0)

            feas_dir = req_dir / "feasibility"
            self.assertTrue(feas_dir.exists())
            self.assertEqual(len(list(feas_dir.glob("*.md"))), 0)

            y = yaml.safe_load((req_dir / "requirements.yaml").read_text(encoding="utf-8")) or {}
            reqs = y.get("requirements") or []
            self.assertTrue(reqs)
            r = next(x for x in reqs if str(x.get("req_id") or "") == out.req_id)
            self.assertEqual(str(r.get("source") or ""), "SYSTEM_SELF_IMPROVE")
            self.assertEqual(list(r.get("raw_input_refs") or []), [])


if __name__ == "__main__":
    unittest.main()
