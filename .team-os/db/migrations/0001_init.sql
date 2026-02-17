-- Team OS Postgres schema (v1)
--
-- Notes:
-- - Keep migrations idempotent (`IF NOT EXISTS`) where practical.
-- - Use TEXT ids (UUID generated in app) to avoid requiring extensions.
-- - Store most runtime timestamps as TEXT (ISO-8601) to keep parity with sqlite fallback.
-- - Cluster/approvals tables may use TIMESTAMPTZ for TTL semantics.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --- Runtime agents registry ---
CREATE TABLE IF NOT EXISTS agents (
  agent_id TEXT PRIMARY KEY,
  role_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  workstream_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at TEXT NOT NULL,
  last_heartbeat TEXT NOT NULL,
  current_action TEXT NOT NULL,
  instance_id TEXT NOT NULL DEFAULT '',
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_agents_project_id ON agents(project_id);
CREATE INDEX IF NOT EXISTS idx_agents_last_heartbeat ON agents(last_heartbeat);

-- --- Runtime runs ---
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  workstream_id TEXT NOT NULL,
  objective TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at TEXT NOT NULL,
  last_update TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_project_id ON runs(project_id);

-- --- Runtime events ---
CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY,
  ts TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  project_id TEXT NOT NULL,
  workstream_id TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_project_id ON events(project_id);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);

-- --- Nodes registry (cluster/local cache) ---
CREATE TABLE IF NOT EXISTS nodes (
  instance_id TEXT PRIMARY KEY,
  role_preference TEXT NOT NULL,
  base_url TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  capabilities_json TEXT NOT NULL,
  resources_json TEXT NOT NULL,
  agent_policy_json TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  llm_profile_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_nodes_heartbeat_at ON nodes(heartbeat_at);

-- --- Panel sync runs (GitHub Projects is a view-layer) ---
CREATE TABLE IF NOT EXISTS panel_sync_runs (
  id BIGSERIAL PRIMARY KEY,
  ts_start TEXT NOT NULL,
  ts_end TEXT NOT NULL,
  project_id TEXT NOT NULL,
  panel_type TEXT NOT NULL,
  mode TEXT NOT NULL,
  dry_run INTEGER NOT NULL,
  ok INTEGER NOT NULL,
  stats_json TEXT NOT NULL,
  error TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_panel_sync_runs_project_id ON panel_sync_runs(project_id);

-- --- Panel KV store ---
CREATE TABLE IF NOT EXISTS panel_kv (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Backward/forward compatibility: ensure new columns exist when upgrading an existing DB.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS capabilities_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE nodes ADD COLUMN IF NOT EXISTS llm_profile_json TEXT NOT NULL DEFAULT '{}';

-- --- Cluster leases (leader election) ---
CREATE TABLE IF NOT EXISTS cluster_leases (
  lease_name TEXT PRIMARY KEY,
  holder_instance_id TEXT NOT NULL DEFAULT '',
  holder_base_url TEXT NOT NULL DEFAULT '',
  holder_llm_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  lease_version BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cluster_leases_expires_at ON cluster_leases(expires_at);

-- --- Approvals (high-risk gates) ---
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL DEFAULT '',
  action_kind TEXT NOT NULL,
  action_summary TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  risk_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
  category TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  requested_by TEXT NOT NULL DEFAULT '',
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_by TEXT NOT NULL DEFAULT '',
  decided_at TIMESTAMPTZ NULL,
  decision_engine TEXT NOT NULL DEFAULT '',
  decision_note TEXT NOT NULL DEFAULT '',
  action_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_approvals_task_id ON approvals(task_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_requested_at ON approvals(requested_at);

-- --- Sync mapping (local id <-> remote id) ---
CREATE TABLE IF NOT EXISTS sync_mapping (
  mapping_key TEXT PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT '',
  project_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  local_id TEXT NOT NULL DEFAULT '',
  remote_id TEXT NOT NULL DEFAULT '',
  remote_url TEXT NOT NULL DEFAULT '',
  extra JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sync_mapping_scope_project ON sync_mapping(scope, project_id);

-- --- Self improve runs (dedupe + audit) ---
CREATE TABLE IF NOT EXISTS self_improve_runs (
  run_id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  instance_id TEXT NOT NULL DEFAULT '',
  is_leader BOOLEAN NOT NULL DEFAULT false,
  trigger TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  ok BOOLEAN NOT NULL DEFAULT false,
  applied_count INTEGER NOT NULL DEFAULT 0,
  dedupe_key TEXT NOT NULL DEFAULT '',
  proposal_path TEXT NOT NULL DEFAULT '',
  details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_self_improve_runs_ts ON self_improve_runs(ts);
