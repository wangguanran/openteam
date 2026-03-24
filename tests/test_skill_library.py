import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import crewai_spec_loader  # noqa: E402
from app.skill_library import builtin_skills  # noqa: E402
from app.skill_library import executor as skill_executor  # noqa: E402
from app.skill_library import registry as skill_registry  # noqa: E402


class SkillLibraryTests(unittest.TestCase):
    def test_skill_specs_are_loaded_from_skill_library(self) -> None:
        ids = {spec.skill_id for spec in skill_registry.list_skill_specs()}
        self.assertIn("team.collect-context", ids)
        self.assertIn("team.materialize-findings", ids)
        self.assertIn("team.claim-discussion", ids)
        self.assertIn("team.run-coding-pipeline", ids)

    def test_execute_skill_uses_registered_handler(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            context = SimpleNamespace(
                project_id="openteam",
                target_id="demo-target",
                force=False,
                workflow=SimpleNamespace(lane="feature", max_candidates=lambda: 5),
                extra={},
            )
            target = {"target_id": "demo-target", "project_id": "openteam", "repo_root": str(repo), "repo_locator": "foo/bar"}
            repo_context = {"repo_locator": "foo/bar", "current_version": "0.1.0"}
            outputs = {
                "project_id": "openteam",
                "target": target,
                "target_id": "demo-target",
                "repo_root": str(repo),
                "scan_repo_root": str(repo),
                "repo_locator": "foo/bar",
                "repo_context": repo_context,
                "repo_context_blob": "{}",
                "current_version": "0.1.0",
                "max_findings": 5,
                "bug_scan_dormant": False,
                "bug_scan_policy": {},
                "skip": False,
                "skip_reason": "",
            }
            with mock.patch.object(builtin_skills, "_safe_target_from_context", return_value=target), mock.patch.object(
                builtin_skills,
                "_repo_context_outputs",
                return_value=outputs,
            ):
                out = skill_executor.execute_skill("team.collect-context", context=context, inputs={}, state={})

        self.assertTrue(out["ok"])
        self.assertEqual(out["outputs"]["repo_locator"], "foo/bar")
        self.assertEqual(out["outputs"]["target_id"], "demo-target")

    def test_execute_skill_prefers_team_local_skill_spec(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            global_skills = root / "skill_library" / "specs"
            team_skills = root / "teams" / "demo_team" / "specs" / "skills"
            global_skills.mkdir(parents=True, exist_ok=True)
            team_skills.mkdir(parents=True, exist_ok=True)
            (global_skills / "shared-skill.yaml").write_text(
                "\n".join(
                    [
                        "skill_id: shared.skill",
                        "handler_id: global.handler",
                        "display_name_zh: 全局技能",
                    ]
                ),
                encoding="utf-8",
            )
            (team_skills / "shared-skill.yaml").write_text(
                "\n".join(
                    [
                        "skill_id: shared.skill",
                        "handler_id: team.handler",
                        "display_name_zh: 团队技能",
                    ]
                ),
                encoding="utf-8",
            )
            context = SimpleNamespace(workflow=SimpleNamespace(team_id="demo-team"))
            with mock.patch.object(crewai_spec_loader, "skill_specs_root", return_value=global_skills), mock.patch.object(
                crewai_spec_loader,
                "teams_root",
                return_value=root / "teams",
            ), mock.patch.dict(
                skill_executor._SKILL_HANDLERS,
                {
                    "global.handler": lambda **_: {"ok": True, "outputs": {"handler": "global"}},
                    "team.handler": lambda **_: {"ok": True, "outputs": {"handler": "team"}},
                },
                clear=False,
            ):
                crewai_spec_loader.clear_spec_caches()
                old_loaded = skill_executor._HANDLER_MODULES_LOADED
                skill_executor._HANDLER_MODULES_LOADED = True
                try:
                    out = skill_executor.execute_skill("shared.skill", context=context, inputs={}, state={})
                finally:
                    skill_executor._HANDLER_MODULES_LOADED = old_loaded

            self.assertTrue(out["ok"])
            self.assertEqual(out["outputs"]["handler"], "team")
        crewai_spec_loader.clear_spec_caches()


if __name__ == "__main__":
    unittest.main()
