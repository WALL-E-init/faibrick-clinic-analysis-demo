-- =====================================================================
-- Faibrick — Dentally Simulation Schema
-- =====================================================================
-- Paste this entire file into Supabase SQL Editor and click RUN.
-- Safe to re-run (uses IF NOT EXISTS / DROP CASCADE blocks).
--
-- Tables: 21 Dentally tables + 1 embeddings table + 2 sync infra tables
-- Based on: 2026-04-08-Dentally-Data-Pull-Architecture.md
-- =====================================================================

-- Enable pgvector extension (for semantic search)
CREATE EXTENSION IF NOT EXISTS vector;

-- =====================================================================
-- LAYER 0: Sync Infrastructure
-- =====================================================================

CREATE TABLE IF NOT EXISTS sync_log (
  id               SERIAL PRIMARY KEY,
  sync_type        TEXT NOT NULL,
  endpoint         TEXT NOT NULL,
  started_at       TIMESTAMPTZ NOT NULL,
  finished_at      TIMESTAMPTZ,
  records_fetched  INTEGER DEFAULT 0,
  status           TEXT DEFAULT 'running',
  error_message    TEXT,
  last_updated_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sync_state (
  endpoint                 TEXT PRIMARY KEY,
  last_synced_at           TIMESTAMPTZ NOT NULL,
  last_record_updated_at   TIMESTAMPTZ,
  total_records            INTEGER DEFAULT 0
);

-- =====================================================================
-- LAYER 1: Practice Setup
-- =====================================================================

CREATE TABLE IF NOT EXISTS practice (
  id               INTEGER PRIMARY KEY,
  name             TEXT,
  address_line_1   TEXT,
  address_line_2   TEXT,
  postcode         TEXT,
  town             TEXT,
  email_address    TEXT,
  phone_number     TEXT,
  website          TEXT,
  nhs              BOOLEAN,
  time_zone        TEXT,
  opening_hours    JSONB,
  synced_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sites (
  id               INTEGER PRIMARY KEY,
  active           BOOLEAN,
  name             TEXT,
  nickname         TEXT,
  address_line_1   TEXT,
  postcode         TEXT,
  town             TEXT,
  phone_number     TEXT,
  email_address    TEXT,
  practice_id      INTEGER,
  opening_hours    JSONB,
  synced_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rooms (
  id               INTEGER PRIMARY KEY,
  name             TEXT,
  site_id          INTEGER,
  created_at       TIMESTAMPTZ,
  updated_at       TIMESTAMPTZ,
  synced_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS practitioners (
  id                   INTEGER PRIMARY KEY,
  active               BOOLEAN,
  gdc_number           TEXT,
  nhs_number           TEXT,
  site_id              INTEGER,
  default_contract_id  INTEGER,
  user_id              INTEGER,
  user_first_name      TEXT,
  user_last_name       TEXT,
  user_email           TEXT,
  user_role            TEXT,
  created_at           TIMESTAMPTZ,
  updated_at           TIMESTAMPTZ,
  synced_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payment_plans (
  id                           INTEGER PRIMARY KEY,
  name                         TEXT,
  active                       BOOLEAN,
  dentist_recall_interval      INTEGER,
  hygienist_recall_interval    INTEGER,
  exam_duration                INTEGER,
  scale_and_polish_duration    INTEGER,
  site_id                      INTEGER,
  created_at                   TIMESTAMPTZ,
  synced_at                    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS treatments (
  id                     INTEGER PRIMARY KEY,
  active                 BOOLEAN,
  code                   TEXT,
  nomenclature           TEXT,
  patient_nomenclature   TEXT,
  description            TEXT,
  region                 TEXT,
  nhs_treatment_cat      TEXT,
  treatment_category_id  INTEGER,
  created_at             TIMESTAMPTZ,
  updated_at             TIMESTAMPTZ,
  synced_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS appointment_cancellation_reasons (
  id               INTEGER PRIMARY KEY,
  reason           TEXT,
  reason_type      TEXT,
  archived         BOOLEAN,
  created_at       TIMESTAMPTZ,
  updated_at       TIMESTAMPTZ,
  synced_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS acquisition_sources (
  id               INTEGER PRIMARY KEY,
  name             TEXT,
  active           BOOLEAN,
  notes            TEXT,
  synced_at        TIMESTAMPTZ DEFAULT NOW()
);

-- =====================================================================
-- LAYER 2: Core Patient & Clinical Data
-- =====================================================================

CREATE TABLE IF NOT EXISTS patients (
  id                          INTEGER PRIMARY KEY,
  title                       TEXT,
  first_name                  TEXT,
  last_name                   TEXT,
  preferred_name              TEXT,
  date_of_birth               DATE,
  gender                      TEXT,
  email_address               TEXT,
  home_phone                  TEXT,
  mobile_phone                TEXT,
  work_phone                  TEXT,
  preferred_phone_number      INTEGER,
  address_line_1              TEXT,
  address_line_2              TEXT,
  town                        TEXT,
  county                      TEXT,
  postcode                    TEXT,
  dentist_id                  INTEGER,
  hygienist_id                INTEGER,
  dentist_recall_date         DATE,
  dentist_recall_interval     INTEGER,
  hygienist_recall_date       DATE,
  hygienist_recall_interval   INTEGER,
  medical_alert               BOOLEAN,
  medical_alert_text          TEXT,
  payment_plan_id             INTEGER,
  acquisition_source_id       INTEGER,
  marketing                   BOOLEAN,
  use_email                   BOOLEAN,
  use_sms                     BOOLEAN,
  recall_method               TEXT,
  active                      BOOLEAN,
  archived_reason             TEXT,
  site_id                     INTEGER,
  nhs_number                  TEXT,
  emergency_contact_name      TEXT,
  emergency_contact_phone     TEXT,
  created_at                  TIMESTAMPTZ,
  updated_at                  TIMESTAMPTZ,
  synced_at                   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_patients_active ON patients(active);
CREATE INDEX IF NOT EXISTS idx_patients_dentist ON patients(dentist_id);
CREATE INDEX IF NOT EXISTS idx_patients_last_name ON patients(last_name);

CREATE TABLE IF NOT EXISTS patient_stats (
  patient_id                        INTEGER PRIMARY KEY,
  first_appointment_date            DATE,
  first_exam_date                   DATE,
  last_appointment_date             DATE,
  last_exam_date                    DATE,
  last_scale_and_polish_date        DATE,
  last_cancelled_appointment_date   DATE,
  last_fta_appointment_date         DATE,
  next_appointment_date             DATE,
  next_exam_date                    DATE,
  next_scale_and_polish_date        DATE,
  total_invoiced                    DECIMAL(10,2),
  total_paid                        DECIMAL(10,2),
  created_at                        TIMESTAMPTZ,
  updated_at                        TIMESTAMPTZ,
  synced_at                         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_patient_stats_next_appt ON patient_stats(next_appointment_date);

CREATE TABLE IF NOT EXISTS appointments (
  id                                  INTEGER PRIMARY KEY,
  patient_id                          INTEGER,
  practitioner_id                     INTEGER,
  room_id                             INTEGER,
  reason                              TEXT,
  state                               TEXT,
  duration                            INTEGER,
  start_time                          TIMESTAMPTZ,
  finish_time                         TIMESTAMPTZ,
  notes                               TEXT,
  pending_at                          TIMESTAMPTZ,
  confirmed_at                        TIMESTAMPTZ,
  arrived_at                          TIMESTAMPTZ,
  in_surgery_at                       TIMESTAMPTZ,
  completed_at                        TIMESTAMPTZ,
  cancelled_at                        TIMESTAMPTZ,
  did_not_attend_at                   TIMESTAMPTZ,
  appointment_cancellation_reason_id  INTEGER,
  payment_plan_id                     INTEGER,
  site_id                             INTEGER,
  created_at                          TIMESTAMPTZ,
  updated_at                          TIMESTAMPTZ,
  synced_at                           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_appointments_patient ON appointments(patient_id);
CREATE INDEX IF NOT EXISTS idx_appointments_state ON appointments(state);
CREATE INDEX IF NOT EXISTS idx_appointments_start_time ON appointments(start_time);

CREATE TABLE IF NOT EXISTS treatment_plans (
  id                        INTEGER PRIMARY KEY,
  patient_id                INTEGER,
  practitioner_id           INTEGER,
  completed                 BOOLEAN,
  completed_at              TIMESTAMPTZ,
  start_date                DATE,
  end_date                  DATE,
  last_completed_at         TIMESTAMPTZ,
  nickname                  TEXT,
  nhs_uda_value             DECIMAL(10,2),
  nhs_completed_uda_value   DECIMAL(10,2),
  private_treatment_value   DECIMAL(10,2),
  created_at                TIMESTAMPTZ,
  updated_at                TIMESTAMPTZ,
  synced_at                 TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_treatment_plans_patient ON treatment_plans(patient_id);
CREATE INDEX IF NOT EXISTS idx_treatment_plans_completed ON treatment_plans(completed);

CREATE TABLE IF NOT EXISTS treatment_plan_items (
  id                     INTEGER PRIMARY KEY,
  patient_id             INTEGER,
  practitioner_id        INTEGER,
  treatment_plan_id      INTEGER,
  treatment_id           INTEGER,
  nomenclature           TEXT,
  code                   TEXT,
  notes                  TEXT,
  duration               INTEGER,
  price                  DECIMAL(10,2),
  region                 TEXT,
  teeth                  JSONB,
  surfaces               JSONB,
  completed              BOOLEAN,
  completed_at           TIMESTAMPTZ,
  charged                BOOLEAN,
  appear_on_invoice      BOOLEAN,
  invoice_id             INTEGER,
  payment_plan_id        INTEGER,
  created_at             TIMESTAMPTZ,
  updated_at             TIMESTAMPTZ,
  synced_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tp_items_plan ON treatment_plan_items(treatment_plan_id);
CREATE INDEX IF NOT EXISTS idx_tp_items_patient ON treatment_plan_items(patient_id);
CREATE INDEX IF NOT EXISTS idx_tp_items_completed ON treatment_plan_items(completed);

CREATE TABLE IF NOT EXISTS recalls (
  id                       INTEGER PRIMARY KEY,
  patient_id               INTEGER,
  due_date                 DATE,
  recall_type              TEXT,
  recall_method            TEXT,
  status                   TEXT,
  prebooked                BOOLEAN,
  times_contacted          INTEGER,
  first_reminder_sent_at   TIMESTAMPTZ,
  last_reminded_at         TIMESTAMPTZ,
  appointment_id           INTEGER,
  created_at               TIMESTAMPTZ,
  updated_at               TIMESTAMPTZ,
  synced_at                TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recalls_patient ON recalls(patient_id);
CREATE INDEX IF NOT EXISTS idx_recalls_status ON recalls(status);
CREATE INDEX IF NOT EXISTS idx_recalls_due_date ON recalls(due_date);

-- =====================================================================
-- LAYER 3: Financial Data
-- =====================================================================

CREATE TABLE IF NOT EXISTS accounts (
  id                                INTEGER PRIMARY KEY,
  patient_id                        INTEGER,
  patient_name                      TEXT,
  current_balance                   DECIMAL(10,2),
  opening_balance                   DECIMAL(10,2),
  planned_nhs_treatment_value       DECIMAL(10,2),
  planned_private_treatment_value   DECIMAL(10,2),
  synced_at                         TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS invoices (
  id                    INTEGER PRIMARY KEY,
  patient_id            INTEGER,
  account_id            INTEGER,
  site_id               INTEGER,
  amount                DECIMAL(10,2),
  amount_outstanding    DECIMAL(10,2),
  dated_on              DATE,
  due_on                DATE,
  paid                  BOOLEAN,
  paid_on               DATE,
  reference             TEXT,
  status                TEXT,
  nhs_amount            DECIMAL(10,2),
  sent_at               TIMESTAMPTZ,
  created_at            TIMESTAMPTZ,
  updated_at            TIMESTAMPTZ,
  synced_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoices_patient ON invoices(patient_id);
CREATE INDEX IF NOT EXISTS idx_invoices_paid ON invoices(paid);

CREATE TABLE IF NOT EXISTS invoice_items (
  id                        INTEGER PRIMARY KEY,
  invoice_id                INTEGER,
  practitioner_id           INTEGER,
  name                      TEXT,
  item_price                DECIMAL(10,2),
  total_price               DECIMAL(10,2),
  quantity                  INTEGER,
  nhs_charge                DECIMAL(10,2),
  treatment_plan_id         INTEGER,
  treatment_plan_item_id    INTEGER,
  created_at                TIMESTAMPTZ,
  updated_at                TIMESTAMPTZ,
  synced_at                 TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice ON invoice_items(invoice_id);

CREATE TABLE IF NOT EXISTS payments (
  id                    INTEGER PRIMARY KEY,
  patient_id            INTEGER,
  account_id            INTEGER,
  practitioner_id       INTEGER,
  site_id               INTEGER,
  amount                DECIMAL(10,2),
  amount_unexplained    DECIMAL(10,2),
  dated_on              DATE,
  method                TEXT,
  status                TEXT,
  fully_explained       BOOLEAN,
  payment_plan_id       INTEGER,
  created_at            TIMESTAMPTZ,
  updated_at            TIMESTAMPTZ,
  synced_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_patient ON payments(patient_id);

CREATE TABLE IF NOT EXISTS nhs_claims (
  id                     INTEGER PRIMARY KEY,
  patient_id             INTEGER,
  practitioner_id        INTEGER,
  treatment_plan_id      INTEGER,
  contract_id            INTEGER,
  site_id                INTEGER,
  claim_status           TEXT,
  expected_uda           DECIMAL(10,2),
  awarded_uda            DECIMAL(10,2),
  uda_band               TEXT,
  submitted_date         DATE,
  approval_date          DATE,
  patient_charge         DECIMAL(10,2),
  dentist_charge         DECIMAL(10,2),
  created_at             TIMESTAMPTZ,
  updated_at             TIMESTAMPTZ,
  synced_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nhs_claims_status ON nhs_claims(claim_status);

-- =====================================================================
-- LAYER 4: Vector Embeddings (semantic search)
-- =====================================================================
-- One row per patient with a 512-dim embedding (Voyage AI voyage-3-lite)
-- summarizing their key clinical/financial context.

CREATE TABLE IF NOT EXISTS patient_embeddings (
  patient_id      INTEGER PRIMARY KEY,
  summary_text    TEXT,                -- the text that was embedded
  embedding       vector(512),         -- voyage-3-lite dimension
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- IVFFlat index for fast similarity search
-- (Use AFTER data is loaded, not before — needs data to train)
CREATE INDEX IF NOT EXISTS idx_patient_embeddings_vector
  ON patient_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 10);

-- =====================================================================
-- Helper function for semantic search
-- =====================================================================

CREATE OR REPLACE FUNCTION search_patients(
  query_embedding vector(512),
  match_count int DEFAULT 10
)
RETURNS TABLE (
  patient_id int,
  summary_text text,
  similarity float
)
LANGUAGE sql STABLE AS $$
  SELECT
    patient_id,
    summary_text,
    1 - (embedding <=> query_embedding) AS similarity
  FROM patient_embeddings
  ORDER BY embedding <=> query_embedding
  LIMIT match_count;
$$;
