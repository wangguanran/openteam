# Hub Runbook

## Initialize

```bash
openteam hub init
openteam hub up
openteam hub migrate
```

Default behavior:

- Postgres enabled and bound to `127.0.0.1`
- Redis enabled and bound to `127.0.0.1`

## Status and Logs

```bash
openteam hub status
openteam hub logs --tail 200
openteam hub logs --service postgres
```

## Expose to Other Nodes (High Risk)

```bash
openteam hub expose --bind-ip 10.0.0.10 --allow-cidrs "10.0.0.0/24" --open-redis
```

This command:

- requires approval
- updates `pg_hba.conf`
- updates compose bind addresses
- writes `~/.openteam/hub/FIREWALL_PLAN.md`

## Backup / Restore

```bash
openteam hub backup
openteam hub restore --file ~/.openteam/hub/backups/<file>.sql
```

Restore is high risk and approval-gated.

## Export / Push Config

```bash
openteam hub export-config --format env
openteam hub push-config --host 10.0.0.20 --user ubuntu --ssh-key ~/.ssh/id_ed25519
```

For password flow:

```bash
printf '%s' "$SSH_PASSWORD" | openteam hub push-config --host 10.0.0.20 --user ubuntu --password-stdin
```

## File Permissions

- `~/.openteam/hub/env/.env`: `0600`
- `~/.openteam/hub/*` directories: `0700`
