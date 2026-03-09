-- Team OS Postgres schema (v3): distributed task leases for runtime workers

CREATE TABLE IF NOT EXISTS task_leases (
  lease_scope TEXT NOT NULL,
  lease_key TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT '',
  task_id TEXT NOT NULL DEFAULT '',
  holder_instance_id TEXT NOT NULL DEFAULT '',
  holder_actor TEXT NOT NULL DEFAULT '',
  holder_meta_json TEXT NOT NULL DEFAULT '{}',
  lease_ttl_sec INTEGER NOT NULL DEFAULT 600,
  lease_acquired_at TEXT NOT NULL,
  lease_heartbeat_at TEXT NOT NULL,
  lease_expires_at TEXT NOT NULL,
  lease_version BIGINT NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_leases_scope_expires ON task_leases(lease_scope, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_task_leases_holder ON task_leases(holder_instance_id, lease_expires_at);
