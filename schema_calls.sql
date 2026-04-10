-- =====================================================================
-- Faibrick — bOnline call simulation schema
-- =====================================================================
-- Add-on to schema.sql. Safe to run alongside existing tables.
-- Adds:
--   calls            — fake call records (mirrors what bOnline would export)
--   call_embeddings  — vector(512) embeddings for call semantic search
--   search_calls()   — RPC helper for cosine similarity search
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =====================================================================
-- calls — phone system call log
-- =====================================================================

CREATE TABLE IF NOT EXISTS calls (
  id                 SERIAL PRIMARY KEY,
  call_id            TEXT UNIQUE,            -- bOnline-style id

  direction          TEXT,                   -- 'inbound' | 'outbound'
  from_number        TEXT,
  from_name          TEXT,                   -- caller id name if available
  to_number          TEXT,

  started_at         TIMESTAMPTZ,
  ended_at           TIMESTAMPTZ,
  duration_seconds   INTEGER,                -- total talk time
  ring_seconds       INTEGER,                -- how long it rang before answered / gave up

  state              TEXT,                   -- 'answered' | 'missed' | 'voicemail' | 'busy' | 'no_answer'
  answered           BOOLEAN,
  voicemail_left     BOOLEAN DEFAULT FALSE,
  after_hours        BOOLEAN DEFAULT FALSE,

  recording_url      TEXT,                   -- fake URL
  transcript         TEXT,                   -- dialogue or voicemail text
  summary            TEXT,                   -- 1-line Claude summary

  category           TEXT,
  priority           TEXT,                   -- 'high' | 'normal' | 'low'
  sentiment          TEXT,                   -- 'positive' | 'neutral' | 'negative'

  -- Callback tracking (for missed/voicemail only)
  is_returned        BOOLEAN DEFAULT FALSE,
  returned_at        TIMESTAMPTZ,

  patient_id         INTEGER,
  match_method       TEXT,                   -- 'mobile_phone' | 'home_phone' | 'work_phone' | 'none'

  agent_name         TEXT,                   -- receptionist who answered

  created_at         TIMESTAMPTZ,
  updated_at         TIMESTAMPTZ,
  synced_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_direction   ON calls(direction);
CREATE INDEX IF NOT EXISTS idx_calls_state       ON calls(state);
CREATE INDEX IF NOT EXISTS idx_calls_category    ON calls(category);
CREATE INDEX IF NOT EXISTS idx_calls_patient     ON calls(patient_id);
CREATE INDEX IF NOT EXISTS idx_calls_started_at  ON calls(started_at);
CREATE INDEX IF NOT EXISTS idx_calls_returned    ON calls(is_returned);

COMMENT ON TABLE calls IS 'Simulated bOnline phone system call log';
COMMENT ON COLUMN calls.category IS 'appointment_inquiry | treatment_inquiry | cancellation | reschedule | complaint | insurance | emergency | general_question | appointment_reminder | recall_call | collections_call | followup_call';

-- =====================================================================
-- call_embeddings
-- =====================================================================

CREATE TABLE IF NOT EXISTS call_embeddings (
  call_id       INTEGER PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
  summary_text  TEXT,
  embedding     vector(512),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_embeddings_vector
  ON call_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 10);

-- =====================================================================
-- search_calls RPC
-- =====================================================================

CREATE OR REPLACE FUNCTION search_calls(
  query_embedding vector(512),
  match_count int DEFAULT 20
)
RETURNS TABLE (
  call_id      int,
  summary      text,
  summary_text text,
  similarity   float
)
LANGUAGE sql STABLE AS $$
  SELECT
    ce.call_id,
    c.summary,
    ce.summary_text,
    1 - (ce.embedding <=> query_embedding) AS similarity
  FROM call_embeddings ce
  JOIN calls c ON c.id = ce.call_id
  ORDER BY ce.embedding <=> query_embedding
  LIMIT match_count;
$$;
