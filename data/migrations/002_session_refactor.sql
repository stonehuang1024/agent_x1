-- Session module refactor migration
-- Adds multi-agent fields, turn_contexts table, metadata to turns,
-- and schema_migrations tracking.

-- ============================================
-- Schema Migrations Tracking
-- ============================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL,
    description TEXT
);

-- ============================================
-- Sessions table: add new columns
-- ============================================

ALTER TABLE sessions ADD COLUMN agent_id TEXT DEFAULT NULL;
ALTER TABLE sessions ADD COLUMN session_type TEXT DEFAULT 'primary';
ALTER TABLE sessions ADD COLUMN transcript_path TEXT DEFAULT '';

-- ============================================
-- Turns table: add metadata column
-- ============================================

ALTER TABLE turns ADD COLUMN metadata TEXT DEFAULT '{}';

-- ============================================
-- Turn Contexts table
-- ============================================

CREATE TABLE IF NOT EXISTS turn_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,

    -- Config fields (frozen at turn start)
    working_dir TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.0,
    max_tokens INTEGER NOT NULL DEFAULT 0,
    tool_configs TEXT NOT NULL DEFAULT '[]',
    approval_policy TEXT NOT NULL DEFAULT '',
    behavior_settings TEXT NOT NULL DEFAULT '{}',

    -- Runtime fields (updated during/after turn)
    token_usage TEXT NOT NULL DEFAULT '{}',
    tool_call_records TEXT NOT NULL DEFAULT '[]',
    latency_stats TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at REAL NOT NULL,
    completed_at REAL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'completed', 'failed')),

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(session_id, turn_number)
);

CREATE INDEX IF NOT EXISTS idx_turn_contexts_session ON turn_contexts(session_id);
CREATE INDEX IF NOT EXISTS idx_turn_contexts_session_turn ON turn_contexts(session_id, turn_number);

-- Record this migration
INSERT INTO schema_migrations (version, applied_at, description)
VALUES (2, strftime('%s', 'now'), 'Session module refactor: multi-agent fields, turn_contexts, metadata');
