"""Config subcommand handlers."""
from __future__ import annotations

import argparse

from openteam_cli._shared import (
    _config_path,
    _default_workspace_root,
    _dump_toml,
    _load_config,
    _save_config,
    eprint,
)


def cmd_config_init(_args: argparse.Namespace) -> None:
    config_path = _config_path()
    if config_path.exists():
        eprint(f"config_exists={config_path}")
        return
    cfg = {
        "current_profile": "local",
        "workspace_root": str(_default_workspace_root()),
        "default_project_id": "openteam",
        "leader_only_writes": True,
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8787",
                # Prefer the real OpenTeam dev project by default; demos are opt-in.
                "default_project_id": "openteam",
            }
        },
    }
    _save_config(cfg)
    print(f"config_created={config_path}")


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
    print(_config_path())
    print(_dump_toml(cfg))
