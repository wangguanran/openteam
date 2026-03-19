import os
import sys
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

from app import crew_tools  # noqa: E402
from app.crewai_orchestrator import RunSpec, run_once  # noqa: E402


class _FakeDB:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.runs: dict[str, dict] = {}
        self._n = 0

    def upsert_run(self, *, run_id=None, project_id: str, workstream_id: str, objective: str, state: str) -> str:
        if not run_id:
            self._n += 1
            run_id = f"run-{self._n}"
        self.runs[str(run_id)] = {
            "project_id": project_id,
            "workstream_id": workstream_id,
            "objective": objective,
            "state": state,
        }
        return str(run_id)

    def add_event(self, *, event_type: str, actor: str, project_id: str, workstream_id: str, payload: dict) -> None:
        self.events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "project_id": project_id,
                "workstream_id": workstream_id,
                "payload": payload,
            }
        )

    def update_run_state(self, *, run_id: str, state: str) -> None:
        self.runs[str(run_id)]["state"] = state


class CrewOrchestratorTests(unittest.TestCase):
    def test_flow_alias_maps_to_deterministic_pipeline_chain(self):
        self.assertEqual(crew_tools.flow_to_pipelines("maintenance"), ["doctor", "db_migrate"])

    def test_team_flow_is_native_crewai_flow(self):
        self.assertEqual(crew_tools.normalize_flow("team:repo-improvement"), "team:repo-improvement")
        self.assertTrue(crew_tools.is_native_crewai_flow("team:repo-improvement"))

    def test_direct_pipeline_allowlist_accepts_supported_pipeline(self):
        self.assertEqual(crew_tools.flow_to_pipelines("pipeline:doctor"), ["doctor"])

    def test_direct_pipeline_allowlist_rejects_native_workflow_name(self):
        with self.assertRaises(crew_tools.CrewToolsError):
            crew_tools.flow_to_pipelines("pipeline:team:repo-improvement")

    def test_direct_pipeline_allowlist_rejects_unsupported_pipeline(self):
        with self.assertRaises(crew_tools.CrewToolsError):
            crew_tools.flow_to_pipelines("pipeline:task_create")

    def test_run_request_legacy_pipeline_field_routes_to_pipeline_mode(self):
        flow = crew_tools.resolve_run_request_flow(flow=None, pipeline="db_migrate")
        self.assertEqual(flow, "pipeline:db_migrate")

    def test_run_once_emits_explicit_pipeline_write_delegation_evidence(self):
        db = _FakeDB()
        spec = RunSpec(project_id="teamos", workstream_id="general", objective="run checks", flow="standard")

        with mock.patch(
            "app.crewai_orchestrator.crewai_runtime.require_crewai_importable",
            return_value={"importable": True, "version": "test", "module_path": "/tmp/crewai/__init__.py", "source_path": "/tmp/crewai-src"},
        ), mock.patch("app.crewai_orchestrator.team_os_root", return_value=Path("/tmp/team-os")), mock.patch(
            "app.crewai_orchestrator.crew_tools.workspace_root", return_value=Path("/tmp/ws")
        ), mock.patch(
            "app.crewai_orchestrator.crew_tools.run_pipeline",
            return_value={
                "pipeline": "doctor",
                "script_path": "/tmp/team-os/scripts/pipelines/doctor.py",
                "returncode": 0,
                "stdout": "{\"ok\": true}",
                "stderr": "",
                "write_delegate": {
                    "write_mode": "delegated_pipeline_script",
                    "writer": "deterministic_pipeline_script",
                    "pipeline": "doctor",
                    "script_path": "/tmp/team-os/scripts/pipelines/doctor.py",
                    "agent_truth_source_write": "disabled",
                },
            },
        ):
            out = run_once(db=db, spec=spec, actor="test")

        self.assertTrue(out["ok"])
        started = next(e for e in db.events if e["event_type"] == "RUN_STARTED")
        self.assertEqual(started["payload"]["write_delegate"]["writer"], "deterministic_pipeline_scripts")
        self.assertEqual(started["payload"]["write_delegate"]["agent_truth_source_write"], "disabled")
        delegated = next(e for e in db.events if e["event_type"] == "RUN_PIPELINE_DELEGATED")
        self.assertEqual(delegated["payload"]["write_delegate"]["write_mode"], "delegated_pipeline_script")

    def test_run_once_rejects_unsupported_direct_pipeline_with_allowlist_context(self):
        db = _FakeDB()
        spec = RunSpec(project_id="teamos", workstream_id="general", objective="bad request", flow="pipeline:task_create")
        with mock.patch(
            "app.crewai_orchestrator.crewai_runtime.require_crewai_importable",
            return_value={"importable": True, "version": "test", "module_path": "/tmp/crewai/__init__.py", "source_path": "/tmp/crewai-src"},
        ):
            out = run_once(db=db, spec=spec, actor="test")
        self.assertFalse(out["ok"])
        self.assertIn("unsupported_direct_pipeline", out["error"])
        self.assertIn("doctor", out["direct_pipeline_allowlist"])
        failed = next(e for e in db.events if e["event_type"] == "RUN_FAILED")
        self.assertIn("direct_pipeline_allowlist", failed["payload"])

    def test_run_once_team_workflow_uses_team_runtime_adapter(self):
        db = _FakeDB()
        spec = RunSpec(project_id="teamos", workstream_id="general", objective="upgrade", flow="team:repo-improvement", repo_path="/tmp/team-os")
        adapter = SimpleNamespace(
            run_once_fn=mock.Mock(
                return_value={
                    "ok": True,
                    "summary": "planned",
                    "records": [{"title": "Add CI", "task_id": "TEAMOS-0001"}],
                    "panel_sync": {"ok": True},
                    "report_path": "/tmp/report.json",
                    "write_delegate": {"writer": "crewai_agents", "write_mode": "workflow_runner"},
                }
            )
        )

        with mock.patch(
            "app.crewai_orchestrator.crewai_runtime.require_crewai_importable",
            return_value={"importable": True, "version": "test", "module_path": "/tmp/crewai/__init__.py", "source_path": "/tmp/crewai-src"},
        ), mock.patch(
            "app.crewai_orchestrator.team_runtime_registry.team_runtime_adapter",
            return_value=adapter,
        ) as mocked_adapter:
            out = run_once(db=db, spec=spec, actor="test")

        self.assertTrue(out["ok"])
        mocked_adapter.assert_called_once_with("repo-improvement")
        adapter.run_once_fn.assert_called_once()
        started = next(e for e in db.events if e["event_type"] == "RUN_STARTED")
        self.assertEqual(started["payload"]["write_delegate"]["writer"], "crewai_agents")
        finished = next(e for e in db.events if e["event_type"] == "RUN_FINISHED")
        self.assertEqual(finished["payload"]["write_delegate"]["write_mode"], "workflow_runner")


if __name__ == "__main__":
    unittest.main()
