"""Config subcommand handlers."""
from __future__ import annotations

import argparse

from team_os_cli._shared import (
    CONFIG_PATH,
    DEFAULT_WORKSPACE_ROOT,
    _dump_toml,
    _load_config,
    _save_config,
    eprint,
)


def cmd_config_init(_args: argparse.Namespace) -> None:
    if CONFIG_PATH.exists():
        eprint(f"config_exists={CONFIG_PATH}")
        return
    cfg = {
        "current_profile": "local",
        "workspace_root": str(DEFAULT_WORKSPACE_ROOT),
        "default_project_id": "teamos",
        "leader_only_writes": True,
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8787",
                # Prefer the real Team OS dev project by default; demos are opt-in.
                "default_project_id": "teamos",
            }
        },
    }
    _save_config(cfg)
    print(f"config_created={CONFIG_PATH}")


def cmd_config_add_profile(args: argparse.Namespace) -> None:
    cfg = _load_config()
    profiles = cfg.get("profiles", {}) or {}
    profiles[args.name] = {"base_url": args.base_url, "default_project_id": args.default_project_id or ""}
    cfg["profiles"] = profiles
    if not cfg.get("current_profile"):
        cfg["current_profile"] = args.name
    _save_config(cfg)
    print(f"profile_added={args.name}")


def cmd_config_use(args: argparse.Namespace) -> None:
    cfg = _load_config()
    profiles = cfg.get("profiles", {}) or {}
    if args.name not in profiles:
        raise RuntimeError(f"Unknown profile: {args.name}")
    cfg["current_profile"] = args.name
    _save_config(cfg)
    print(f"profile_in_use={args.name}")


def cmd_config_show(_args: argparse.Namespace) -> None:
    cfg = _load_config()
    print(CONFIG_PATH)
    print(_dump_toml(cfg))
