import argparse
import contextlib
import io
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

# Ensure repo root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import team_os_cli
import team_os_cli._shared as _shared
import team_os_cli.http as _http
import team_os_cli.project as _project
import team_os_cli.team as _team


class TeamosReplTests(unittest.TestCase):

    def test_main_no_args_auto_enters_repl_from_runtime_workspace_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "runtime_root"
            workspace_root = runtime_root / "workspace"
            cwd = workspace_root / "projects" / "demo1" / "repo"
            cwd.mkdir(parents=True, exist_ok=True)
            old_cwd = Path.cwd()
            os.chdir(cwd)
            try:
                with mock.patch("team_os_cli._shared._load_config", return_value={}), mock.patch(
                    "team_os_cli._shared._workspace_root_from_cfg", return_value=Path("/tmp/unrelated_workspace_root")
                ), mock.patch("team_os_cli.project._project_repl", return_value=0) as repl, mock.patch(
                    "team_os_cli._project_repl", return_value=0
                ):
                    rc = team_os_cli.main([])
            finally:
                os.chdir(old_cwd)

            self.assertEqual(rc, 0)

    def test_project_repl_control_commands_do_not_write_raw(self) -> None:
        calls = []

        def fake_http(method, url, payload=None, timeout_sec=10, **kwargs):
            calls.append((method, url, payload, timeout_sec, kwargs))
            if url.endswith("/v1/status"):
                return {"instance_id": "i-001", "leader": {"leader_base_url": "http://leader.local"}}
            return {"summary": "ok"}

        args = argparse.Namespace(profile=None, workspace_root="/tmp/ws")
        stdout = io.StringIO()
        with mock.patch("team_os_cli.project._base_url", return_value=("http://cp.local", {"name": "local"})), mock.patch(
            "team_os_cli.project._http_json", side_effect=fake_http
        ), mock.patch("sys.stdin", io.StringIO("/help\n/status\n/exit\n")), contextlib.redirect_stdout(stdout):
            rc = _project._project_repl(args, project_id="demo1")

        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("输入会落盘为 Raw，不要输入密码/密钥", out)
        self.assertIn("commands: /exit /help /status", out)
        self.assertIn("status.instance_id=i-001", out)
        self.assertIn("status.leader_base_url=http://leader.local", out)

        add_calls = [c for c in calls if c[0] == "POST" and c[1].endswith("/v1/requirements/add")]
        self.assertEqual(add_calls, [])

        status_calls = [c for c in calls if c[0] == "GET" and c[1].endswith("/v1/status")]
        self.assertEqual(len(status_calls), 1)

    def test_project_repl_plain_text_writes_via_requirements_add(self) -> None:
        calls = []

        def fake_http(method, url, payload=None, timeout_sec=10, **kwargs):
            calls.append((method, url, payload, timeout_sec, kwargs))
            return {"summary": "REQ added"}

        args = argparse.Namespace(profile=None, workspace_root="/tmp/ws")
        with mock.patch("team_os_cli.project._base_url", return_value=("http://cp.local", {"name": "local"})), mock.patch(
            "team_os_cli.project._http_json", side_effect=fake_http
        ), mock.patch("sys.stdin", io.StringIO("need audit logs\n/exit\n")), contextlib.redirect_stdout(io.StringIO()):
            rc = _project._project_repl(args, project_id="demo1")

        self.assertEqual(rc, 0)
        add_calls = [c for c in calls if c[0] == "POST" and c[1] == "http://cp.local/v1/requirements/add"]
        self.assertEqual(len(add_calls), 1)
        payload = add_calls[0][2]
        self.assertEqual(payload["scope"], "project:demo1")
        self.assertEqual(payload["text"], "need audit logs")
        self.assertEqual(payload["source"], "cli")
        self.assertEqual(payload["workstream_id"], "general")

    def test_team_logs_defaults_to_latest_run(self) -> None:
        calls = []

        def fake_http(method, url, payload=None, timeout_sec=10, **kwargs):
            calls.append((method, url, payload, timeout_sec, kwargs))
            if url.endswith("/v1/status"):
                return {"teams": {"repo-improvement": {"last_run": {"run_id": "run-123"}}}}
            if url.endswith("/v1/teams/repo-improvement/runs/run-123/logs?limit=25"):
                return {
                    "run": {
                        "run_id": "run-123",
                        "state": "DONE",
                        "project_id": "teamos",
                        "workstream_id": "general",
                        "objective": "CLI-triggered team:repo-improvement",
                    },
                    "report_available": True,
                    "summary": "no provable defect signal found this round",
                    "team_id": "repo-improvement",
                    "saved_logs": {
                        "markdown_path": "/tmp/team/repo-improvement/run-123.md",
                        "json_path": "/tmp/team/repo-improvement/run-123.json",
                    },
                    "planning_agent_logs": [
                        {
                            "stage": "planning",
                            "task_name": "bug_scan",
                            "agent": "Test-Manager",
                            "raw": "0 bug findings",
                        }
                    ],
                    "events": [{"event_type": "RUN_FINISHED"}],
                }
            raise AssertionError(f"unexpected url: {url}")

        args = argparse.Namespace(profile=None, team_id="repo-improvement", run_id="", limit=25, json=False)
        stdout = io.StringIO()
        with mock.patch("team_os_cli.team._base_url", return_value=("http://cp.local", {"name": "local"})), mock.patch(
            "team_os_cli.team._http_json", side_effect=fake_http
        ), contextlib.redirect_stdout(stdout):
            _team.cmd_team_logs(args)

        out = stdout.getvalue()
        self.assertIn("Team Run", out)
        self.assertIn("team_id: repo-improvement", out)
        self.assertIn("run_id: run-123", out)
        self.assertIn("Planning Agent Logs", out)
        self.assertIn("1. Test-Manager :: bug_scan", out)
        self.assertIn("0 bug findings", out)
        self.assertIn("/tmp/team/repo-improvement/run-123.md", out)

    def test_team_watch_prints_sse_stream(self) -> None:
        class _FakeStream:
            def __init__(self, chunks: bytes) -> None:
                self._buf = io.BytesIO(chunks)

            def readline(self) -> bytes:
                return self._buf.readline()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        sse = b"".join(
            [
                b"event: run\n",
                b'data: {"run":{"run_id":"run-123","state":"RUNNING","project_id":"projectmanager","objective":"watch me"}}\n\n',
                b"event: agent\n",
                b'data: {"role_id":"Bug-TestCase-Agent","state":"RUNNING","task_id":"PROJECTMANAGER-0001","current_action":"bootstrapping failing bug test case"}\n\n',
                b"event: runtime_event\n",
                b'data: {"ts":"2026-03-12T08:21:15Z","event_type":"TEAM_WORKFLOW_PLANNING_TASK_OUTPUT","actor":"repo_improvement_api","payload":{"agent":"Test-Manager","task_name":"qa_bug_scan_src-ai-llm","raw":"found one bug"}}\n\n',
                b"event: end\n",
                b'data: {"run":{"run_id":"run-123","state":"DONE"}}\n\n',
            ]
        )

        args = argparse.Namespace(profile=None, team_id="repo-improvement", project_id="projectmanager", run_id="", timeout=30, json=False)
        stdout = io.StringIO()
        with mock.patch("team_os_cli.team._base_url", return_value=("http://cp.local", {"name": "local"})), mock.patch(
            "team_os_cli.team._resolve_team_watch_run_id", return_value="run-123"
        ), mock.patch("team_os_cli.team.urllib.request.urlopen", return_value=_FakeStream(sse)), contextlib.redirect_stdout(stdout):
            _team.cmd_team_watch(args)

        out = stdout.getvalue()
        self.assertIn("[run] run_id=run-123 state=RUNNING project_id=projectmanager", out)
        self.assertIn("[agent] Bug-TestCase-Agent state=RUNNING task=PROJECTMANAGER-0001", out)
        self.assertIn("[planning] Test-Manager :: qa_bug_scan_src-ai-llm", out)
        self.assertIn("found one bug", out)
        self.assertIn("[end] run_id=run-123 state=DONE", out)


if __name__ == "__main__":
    unittest.main()
