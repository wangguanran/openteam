from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scaffolds" / "runtime" / "scripts" / "auto_update.py"
SPEC = importlib.util.spec_from_file_location("runtime_auto_update", MODULE_PATH)
assert SPEC and SPEC.loader
runtime_auto_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_auto_update)


class RuntimeAutoUpdateTests(unittest.TestCase):
    def test_load_runtime_settings_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            settings = runtime_auto_update.load_runtime_settings(Path(td))
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["interval_sec"], 300)
        self.assertFalse(settings["only_if_idle"])
        self.assertEqual(settings["image"], "ghcr.io/wangguanran/teamos-control-plane:main")
        self.assertEqual(settings["port"], 8787)

    def test_load_runtime_settings_reads_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            (runtime_dir / ".env").write_text(
                "\n".join(
                    [
                        "TEAMOS_CONTROL_PLANE_AUTO_UPDATE=1",
                        "TEAMOS_CONTROL_PLANE_AUTO_UPDATE_INTERVAL_SEC=120",
                        "TEAMOS_CONTROL_PLANE_AUTO_UPDATE_ONLY_IF_IDLE=1",
                        "TEAMOS_CONTROL_PLANE_IMAGE=ghcr.io/example/custom:sha-123",
                        "CONTROL_PLANE_PORT=9999",
                    ]
                ),
                encoding="utf-8",
            )
            settings = runtime_auto_update.load_runtime_settings(runtime_dir)
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["interval_sec"], 120)
        self.assertTrue(settings["only_if_idle"])
        self.assertEqual(settings["image"], "ghcr.io/example/custom:sha-123")
        self.assertEqual(settings["port"], 9999)

    def test_should_restart_when_new_image_pulled(self) -> None:
        self.assertTrue(
            runtime_auto_update.should_restart_control_plane(
                "sha256:old",
                "sha256:new",
                "sha256:old",
            )
        )

    def test_should_restart_when_running_container_lags_local_tag(self) -> None:
        self.assertTrue(
            runtime_auto_update.should_restart_control_plane(
                "sha256:same",
                "sha256:same",
                "sha256:old",
            )
        )

    def test_should_not_restart_when_already_current(self) -> None:
        self.assertFalse(
            runtime_auto_update.should_restart_control_plane(
                "sha256:same",
                "sha256:same",
                "sha256:same",
            )
        )

    def test_run_update_check_skips_when_active_runs_exist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            with (
                patch.object(runtime_auto_update, "load_runtime_settings", return_value={
                    "enabled": True,
                    "interval_sec": 300,
                    "only_if_idle": True,
                    "image": "ghcr.io/example/custom:main",
                    "port": 8787,
                    "base_url": "http://127.0.0.1:8787",
                }),
                patch.object(runtime_auto_update, "query_active_run_count", return_value=2),
                patch.object(runtime_auto_update, "_run") as run_mock,
            ):
                out = runtime_auto_update.run_update_check(runtime_dir)
        self.assertEqual(out["status"], "skipped_active_runs")
        run_mock.assert_not_called()

    def test_run_update_check_restarts_when_new_image_is_pulled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            fake_pull = unittest.mock.Mock(returncode=0, stdout="", stderr="")
            fake_up = unittest.mock.Mock(returncode=0, stdout="", stderr="")
            with (
                patch.object(runtime_auto_update, "load_runtime_settings", return_value={
                    "enabled": True,
                    "interval_sec": 300,
                    "only_if_idle": False,
                    "image": "ghcr.io/example/custom:main",
                    "port": 8787,
                    "base_url": "http://127.0.0.1:8787",
                }),
                patch.object(runtime_auto_update, "query_active_run_count", return_value=0),
                patch.object(runtime_auto_update, "local_image_id", side_effect=["sha256:old", "sha256:new"]),
                patch.object(runtime_auto_update, "current_control_plane_image_id", side_effect=["sha256:old", "sha256:new"]),
                patch.object(runtime_auto_update, "_run", side_effect=[fake_pull, fake_up]) as run_mock,
            ):
                out = runtime_auto_update.run_update_check(runtime_dir)
        self.assertEqual(out["status"], "updated")
        self.assertEqual(run_mock.call_args_list[0].args[0], ["docker", "compose", "pull", "control-plane"])
        self.assertEqual(
            run_mock.call_args_list[1].args[0],
            ["docker", "compose", "up", "-d", "--no-build", "--force-recreate", "--no-deps", "control-plane"],
        )
