# Clinic_Analysis — Faibrick local simulation

Simulated Dentally dataset + vector search + lost revenue UI. Runs against a
Supabase cloud project (no Docker). Uses fake data so no GDPR concerns.

## Setup (once)

### 1. Install Python packages

```bash
cd W:\projects\faibrick\Clinic_Analysis
pip install -r requirements.txt
```

### 2. Configure `.env`

Already done — file exists at `Clinic_Analysis/.env` with Supabase, Voyage AI,
and Anthropic credentials. It is gitignored and will never be committed.

### 3. Apply the SQL schema to Supabase

1. Open your Supabase dashboard → SQL Editor
2. Open `schema.sql` from this folder
3. Copy everything, paste into the SQL Editor, click **Run**
4. You should see "Success. No rows returned." — all 22 tables are now created,
   and the `pgvector` extension is enabled.

## Running the pipeline

### First time — clinic data

```bash
# Step 1 — apply schema.sql in Supabase SQL Editor (one time)

# Step 2 — generate fake clinic data (~20 seconds)
python generate_data.py

# Step 3 — build patient vector embeddings (~30 seconds)
python embed.py
```

### Adding emails (optional but recommended)

```bash
# Step 4 — apply schema_emails.sql in Supabase SQL Editor (one time)

# Step 5 — generate 2000 fake emails with Claude Haiku (~5-15 minutes)
python generate_emails.py
#   --count 500       # smaller for testing
#   --no-wipe         # append instead of wiping

# Step 6 — build email vector embeddings (~30 seconds)
python embed_emails.py
```

### Adding phone calls (optional)

```bash
# Step 7 — apply schema_calls.sql in Supabase SQL Editor (one time)

# Step 8 — generate 2000 fake bOnline calls with Claude Haiku (~5-15 minutes)
python generate_calls.py
#   --count 500       # smaller for testing
#   --no-wipe

# Step 9 — build call vector embeddings (~30 seconds)
python embed_calls.py
```

### Launch the UI

```bash
streamlit run app.py
```

The UI opens at http://localhost:8501.

## What's in the UI

- **👥 Patients** — filter by dentist / payment plan, click through to patient detail (linked emails + calls)
- **🔍 Semantic search** — toggle between Patients / Emails / Calls search modes
- **📧 Emails** — full mailbox view: filter by direction, category, reply status, match status
- **📞 Calls** — full bOnline call log: direction, state, category, missed+not-returned, after-hours, etc.
- **💰 Lost revenue** — clinical + email + call metrics + Claude-powered recommendations

## File layout

```
Clinic_Analysis/
├── .env                  ← secrets (gitignored)
├── .gitignore
├── requirements.txt      ← Python dependencies
├── schema.sql            ← 22 clinic tables + pgvector (run once)
├── schema_emails.sql     ← emails + email_embeddings (run once)
├── schema_calls.sql      ← calls + call_embeddings (run once)
├── generate_data.py      ← fake clinic data → Supabase
├── embed.py              ← patient summaries → Voyage → pgvector
├── generate_emails.py    ← fake mailbox (Claude Haiku) → Supabase
├── embed_emails.py       ← email summaries → Voyage → pgvector
├── generate_calls.py     ← fake bOnline call log (Claude Haiku) → Supabase
├── embed_calls.py        ← call summaries → Voyage → pgvector
├── app.py                ← Streamlit UI
└── lib/
    ├── db.py             ← Supabase connection helpers
    ├── fake_clinic.py    ← clinic data generation logic
    ├── fake_emails.py    ← email planner + Claude Haiku bodies
    ├── fake_calls.py     ← call planner + Claude Haiku transcripts
    └── analysis.py       ← lost revenue SQL + Claude narrative
```

## Tweaking the simulation

Open `lib/fake_clinic.py` and edit the `CONFIG` dict at the top:

```python
CONFIG = {
    "num_patients": 1000,        # bigger → slower to generate
    "history_years": 2,
    "pct_fta": 0.08,             # 8% no-show rate
    "pct_cancelled": 0.10,
    "pct_churned_patients": 0.05,
    "pct_overdue_recall": 0.20,
    "pct_unpaid_invoices": 0.12,
    "pct_uncompleted_tp_items": 0.25,
}
```

Then re-run `python generate_data.py` to wipe and reseed.
