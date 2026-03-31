import sys
import unittest
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_template_app_to_syspath() -> None:
    app_dir = _repo_root() / "scaffolds" / "runtime" / "orchestrator"
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))


_add_template_app_to_syspath()


class RepoImprovementTeamLayoutTests(unittest.TestCase):
    def test_repo_improvement_team_directory_only_contains_specs(self) -> None:
        team_dir = _repo_root() / "scaffolds" / "runtime" / "orchestrator" / "app" / "teams" / "repo_improvement"
        py_files = sorted(str(path.relative_to(team_dir)) for path in team_dir.rglob("*.py"))
        self.assertEqual(py_files, [])

    def test_repo_review_workflow_agents_do_not_hardcode_openrouter(self) -> None:
        import yaml

        workflow_path = (
            _repo_root()
            / "scaffolds"
            / "runtime"
            / "orchestrator"
            / "app"
            / "teams"
            / "repo_improvement"
            / "specs"
            / "workflows"
            / "repo-review.yaml"
        )
        data = yaml.safe_load(workflow_path.read_text(encoding="utf-8")) or {}
        for agent in data.get("agents") or []:
            if isinstance(agent, dict):
                self.assertNotIn("openrouter/", str(agent.get("model") or ""))


if __name__ == "__main__":
    unittest.main()
