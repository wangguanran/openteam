#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess

from _common import PipelineError, add_default_args, resolve_repo_root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bootstrap a remote Team-OS node via SSH script")
    add_default_args(ap)
    ap.add_argument("--host", required=True)
    ap.add_argument("--user", required=True)
    ap.add_argument("--cluster-repo", required=True)
    ap.add_argument("--brain-base-url", required=True)
    ap.add_argument("--role", default="auto")
    ap.add_argument("--capabilities", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--ssh-key", default="")
    ap.add_argument("--password-stdin", action="store_true")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    repo = resolve_repo_root(args)
    script = repo / "scripts" / "cluster" / "bootstrap_remote_node.sh"
    if not script.exists():
        raise PipelineError(f"missing script: {script}")

    cmd = [
        "bash",
        str(script),
        "--host",
        str(args.host),
        "--user",
        str(args.user),
        "--cluster-repo",
        str(args.cluster_repo),
        "--brain-base-url",
        str(args.brain_base_url),
        "--role",
        str(args.role),
        "--capabilities",
        str(args.capabilities),
        "--tags",
        str(args.tags),
    ]
    if str(args.ssh_key or "").strip():
        cmd += ["--ssh-key", str(args.ssh_key).strip()]
    if bool(args.password_stdin):
        cmd.append("--password-stdin")
    if bool(args.execute):
        cmd.append("--execute")

    stdin_data = None
    env = dict(os.environ)
    if bool(args.password_stdin):
        pw = input()
        if not str(pw or "").strip():
            raise PipelineError("--password-stdin set but stdin password is empty")
        stdin_data = str(pw).rstrip("\r\n") + "\n"
        env["OPENTEAM_SSH_PASSWORD"] = str(pw).rstrip("\r\n")

    p = subprocess.run(cmd, cwd=str(repo), input=stdin_data, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    out = {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": (p.stdout or "")[-2000:], "stderr": (p.stderr or "")[-2000:], "cmd": cmd}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if p.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
