-- Professional memory schema upgrade (non-breaking, incremental).
-- Safe for existing databases: only CREATE/ALTER IF NOT EXISTS and backfill updates.

-- 1) chat_session_summaries: add hierarchy + quality metadata.
ALTER TABLE chat_session_summaries
    ADD COLUMN IF NOT EXISTS summary_kind TEXT NOT NULL DEFAULT 'rolling',
    ADD COLUMN IF NOT EXISTS parent_summary_ids BIGINT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS quality_score NUMERIC(5,4);

UPDATE chat_session_summaries
SET summary_kind = 'rolling'
WHERE summary_kind IS NULL OR summary_kind = '';

CREATE INDEX IF NOT EXISTS idx_chat_session_summaries_kind_status_created
    ON chat_session_summaries(session_id, summary_kind, status, created_at DESC);

-- 2) chat_memory_facts: add lifecycle + dedup hash for stronger constraints.
ALTER TABLE chat_memory_facts
    ADD COLUMN IF NOT EXISTS normalized_fact_hash TEXT,
    ADD COLUMN IF NOT EXISTS last_used_at TEXT,
    ADD COLUMN IF NOT EXISTS expires_at TEXT;

UPDATE chat_memory_facts
SET normalized_fact_hash = md5(lower(trim(fact_type || '|' || fact_value)))
WHERE normalized_fact_hash IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_memory_facts_session_hash_active
    ON chat_memory_facts(COALESCE(session_id, '__global__'), normalized_fact_hash)
    WHERE status = 'active' AND normalized_fact_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chat_memory_facts_session_status_priority_updated
    ON chat_memory_facts(session_id, status, priority DESC, updated_at DESC);

-- 3) chat_context_injections: retrieval observability for replay/debug.
ALTER TABLE chat_context_injections
    ADD COLUMN IF NOT EXISTS query_text TEXT,
    ADD COLUMN IF NOT EXISTS retrieval_strategy TEXT NOT NULL DEFAULT 'priority_topk',
    ADD COLUMN IF NOT EXISTS dropped_item_ids BIGINT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_chat_context_injections_session_created_desc
    ON chat_context_injections(session_id, created_at DESC);

-- 4) Strong decisions table (separate from facts).
CREATE TABLE IF NOT EXISTS chat_decisions (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
    decision_key TEXT NOT NULL,
    decision_value TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    source_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_decisions_session_key_active
    ON chat_decisions(COALESCE(session_id, '__global__'), decision_key)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_chat_decisions_session_status_updated
    ON chat_decisions(session_id, status, updated_at DESC);

-- 5) Optional TIMESTAMPTZ bridge columns for gradual migration from TEXT timestamps.
ALTER TABLE chat_session_summaries
    ADD COLUMN IF NOT EXISTS created_at_ts TIMESTAMPTZ;
ALTER TABLE chat_memory_facts
    ADD COLUMN IF NOT EXISTS created_at_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS effective_from_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS effective_to_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expires_at_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_used_at_ts TIMESTAMPTZ;
ALTER TABLE chat_context_injections
    ADD COLUMN IF NOT EXISTS created_at_ts TIMESTAMPTZ;
ALTER TABLE chat_decisions
    ADD COLUMN IF NOT EXISTS created_at_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at_ts TIMESTAMPTZ;

UPDATE chat_session_summaries
SET created_at_ts = NULLIF(created_at, '')::timestamptz
WHERE created_at_ts IS NULL AND created_at IS NOT NULL AND created_at <> '';

UPDATE chat_memory_facts
SET
    created_at_ts = COALESCE(created_at_ts, NULLIF(created_at, '')::timestamptz),
    updated_at_ts = COALESCE(updated_at_ts, NULLIF(updated_at, '')::timestamptz),
    effective_from_ts = COALESCE(effective_from_ts, NULLIF(effective_from, '')::timestamptz),
    effective_to_ts = COALESCE(effective_to_ts, NULLIF(effective_to, '')::timestamptz),
    expires_at_ts = COALESCE(expires_at_ts, NULLIF(expires_at, '')::timestamptz),
    last_used_at_ts = COALESCE(last_used_at_ts, NULLIF(last_used_at, '')::timestamptz)
WHERE TRUE;

UPDATE chat_context_injections
SET created_at_ts = NULLIF(created_at, '')::timestamptz
WHERE created_at_ts IS NULL AND created_at IS NOT NULL AND created_at <> '';

UPDATE chat_decisions
SET
    created_at_ts = COALESCE(created_at_ts, NULLIF(created_at, '')::timestamptz),
    updated_at_ts = COALESCE(updated_at_ts, NULLIF(updated_at, '')::timestamptz)
WHERE TRUE;
