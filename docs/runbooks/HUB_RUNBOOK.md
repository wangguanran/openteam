# Hub Runbook

## Initialize

```bash
teamos hub init
teamos hub up
teamos hub migrate
```

Default behavior:

- Postgres enabled and bound to `127.0.0.1`
- Redis enabled and bound to `127.0.0.1`

## Status and Logs

```bash
teamos hub status
teamos hub logs --tail 200
teamos hub logs --service postgres
```

## Expose to Other Nodes (High Risk)

```bash
teamos hub expose --bind-ip 10.0.0.10 --allow-cidrs "10.0.0.0/24" --open-redis
```

This command:

- requires approval
- updates `pg_hba.conf`
- updates compose bind addresses
- writes `~/.teamos/hub/FIREWALL_PLAN.md`

## Backup / Restore

```bash
teamos hub backup
teamos hub restore --file ~/.teamos/hub/backups/<file>.sql
```

Restore is high risk and approval-gated.

## Export / Push Config

```bash
teamos hub export-config --format env
teamos hub push-config --host 10.0.0.20 --user ubuntu --ssh-key ~/.ssh/id_ed25519
```

For password flow:

```bash
printf '%s' "$SSH_PASSWORD" | teamos hub push-config --host 10.0.0.20 --user ubuntu --password-stdin
```

## File Permissions

- `~/.teamos/hub/env/.env`: `0600`
- `~/.teamos/hub/*` directories: `0700`
