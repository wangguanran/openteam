-- Team OS Postgres schema (v2): hub/locks/approval execution audit

CREATE TABLE IF NOT EXISTS approval_executions (
  execution_id TEXT PRIMARY KEY,
  approval_id TEXT NOT NULL,
  execution_status TEXT NOT NULL,
  executor TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  detail JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_approval_executions_approval_id ON approval_executions(approval_id);
CREATE INDEX IF NOT EXISTS idx_approval_executions_created_at ON approval_executions(created_at);

CREATE TABLE IF NOT EXISTS locks (
  lock_key TEXT PRIMARY KEY,
  backend TEXT NOT NULL DEFAULT 'db_advisory',
  holder JSONB NOT NULL DEFAULT '{}'::jsonb,
  lease_ttl_sec INTEGER NOT NULL DEFAULT 120,
  acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  state TEXT NOT NULL DEFAULT 'HELD'
);
CREATE INDEX IF NOT EXISTS idx_locks_expires_at ON locks(expires_at);

CREATE TABLE IF NOT EXISTS installer_runs (
  run_id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  instance_id TEXT NOT NULL DEFAULT '',
  target_host TEXT NOT NULL DEFAULT '',
  ok BOOLEAN NOT NULL DEFAULT false,
  category TEXT NOT NULL DEFAULT '',
  detail JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS installer_knowledge (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW migrations_version AS
SELECT COALESCE(MAX(version), '0000') AS version FROM schema_migrations;

CREATE OR REPLACE VIEW hub_state AS
SELECT
  now() AS ts,
  (SELECT COALESCE(MAX(version), '0000') FROM schema_migrations) AS schema_version,
  (SELECT COUNT(1) FROM approvals WHERE status='REQUESTED') AS approvals_pending,
  (SELECT COUNT(1) FROM locks WHERE state='HELD') AS locks_held;
