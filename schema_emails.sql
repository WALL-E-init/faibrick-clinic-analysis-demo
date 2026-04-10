-- =====================================================================
-- Faibrick — Email simulation schema (add-on to schema.sql)
-- =====================================================================
-- Safe to run on top of the existing schema. Does NOT touch existing tables.
-- Adds:
--   emails            — fake emails pulled from the clinic's mailbox
--   email_embeddings  — vector(512) embeddings for email semantic search
--   search_emails()   — RPC helper for cosine similarity search
-- =====================================================================

-- Vector extension already enabled by schema.sql, but safe to repeat
CREATE EXTENSION IF NOT EXISTS vector;

-- =====================================================================
-- emails — mirrors the subset of Microsoft Graph message fields we care about
-- =====================================================================

CREATE TABLE IF NOT EXISTS emails (
  id                 SERIAL PRIMARY KEY,
  message_id         TEXT UNIQUE,            -- MS Graph-style id
  thread_id          TEXT,                   -- groups replies together
  folder             TEXT,                   -- 'inbox' | 'sent'
  direction          TEXT,                   -- 'inbound' | 'outbound'

  from_address       TEXT,
  from_name          TEXT,
  to_address         TEXT,
  cc_addresses       JSONB,                  -- array of strings

  subject            TEXT,
  body_text          TEXT,
  body_preview       TEXT,                   -- first ~120 chars

  received_at        TIMESTAMPTZ,
  sent_at            TIMESTAMPTZ,

  is_read            BOOLEAN,
  is_replied         BOOLEAN,                -- did reception reply to this?
  replied_at         TIMESTAMPTZ,
  reply_to_message_id TEXT,                  -- the inbound this is a reply to

  category           TEXT,                   -- see categories list below
  priority           TEXT,                   -- 'high' | 'normal' | 'low'
  sentiment          TEXT,                   -- 'positive' | 'neutral' | 'negative'
  has_attachment     BOOLEAN DEFAULT FALSE,

  patient_id         INTEGER,                -- nullable FK to patients
  match_method       TEXT,                   -- 'email_address' | 'name' | 'none'

  created_at         TIMESTAMPTZ,
  updated_at         TIMESTAMPTZ,
  synced_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common filters
CREATE INDEX IF NOT EXISTS idx_emails_direction   ON emails(direction);
CREATE INDEX IF NOT EXISTS idx_emails_category    ON emails(category);
CREATE INDEX IF NOT EXISTS idx_emails_patient     ON emails(patient_id);
CREATE INDEX IF NOT EXISTS idx_emails_received_at ON emails(received_at);
CREATE INDEX IF NOT EXISTS idx_emails_replied     ON emails(is_replied);
CREATE INDEX IF NOT EXISTS idx_emails_thread      ON emails(thread_id);

COMMENT ON TABLE emails IS 'Simulated dental clinic mailbox (mirrors MS Graph schema subset)';
COMMENT ON COLUMN emails.category IS 'appointment_inquiry | treatment_inquiry | cancellation | reschedule | complaint | insurance | prescription | general_question | positive_feedback | appointment_confirmation | recall_reminder | invoice_reminder | treatment_followup | reply';

-- =====================================================================
-- email_embeddings — for semantic search over emails
-- =====================================================================

CREATE TABLE IF NOT EXISTS email_embeddings (
  email_id      INTEGER PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
  summary_text  TEXT,
  embedding     vector(512),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_embeddings_vector
  ON email_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 10);

-- =====================================================================
-- search_emails RPC
-- =====================================================================

CREATE OR REPLACE FUNCTION search_emails(
  query_embedding vector(512),
  match_count int DEFAULT 20
)
RETURNS TABLE (
  email_id     int,
  subject      text,
  summary_text text,
  similarity   float
)
LANGUAGE sql STABLE AS $$
  SELECT
    e.email_id,
    em.subject,
    e.summary_text,
    1 - (e.embedding <=> query_embedding) AS similarity
  FROM email_embeddings e
  JOIN emails em ON em.id = e.email_id
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
$$;
