-- Context memory layering for long conversations:
-- 1) raw chat messages (existing chat_messages)
-- 2) rolling summaries (chat_session_summaries)
-- 3) stable facts (chat_memory_facts)
-- 4) replay/injection audit (chat_context_injections)

CREATE TABLE IF NOT EXISTS chat_session_summaries (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    summary_level INTEGER NOT NULL DEFAULT 1,
    summary_text TEXT NOT NULL,
    summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_from_message_created_at TEXT,
    source_to_message_created_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    approx_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    superseded_by_id BIGINT REFERENCES chat_session_summaries(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_session_created
    ON chat_session_summaries(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_session_level_status
    ON chat_session_summaries(session_id, summary_level, status);

CREATE TABLE IF NOT EXISTS chat_memory_facts (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
    fact_type TEXT NOT NULL DEFAULT 'constraint',
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    fact_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0.7000,
    priority INTEGER NOT NULL DEFAULT 50,
    source_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_memory_facts_scope_key_active
    ON chat_memory_facts(COALESCE(session_id, '__global__'), fact_key, status)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_chat_memory_facts_session_status_priority
    ON chat_memory_facts(session_id, status, priority DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_memory_facts_type_status
    ON chat_memory_facts(fact_type, status);

CREATE TABLE IF NOT EXISTS chat_context_injections (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    request_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
    summary_ids BIGINT[] NOT NULL DEFAULT '{}',
    fact_ids BIGINT[] NOT NULL DEFAULT '{}',
    prompt_budget_tokens INTEGER NOT NULL DEFAULT 0,
    used_tokens INTEGER NOT NULL DEFAULT 0,
    overflow_strategy TEXT NOT NULL DEFAULT 'drop_oldest_summary',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_context_injections_session_created
    ON chat_context_injections(session_id, created_at DESC);
