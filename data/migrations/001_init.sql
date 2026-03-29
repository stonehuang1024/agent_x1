-- Agent X1 Database Schema Initialization
-- Creates tables for Session, Memory, and related entities

-- Enable foreign keys
PRAGMA foreign_keys = ON;

-- ============================================
-- Session Management Tables
-- ============================================

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    name TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'created','active','paused','compacting','completed','failed','archived','forked'
    )),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    ended_at REAL,
    config_snapshot TEXT NOT NULL DEFAULT '{}',
    token_budget_total INTEGER NOT NULL DEFAULT 128000,
    token_budget_reserved INTEGER NOT NULL DEFAULT 8192,
    token_budget_used INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    total_duration_ms REAL NOT NULL DEFAULT 0.0,
    llm_call_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    working_dir TEXT NOT NULL DEFAULT '',
    session_dir TEXT NOT NULL DEFAULT '',
    
    FOREIGN KEY (parent_id) REFERENCES sessions(id) ON DELETE SET NULL
);

-- Session indexes
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);

-- Conversation turns
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    token_count INTEGER NOT NULL DEFAULT 0,
    importance REAL NOT NULL DEFAULT 0.5 CHECK(importance >= 0 AND importance <= 1),
    latency_ms REAL NOT NULL DEFAULT 0.0,
    created_at REAL NOT NULL,
    
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(session_id, turn_number)
);

-- Turn indexes
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_turns_session_number ON turns(session_id, turn_number);

-- Session checkpoints for fork/restore
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT,
    turn_number INTEGER NOT NULL,
    messages_snapshot TEXT NOT NULL,
    budget_total INTEGER DEFAULT 128000,
    budget_reserved INTEGER DEFAULT 8192,
    budget_used INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id);

-- ============================================
-- Memory System Tables
-- ============================================

-- Episodic memory (session-scoped events)
CREATE TABLE IF NOT EXISTS episodic_memory (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    type TEXT CHECK(type IN ('decision','action','outcome','error','note','insight')),
    content TEXT NOT NULL,
    importance REAL DEFAULT 0.5 CHECK(importance >= 0 AND importance <= 1),
    access_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    last_accessed REAL NOT NULL,
    turn_number INTEGER,
    context_json TEXT DEFAULT '{}'
);

-- Episodic memory indexes
CREATE INDEX IF NOT EXISTS idx_episodic_session ON episodic_memory(session_id);
CREATE INDEX IF NOT EXISTS idx_episodic_type ON episodic_memory(type);
CREATE INDEX IF NOT EXISTS idx_episodic_importance ON episodic_memory(importance DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_created ON episodic_memory(created_at DESC);

-- Semantic memory (long-term facts/preferences)
CREATE TABLE IF NOT EXISTS semantic_memory (
    id TEXT PRIMARY KEY,
    category TEXT CHECK(category IN ('preference','convention','fact','pattern','procedure')),
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.8 CHECK(confidence >= 0 AND confidence <= 1),
    verification_count INTEGER DEFAULT 0,
    source_session TEXT,
    source_turn INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Semantic memory indexes
CREATE INDEX IF NOT EXISTS idx_semantic_category ON semantic_memory(category);
CREATE INDEX IF NOT EXISTS idx_semantic_key ON semantic_memory(key);
CREATE INDEX IF NOT EXISTS idx_semantic_updated ON semantic_memory(updated_at DESC);

-- Full-text search for memory content (optional, for advanced retrieval)
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content,
    tokenize='porter'
);

-- ============================================
-- Views for convenient querying
-- ============================================

-- Session statistics view
CREATE VIEW IF NOT EXISTS session_stats AS
SELECT 
    s.id,
    s.name,
    s.status,
    s.turn_count,
    s.token_budget_used,
    s.token_budget_total,
    ROUND(CAST(s.token_budget_used AS REAL) / NULLIF(s.token_budget_total, 0), 4) as token_utilization,
    s.created_at,
    s.updated_at,
    CASE 
        WHEN s.ended_at IS NOT NULL THEN s.ended_at - s.created_at
        ELSE (julianday('now') - 2440587.5) * 86400.0 - s.created_at
    END as duration_seconds
FROM sessions s
WHERE s.status != 'archived';

-- Recent high-importance memories view
CREATE VIEW IF NOT EXISTS important_memories AS
SELECT 
    id,
    session_id,
    type,
    content,
    importance,
    created_at
FROM episodic_memory
WHERE importance >= 0.7
ORDER BY created_at DESC;
