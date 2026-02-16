# Evals (Regression Checks)

This directory contains regression checks for the Team OS "execution strategy" and the always-on self-improve loop.

## Run

```bash
cd /Users/wangguanran/OpenTeam/team-os
bash evals/smoke_self_improve.sh
```

## Policy

- Evals must be safe by default.
- Do not perform remote writes (GitHub Issues/Projects/repo creation) unless explicitly enabled by env and approved.
- Evals should be runnable on a fresh machine as much as possible (prefer stdlib + `unittest`).

