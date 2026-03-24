import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


def _add_template_app_to_syspath():
    # Import requirements protocol from the runtime template (source of truth for control-plane logic).
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

# Unit tests must be offline/fast: disable Codex semantic check (LLM).
os.environ["OPENTEAM_REQUIREMENTS_SEMANTIC_CHECK"] = "0"

from app.requirements_store import add_requirement_raw_first, verify_requirements_raw_first  # noqa: E402


class RequirementsRawFirstTests(unittest.TestCase):
    def test_raw_first_capture_even_on_yaml_parse_error_drift(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            # Pre-existing invalid Expanded file should NOT prevent raw capture.
            (req_dir / "requirements.yaml").write_text(":\n", encoding="utf-8")

            out = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="逐字原文 A\n第二行",
                source="cli",
                channel="cli",
                user="tester",
            )
            self.assertEqual(out.classification, "DRIFT")

            raw = req_dir / "raw_inputs.jsonl"
            self.assertTrue(raw.exists())
            lines = [ln for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1)
            item = json.loads(lines[0])
            self.assertEqual(item["text"], "逐字原文 A\n第二行")
            self.assertEqual(item["channel"], "cli")

            self.assertTrue(out.drift_report_path)
            self.assertTrue((req_dir / str(out.drift_report_path)).exists())

    def test_baseline_v1_create_once_never_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            out1 = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="这是 baseline 的原始描述（v1）",
                source="cli",
                channel="cli",
                user="tester",
            )
            self.assertIn(out1.classification, ("COMPATIBLE", "CONFLICT", "DUPLICATE"))

            b1 = req_dir / "baseline" / "original_description_v1.md"
            self.assertTrue(b1.exists())
            c1 = b1.read_text(encoding="utf-8")
            self.assertIn("这是 baseline 的原始描述（v1）", c1)

            _ = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="第二次输入不应覆盖 baseline v1",
                source="cli",
                channel="cli",
                user="tester",
            )
            c2 = b1.read_text(encoding="utf-8")
            self.assertIn("这是 baseline 的原始描述（v1）", c2)
            self.assertNotIn("第二次输入不应覆盖 baseline v1", c2)

    def test_conflict_generates_report_and_need_pm_decision(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            _ = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="必须默认使用 Codex CLI 的 ChatGPT OAuth（codex login）作为认证方式。",
                source="cli",
                channel="cli",
                user="tester",
            )
            out2 = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="禁止 OAuth；必须使用 API key。",
                source="cli",
                channel="cli",
                user="tester",
            )
            self.assertEqual(out2.classification, "CONFLICT")
            self.assertTrue(out2.conflict_report_path)
            self.assertTrue(Path(str(out2.conflict_report_path)).exists() or (req_dir / str(out2.conflict_report_path)).exists())

            data = yaml.safe_load((req_dir / "requirements.yaml").read_text(encoding="utf-8")) or {}
            reqs = data.get("requirements") or []
            statuses = {r.get("req_id"): str(r.get("status")).upper() for r in reqs}
            # Both sides should be NEED_PM_DECISION after conflict.
            self.assertTrue(any(st == "NEED_PM_DECISION" for st in statuses.values()))

    def test_compatible_appends_and_updates_md_and_changelog_with_raw_id(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            _ = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="实现一个旅行 AI 规划助手（支持微信小程序、iOS、Android）。",
                source="cli",
                channel="cli",
                user="tester",
            )
            out2 = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="增加离线缓存：无网时仍能查看最近一次行程。",
                source="cli",
                channel="cli",
                user="tester",
            )
            self.assertEqual(out2.classification, "COMPATIBLE")
            self.assertTrue(out2.req_id)
            self.assertTrue(out2.raw_id)

            y = yaml.safe_load((req_dir / "requirements.yaml").read_text(encoding="utf-8")) or {}
            reqs = y.get("requirements") or []
            self.assertTrue(any(str(r.get("req_id")) == out2.req_id for r in reqs))
            r2 = next(r for r in reqs if str(r.get("req_id")) == out2.req_id)
            self.assertIn(out2.raw_id, r2.get("raw_input_refs") or [])

            md = (req_dir / "REQUIREMENTS.md").read_text(encoding="utf-8")
            self.assertIn(out2.req_id, md)

            ch = (req_dir / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertIn(f"raw={out2.raw_id}", ch)

    def test_verify_detects_md_drift(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            _ = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="一个需求",
                source="cli",
                channel="cli",
                user="tester",
            )
            md = req_dir / "REQUIREMENTS.md"
            md.write_text(md.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")
            out = verify_requirements_raw_first(req_dir, project_id="demo")
            self.assertFalse(out.get("ok"))
            drift = out.get("drift") or {}
            self.assertFalse(drift.get("ok"))
            pts = drift.get("points") or []
            self.assertTrue(any("REQUIREMENTS.md drift" in str(p) for p in pts))

    def test_feasibility_report_and_assessment_index_written(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            out = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="实现一个简单的离线缓存功能。",
                source="cli",
                channel="cli",
                user="tester",
            )
            self.assertTrue(out.raw_id)
            self.assertTrue(out.feasibility_outcome)
            self.assertTrue((req_dir / "raw_assessments.jsonl").exists())
            assess_lines = [ln for ln in (req_dir / "raw_assessments.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertTrue(assess_lines)
            found = None
            for ln in assess_lines:
                obj = json.loads(ln)
                if obj.get("raw_id") == out.raw_id:
                    found = obj
            self.assertIsNotNone(found)
            self.assertEqual(str(found.get("outcome") or "").upper(), str(out.feasibility_outcome or "").upper())
            rel = str(found.get("report_path") or "")
            self.assertTrue(rel)
            self.assertTrue((req_dir / rel).exists())
            content = (req_dir / rel).read_text(encoding="utf-8")
            self.assertIn(out.raw_id, content)

    def test_needs_info_stops_expansion_and_creates_need_pm_decision(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            out = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="TODO: 需要补充更多细节。",
                source="cli",
                channel="cli",
                user="tester",
            )
            self.assertEqual(out.classification, "NEED_PM_DECISION")
            self.assertTrue(out.raw_id)
            y = yaml.safe_load((req_dir / "requirements.yaml").read_text(encoding="utf-8")) or {}
            reqs = y.get("requirements") or []
            self.assertEqual(len(reqs), 1)
            self.assertEqual(str(reqs[0].get("status") or "").upper(), "NEED_PM_DECISION")
            self.assertTrue(out.pending_decisions)
            self.assertTrue(any(str(d.get("type") or "") == "REQUIREMENT_FEASIBILITY" for d in (out.pending_decisions or [])))

    def test_system_source_does_not_write_raw_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            req_dir = Path(td)
            _ = add_requirement_raw_first(
                project_id="demo",
                req_dir=req_dir,
                requirement_text="self-improve proposal should not enter raw_inputs.jsonl",
                source="self-improve",
                channel="api",
                user="self-improve",
            )
            raw = req_dir / "raw_inputs.jsonl"
            self.assertTrue(raw.exists())
            lines = [ln for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 0)


if __name__ == "__main__":
    unittest.main()
