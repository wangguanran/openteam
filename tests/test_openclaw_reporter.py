import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

from app import openclaw_reporter  # noqa: E402
from app.runtime_db import EventRow  # noqa: E402


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def list_events(self, *, after_id: int = 0, limit: int = 100):
        return [row for row in self._rows if row.id > after_id][:limit]


class OpenClawReporterTests(unittest.TestCase):
    def test_save_and_load_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            env = {
                "OPENTEAM_RUNTIME_ROOT": td,
                "HOME": td,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                cfg = openclaw_reporter.save_config(
                    {
                        "enabled": True,
                        "channel": "telegram",
                        "target": "@openteam",
                        "path_patterns": ["scaffolds/runtime/orchestrator/app/**"],
                        "event_types": ["SELF_UPGRADE_*"],
                    }
                )
                loaded = openclaw_reporter.load_config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(loaded["target"], "@openteam")
        self.assertEqual(loaded["path_patterns"], ["scaffolds/runtime/orchestrator/app/**"])

    def test_detect_openclaw_infers_remote_gateway_in_container(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / ".openclaw"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "openclaw.json").write_text(
                json.dumps(
                    {
                        "gateway": {
                            "port": 18789,
                            "mode": "local",
                            "auth": {"mode": "token", "token": "abc123"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "OPENTEAM_RUNTIME_ROOT": td,
                "HOME": td,
            }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "app.openclaw_reporter._running_in_container",
            return_value=True,
        ), mock.patch(
            "app.openclaw_reporter._which_openclaw",
            return_value="/usr/local/bin/openclaw",
        ), mock.patch(
            "app.openclaw_reporter._infer_gateway_defaults",
            return_value={
                "gateway_mode": "remote",
                "gateway_url": "ws://192.168.65.254:18789",
                "gateway_transport": "direct",
                "allow_insecure_private_ws": "1",
            },
        ):
            out = openclaw_reporter.detect_openclaw(probe_health=False)
        self.assertEqual(out["gateway_mode"], "remote")
        self.assertEqual(out["gateway_url"], "ws://192.168.65.254:18789")
        self.assertTrue(out["available"])

    def test_health_uses_remote_temp_config_when_gateway_url_present(self):
        with tempfile.TemporaryDirectory() as td:
            env = {
                "OPENTEAM_RUNTIME_ROOT": td,
                "HOME": td,
                "OPENTEAM_OPENCLAW_GATEWAY_URL": "ws://host.docker.internal:18789",
                "OPENTEAM_OPENCLAW_GATEWAY_TOKEN": "abc123",
                "OPENTEAM_OPENCLAW_ALLOW_INSECURE_PRIVATE_WS": "1",
            }
            captured: dict[str, str] = {}

            def _fake_run(cmd, **kwargs):
                env_map = kwargs.get("env") or {}
                captured["config_path"] = str(env_map.get("OPENCLAW_CONFIG_PATH") or "")
                captured["state_dir"] = str(env_map.get("OPENCLAW_STATE_DIR") or "")
                captured["allow_insecure_private_ws"] = str(env_map.get("OPENCLAW_ALLOW_INSECURE_PRIVATE_WS") or "")
                cfg_raw = Path(captured["config_path"]).read_text(encoding="utf-8")
                captured["config_raw"] = cfg_raw
                return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "app.openclaw_reporter._which_openclaw",
                return_value="/usr/local/bin/openclaw",
            ), mock.patch(
                "app.openclaw_reporter._running_in_container",
                return_value=True,
            ), mock.patch(
                "subprocess.run",
                side_effect=_fake_run,
            ):
                out = openclaw_reporter.health(timeout_ms=1000)
        self.assertTrue(out["ok"])
        self.assertIn('"mode": "remote"', captured["config_raw"])
        self.assertIn('"url": "ws://host.docker.internal:18789"', captured["config_raw"])
        self.assertIn('"token": "abc123"', captured["config_raw"])
        self.assertTrue(captured["state_dir"].endswith("openclaw-client"))
        self.assertTrue(captured["config_path"].startswith(captured["state_dir"]))
        self.assertEqual(captured["allow_insecure_private_ws"], "1")
        self.assertFalse(Path(captured["config_path"]).exists())

    def test_report_event_filters_by_path(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".openclaw").mkdir(parents=True, exist_ok=True)
            ((Path(td) / ".openclaw") / "openclaw.json").write_text("{}\n", encoding="utf-8")
            env = {
                "OPENTEAM_RUNTIME_ROOT": td,
                "HOME": td,
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "app.openclaw_reporter._which_openclaw",
                return_value="/usr/local/bin/openclaw",
            ), mock.patch(
                "app.openclaw_reporter.health",
                return_value={"ok": True},
            ), mock.patch(
                "app.openclaw_reporter._run_send",
                return_value={"ok": True},
            ) as send_mock:
                openclaw_reporter.save_config(
                    {
                        "enabled": True,
                        "channel": "telegram",
                        "target": "@openteam",
                        "path_patterns": ["scaffolds/runtime/orchestrator/app/**"],
                        "event_types": ["SELF_UPGRADE_*"],
                    }
                )
                skipped = openclaw_reporter.report_event(
                    {
                        "id": 1,
                        "ts": "2026-03-07T00:00:00Z",
                        "event_type": "SELF_UPGRADE_TASK_DELIVERY_FINISHED",
                        "project_id": "openteam",
                        "workstream_id": "general",
                        "payload": {"changed_files": ["docs/README.md"]},
                    }
                )
                sent = openclaw_reporter.report_event(
                    {
                        "id": 2,
                        "ts": "2026-03-07T00:00:01Z",
                        "event_type": "SELF_UPGRADE_TASK_DELIVERY_FINISHED",
                        "project_id": "openteam",
                        "workstream_id": "general",
                        "payload": {"changed_files": ["scaffolds/runtime/orchestrator/app/main.py"]},
                    }
                )
        self.assertFalse(skipped["sent"])
        self.assertEqual(skipped["reason"], "path_filtered")
        self.assertTrue(sent["sent"])
        self.assertEqual(send_mock.call_count, 1)

    def test_sweep_events_advances_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".openclaw").mkdir(parents=True, exist_ok=True)
            ((Path(td) / ".openclaw") / "openclaw.json").write_text("{}\n", encoding="utf-8")
            env = {
                "OPENTEAM_RUNTIME_ROOT": td,
                "HOME": td,
            }
            rows = [
                EventRow(id=1, ts="2026-03-07T00:00:00Z", event_type="OPENCLAW_CONFIG_UPDATED", actor="api", project_id="openteam", workstream_id="general", payload={}),
                EventRow(id=2, ts="2026-03-07T00:00:01Z", event_type="SELF_UPGRADE_TASK_DELIVERY_BLOCKED", actor="api", project_id="openteam", workstream_id="general", payload={"changed_files": ["scaffolds/runtime/orchestrator/app/main.py"]}),
            ]
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "app.openclaw_reporter._which_openclaw",
                return_value="/usr/local/bin/openclaw",
            ), mock.patch(
                "app.openclaw_reporter.health",
                return_value={"ok": True},
            ), mock.patch(
                "app.openclaw_reporter._run_send",
                return_value={"ok": True},
            ):
                openclaw_reporter.save_config(
                    {
                        "enabled": True,
                        "channel": "telegram",
                        "target": "@openteam",
                        "path_patterns": ["*"],
                        "event_types": ["SELF_UPGRADE_*"],
                    }
                )
                out = openclaw_reporter.sweep_events(db=_FakeDB(rows), dry_run=False, limit=20)
                state = openclaw_reporter.load_state()
        self.assertTrue(out["ok"])
        self.assertEqual(out["scanned"], 2)
        self.assertEqual(out["sent"], 1)
        self.assertEqual(state["cursor"], 2)

    def test_report_manual_requires_target(self):
        with tempfile.TemporaryDirectory() as td:
            env = {
                "OPENTEAM_RUNTIME_ROOT": td,
                "HOME": td,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                openclaw_reporter.save_config({"enabled": True, "target": ""})
                with self.assertRaises(openclaw_reporter.OpenClawReporterError):
                    openclaw_reporter.report_manual(message="hello", dry_run=True)


if __name__ == "__main__":
    unittest.main()
