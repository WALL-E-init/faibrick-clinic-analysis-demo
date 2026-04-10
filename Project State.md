# Clinic Analysis — Project State

**Generated:** 2026-04-11
**Last commit:** `4a49530` docs: update project state snapshot (post-deploy session)
**Current phase:** ✅ Working prototype LIVE on Streamlit Community Cloud. All 7 tabs verified. Ready to build next feature.

---

## What This Is

A working end-to-end simulation of the Emily BI data pipeline. Instead of waiting for the real Germany server + Dentally OAuth + Dan's Azure, we built a complete prototype against a Supabase cloud project using **fake but realistic data**. Since there is no real patient data involved, GDPR does not apply.

The same scripts will later point at the real Germany server with minimal changes (just `.env` swap).

This is the engine behind the Practice Intelligence System — it powers what will become the Morning Brief and revenue recovery features.

---

## Git + GitHub

- **Git repo:** `W:\projects\faibrick\Clinic_Analysis` (git init'd Apr 10)
- **GitHub:** https://github.com/WALL-E-init/faibrick-clinic-analysis-demo (public)
- **Branch:** `main`
- **Commits so far:**
  1. `9e43ce3` — Initial commit: working prototype baseline
  2. `d7907e6` — Add unified timeline, case study, action queues, cloud deploy prep
  3. `4bcf14b` — Fix timezone mismatch in timeline (naive vs aware datetimes)
  4. `846d79b` — Replace voyageai SDK with direct HTTP in app.py (Python 3.14 fix)

---

## Streamlit Community Cloud deployment

- **Status:** ✅ **LIVE** (Apr 11)
- **Repo:** https://github.com/WALL-E-init/faibrick-clinic-analysis-demo
- **Cloud Python version:** 3.14 (Streamlit Cloud's current default)
- **Python 3.14 gotcha (resolved):** the `voyageai` SDK transitively uses `pydantic.v1` which breaks on 3.14. Fix is in `app.py::_embed_query` — it calls `https://api.voyageai.com/v1/embeddings` directly via `requests`. The local `embed_*.py` scripts still use the SDK (they run on Python 3.13 where it works).
- **Secrets:** `lib/db.py` falls back to `st.secrets` when `.env` is absent. Template in `.streamlit/secrets.toml.example`. Secrets live in the app's Settings → Secrets page on share.streamlit.io.
- **DB connection:** Must be Supabase **Session pooler** URL (port 5432, format `postgresql://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres`). Direct connection is IPv6-only and fails from Streamlit Cloud.
- **Auto-redeploy:** every `git push` to `main` triggers a rebuild (~30-60s).
- **Deploy guide:** `W:\projects\faibrick\docs\guides\2026-04-10-Streamlit-Cloud-Deploy.md`

---

## What's Built

### 3 data sources, all simulated

| Source                         | Rows         | Status     | How it's generated              |
| ------------------------------ | ------------ | ---------- | ------------------------------- |
| **Dentally clinic data**       | ~15,463 rows | ✅ Working | Python + Faker, deterministic   |
| **Email inbox** (MS Graph sim) | 2,000 emails | ✅ Working | Claude Haiku + parallel workers |
| **bOnline call log**           | 2,000 calls  | ✅ Working | Claude Haiku + parallel workers |

### 24 database tables

**Layer 0 — Sync infrastructure (2 tables)** — `sync_log`, `sync_state`

**Layer 1 — Practice setup (8 tables)** — `practice`, `sites`, `rooms`, `practitioners`, `payment_plans`, `treatments`, `appointment_cancellation_reasons`, `acquisition_sources`

**Layer 2 — Patient & clinical (6 tables)** — `patients`, `patient_stats`, `appointments`, `treatment_plans`, `treatment_plan_items`, `recalls`

**Layer 3 — Financial (5 tables)** — `accounts`, `invoices`, `invoice_items`, `payments`, `nhs_claims`

**Layer 4 — Vector embeddings (3 tables)** — `patient_embeddings` (512 dim), `email_embeddings` (512 dim), `call_embeddings` (512 dim)

**Extensions + 3 helper functions** — `vector` extension, `search_patients`, `search_emails`, `search_calls` RPCs

### Streamlit UI — 7 tabs

1. **👥 Patients** — filter list, patient detail with NEW **Unified timeline** at the top (appointments + emails + calls + invoices + recalls, newest first, grouped by month)
2. **🔍 Semantic search** — toggle between Patients / Emails / Calls, natural language queries
3. **📧 Emails** — full mailbox filter/browse/read
4. **📞 Calls** — full call log with quick filters
5. **💰 Lost revenue** — live metrics + Claude Sonnet narrative recommendation
6. **📖 Case study** (NEW) — ranks top 15 patients by lost value, pick random or specific patient, Claude Sonnet writes a sales-deck case study
7. **📋 Action queues** (NEW) — three sub-queues (recall / callback / invoice), Claude Haiku drafts personalized messages, simulated "Approve & send"

### Baked-in lost revenue scenarios

#### Clinical leaks (`lib/fake_clinic.py`)

| %   | Signal                              |
| --- | ----------------------------------- |
| 8%  | FTA (failed to attend) appointments |
| 10% | Cancelled appointments              |
| 5%  | Churned (archived) patients         |
| 20% | Overdue for recall                  |
| 12% | Unpaid invoices                     |
| 25% | Uncompleted treatment plan items    |

#### Email leaks (`lib/fake_emails.py`)

30% treatment inquiries unreplied · 25% cancellations unreplied · 10% complaints unreplied · 5% inbound from non-patients

#### Call leaks (`lib/fake_calls.py`)

Emergency 90% returned · **Treatment inquiry 50% returned** · Complaint 80% · Appointment inquiry 70% · General 35%

---

## File layout

```
Clinic_Analysis/                       ← git repo root
├── .env                               ← Supabase + Voyage + Anthropic (gitignored)
├── .gitignore
├── .streamlit/
│   └── secrets.toml.example           ← template for Streamlit Cloud secrets
├── README.md                          ← run instructions
├── Project State.md                   ← THIS FILE
├── requirements.txt                   ← includes `requests` for Voyage HTTP fallback
│
├── schema.sql                         ← 22 core tables + pgvector + patient_embeddings
├── schema_emails.sql                  ← emails + email_embeddings + search_emails
├── schema_calls.sql                   ← calls + call_embeddings + search_calls
│
├── generate_data.py                   ← Step 1 — fake clinic data (~20s)
├── embed.py                           ← Step 2 — patient vectors (~30s)
├── generate_emails.py                 ← Step 3 — fake mailbox (~5-15min)
├── embed_emails.py                    ← Step 4 — email vectors (~30s)
├── generate_calls.py                  ← Step 5 — fake calls (~5-15min)
├── embed_calls.py                     ← Step 6 — call vectors (~30s)
│
├── app.py                             ← Streamlit UI (7 tabs)
│
└── lib/
    ├── __init__.py
    ├── db.py                          ← DB conn, reads .env OR st.secrets
    ├── fake_clinic.py                 ← Faker-based clinic generator
    ├── fake_emails.py                 ← Email planner + Claude Haiku
    ├── fake_calls.py                  ← Call planner + Claude Haiku
    ├── analysis.py                    ← Lost revenue SQL + Claude narrative
    ├── timeline.py     (NEW)          ← Merged per-patient event feed
    ├── case_study.py   (NEW)          ← Candidate ranking + Claude case study
    └── actions.py      (NEW)          ← Three action queues + Claude drafts
```

**Total:** ~6,000 lines of code across 16 Python files + 3 SQL files.

---

## Tech Stack

| Layer             | Tech                                   | Why                                                      |
| ----------------- | -------------------------------------- | -------------------------------------------------------- |
| Database          | Supabase cloud Postgres + pgvector     | No Docker, matches production, free tier                 |
| Fake clinic data  | Python + Faker                         | Deterministic, fast, free                                |
| Fake emails/calls | Claude Haiku 4.5 (parallel, 5 workers) | More realistic than templates                            |
| Embeddings (seed) | Voyage AI voyage-3-lite SDK (512 dim)  | Free 200M tokens, IVFFlat cosine index                   |
| Embeddings (UI)   | Voyage AI via plain HTTP `requests`    | Python 3.14 on Streamlit Cloud breaks voyageai SDK       |
| Analysis + cases  | Claude Sonnet 4.6                      | Narrative generation                                     |
| Message drafts    | Claude Haiku 4.5                       | Cheap, fast, good for short messages                     |
| UI                | Streamlit                              | Fast prototyping, Python-native                          |
| Connection        | psycopg2 via Session pooler            | IPv4-compatible, fast bulk insert                        |
| Deployment        | Streamlit Community Cloud (free tier)  | Public URL for Mihai/Rudolf without local Python install |

---

## How to run the full pipeline (locally)

```bash
cd W:\projects\faibrick\Clinic_Analysis

# First time setup
pip install -r requirements.txt

# Apply all 3 schemas in Supabase SQL Editor (one time each):
#   schema.sql
#   schema_emails.sql
#   schema_calls.sql

# Populate data
python generate_data.py    # ~20s
python embed.py            # ~30s
python generate_emails.py  # ~5-15min
python embed_emails.py     # ~30s
python generate_calls.py   # ~5-15min
python embed_calls.py      # ~30s

# Launch UI
streamlit run app.py
```

Once data is seeded, re-running `app.py` is instant. Re-running `generate_*.py` wipes and re-seeds.

---

## Key decisions

| Decision                                               | Date   | Why                                                         |
| ------------------------------------------------------ | ------ | ----------------------------------------------------------- |
| Build prototype against Supabase cloud + fake data     | Apr 10 | Stop waiting on blockers, prove end-to-end architecture     |
| Streamlit Community Cloud (not Vercel/Render)          | Apr 10 | Python-native, zero config, free tier                       |
| New lib modules (timeline, case_study, actions)        | Apr 10 | Keep app.py thin, reusable for future UI variations         |
| Claude Haiku for drafted messages (not Sonnet)         | Apr 10 | Cheap per-row, short output is well within Haiku quality    |
| Candidate ranking formula for case study (SQL scoring) | Apr 10 | Deterministic, surfaces best sales stories without LLM      |
| Replace voyageai SDK with HTTP in web app only         | Apr 11 | voyageai transitively breaks on Python 3.14 via pydantic.v1 |
| Git init at Clinic_Analysis (not faibrick root)        | Apr 10 | Streamlit Cloud needs a repo at the app root                |
| TIMESTAMPTZ everywhere in timeline (UTC for DATE cols) | Apr 10 | Python refuses to sort mixed naive/aware datetimes          |

---

## Known issues

| Issue                                                 | Impact                                   | Workaround                                       |
| ----------------------------------------------------- | ---------------------------------------- | ------------------------------------------------ |
| Voyage AI free tier: 3 RPM / 10K TPM                  | Embedding crashed at seed time           | Added payment method (still 200M free tokens)    |
| Supabase direct connection is IPv6-only               | DNS fails on Windows AND Streamlit Cloud | Use Session pooler URL                           |
| psycopg2-binary 2.9.9 no wheels on Python 3.13        | pip install failure                      | Bumped to `>=2.9.10`                             |
| voyageai SDK broken on Python 3.14                    | Streamlit Cloud boot crash               | Web app now uses `requests` HTTP; SDK local only |
| Claude Haiku tier 1 rate limit (10K OTPM)             | Gen takes 10-15 min for 2000 rows        | Accept or upgrade tier                           |
| Timezone mismatch in timeline sort                    | App crash on any patient                 | Fixed Apr 10 — `_date_to_aware_dt()` helper      |
| Streamlit Cloud `DATABASE_URL` was malformed (Apr 11) | App booted but DB queries failed         | ✅ Fixed — clean Session pooler URL pasted       |

---

## What's next

### Public demo status

✅ **Live and verified.** All 7 tabs working end-to-end on Streamlit Community Cloud.

### High-impact features to add next

1. **Morning Brief** (⭐⭐⭐⭐⭐) — the actual Emily BI product vision. Single "today" screen with tomorrow's schedule, calls to return, high-value unreplied emails, recalls due, invoices to chase, red flags
2. **Sentiment trends over time** — weekly aggregate call/email sentiment
3. **Treatment funnel visualization** — inquiry → consult → plan → completed → paid drop-off
4. **Practitioner dashboard** — per-dentist revenue / no-shows / completion
5. **Incremental sync simulator** — exercise `sync_log` / `sync_state` machinery
6. **Export to Excel/PDF weekly reports**

### Production switch-over (still blocked)

- **Radu:** Install Supabase on the Germany server → will notify when done
- **Nicu/team:** Dentally API credentials (OAuth Bearer token, read-only)
- When unblocked: swap `.env` → real data flows through same code

---

## How to work on this project (for a new session)

1. **Read this file** — it's the fastest way to get context.
2. **Read the root:** `W:\projects\faibrick\PROJECT-STATE.md` for broader Faibrick context
3. **Check git log:** `git log --oneline -10` for recent work
4. **Check Qdrant:** `qdrant-find` in the `faibrick` collection for past decisions
5. **Watch out for:**
   - `DATABASE_URL` must be the **Session pooler** URL, not direct (IPv6-only)
   - `.env` is gitignored — never commit secrets
   - App uses **plain HTTP** for Voyage in `app.py::_embed_query`, NOT the SDK (Python 3.14 incompat)
   - Timeline events mixing DATE and TIMESTAMPTZ need `_date_to_aware_dt()` from `lib/timeline.py`
   - All Python 3.13 locally; Streamlit Cloud is Python 3.14 — treat them differently for SDK compatibility
6. **Vali is not a developer** — explain everything in plain language
7. **All non-code files** start with date: `YYYY-MM-DD-description.md`
