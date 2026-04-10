# Clinic Analysis — Project State

**Generated:** 2026-04-10
**Current phase:** Working local simulation prototype (Phase 1 complete, ready for Phase 2)

---

## What This Is

A working end-to-end simulation of the Emily BI data pipeline. Instead of waiting for the real Germany server + Dentally OAuth + Dan's Azure, we built a complete prototype against a Supabase cloud project using **fake but realistic data**. Since there is no real patient data involved, GDPR does not apply.

The same scripts will later point at the real Germany server with minimal changes (just `.env` swap).

This is the engine behind the Practice Intelligence System — it powers what will become the Morning Brief and revenue recovery features.

---

## What's Built (April 10, 2026)

### 3 data sources, all simulated

| Source                         | Rows         | Status     | How it's generated              |
| ------------------------------ | ------------ | ---------- | ------------------------------- |
| **Dentally clinic data**       | ~15,463 rows | ✅ Working | Python + Faker, deterministic   |
| **Email inbox** (MS Graph sim) | 2,000 emails | ✅ Working | Claude Haiku + parallel workers |
| **bOnline call log**           | 2,000 calls  | ✅ Working | Claude Haiku + parallel workers |

### 24 database tables

**Layer 0 — Sync infrastructure (2 tables)**

- `sync_log`, `sync_state`

**Layer 1 — Practice setup (8 tables)**

- `practice`, `sites`, `rooms`, `practitioners`, `payment_plans`, `treatments`, `appointment_cancellation_reasons`, `acquisition_sources`

**Layer 2 — Patient & clinical (6 tables)**

- `patients`, `patient_stats`, `appointments`, `treatment_plans`, `treatment_plan_items`, `recalls`

**Layer 3 — Financial (5 tables)**

- `accounts`, `invoices`, `invoice_items`, `payments`, `nhs_claims`

**Layer 4 — Vector embeddings (3 tables)**

- `patient_embeddings` (512 dim)
- `email_embeddings` (512 dim)
- `call_embeddings` (512 dim)

**Extensions + 3 helper functions**

- `vector` extension enabled
- `search_patients(query_embedding, k)` RPC
- `search_emails(query_embedding, k)` RPC
- `search_calls(query_embedding, k)` RPC

### Streamlit UI — 5 tabs

1. **👥 Patients** — filter list, click through to patient detail with linked emails + calls timeline
2. **🔍 Semantic search** — toggle between Patients / Emails / Calls, natural language queries
3. **📧 Emails** — full mailbox filter/browse/read
4. **📞 Calls** — full call log with quick filters for missed+not-returned, after-hours, unmatched
5. **💰 Lost revenue** — live metrics from all 3 sources + Claude Sonnet narrative recommendation

---

## Baked-in lost revenue scenarios

The simulation is deliberately seeded with realistic lost-revenue signals that the UI then surfaces:

### Clinical leaks (`lib/fake_clinic.py` CONFIG)

| %   | Signal                                     |
| --- | ------------------------------------------ |
| 8%  | FTA (failed to attend) appointments        |
| 10% | Cancelled appointments                     |
| 5%  | Churned (archived) patients                |
| 20% | Overdue for recall (no future appointment) |
| 12% | Unpaid invoices                            |
| 25% | Uncompleted treatment plan items           |

### Email leaks (`lib/fake_emails.py` UNREPLY_RATE)

| %   | Category                      | Why it matters                             |
| --- | ----------------------------- | ------------------------------------------ |
| 30% | Treatment inquiries unreplied | Lost high-value leads (implants = £2,500+) |
| 25% | Cancellations unreplied       | Missed rebook opportunity                  |
| 10% | Complaints unreplied          | Churn risk                                 |
| 5%  | Inbound from non-patients     | Potential new leads lost                   |

### Call leaks (`lib/fake_calls.py` RETURN_RATE)

| Category            | Callback rate    | Why it matters               |
| ------------------- | ---------------- | ---------------------------- |
| Emergency           | 90% returned     | 10% missed = severe risk     |
| Treatment inquiry   | **50% returned** | **50% of golden leads lost** |
| Complaint           | 80% returned     | Escalation source            |
| Appointment inquiry | 70% returned     | Missed new-patient bookings  |
| General question    | 35% returned     | Lowest priority              |

Plus: ~15% of inbound calls are from numbers not in the patient DB (unmatched leads) + after-hours missed calls.

---

## File layout

```
Clinic_Analysis/
├── .env                     ← Supabase + Voyage + Anthropic credentials (gitignored)
├── .gitignore
├── README.md                ← run instructions
├── Project State.md         ← THIS FILE
├── requirements.txt         ← Python deps (psycopg2, faker, voyageai, anthropic, streamlit, pandas)
│
├── schema.sql               ← 22 core tables + pgvector + patient_embeddings + search_patients
├── schema_emails.sql        ← emails + email_embeddings + search_emails
├── schema_calls.sql         ← calls + call_embeddings + search_calls
│
├── generate_data.py         ← Step 1 — fake clinic data → Supabase (deterministic, 20s)
├── embed.py                 ← Step 2 — patient vector embeddings (~30s)
├── generate_emails.py       ← Step 3 — fake mailbox via Claude Haiku (~5-15min for 2000)
├── embed_emails.py          ← Step 4 — email vector embeddings (~30s)
├── generate_calls.py        ← Step 5 — fake call log via Claude Haiku (~5-15min for 2000)
├── embed_calls.py           ← Step 6 — call vector embeddings (~30s)
│
├── app.py                   ← Streamlit UI (5 tabs)
│
└── lib/
    ├── __init__.py
    ├── db.py                ← Supabase psycopg2 connection helpers
    ├── fake_clinic.py       ← Deterministic Faker-based clinic generator
    ├── fake_emails.py       ← Email planner + Claude Haiku parallel generator
    ├── fake_calls.py        ← Call planner + Claude Haiku parallel generator
    └── analysis.py          ← Lost revenue SQL aggregates + Claude narrative
```

**Total:** ~4,800 lines of code across 13 Python files + 3 SQL files.

---

## Tech Stack

| Layer             | Tech                                   | Why                                           |
| ----------------- | -------------------------------------- | --------------------------------------------- |
| Database          | Supabase cloud Postgres + pgvector     | No Docker, matches production, free tier      |
| Fake clinic data  | Python + Faker                         | Deterministic, fast, free                     |
| Fake emails/calls | Claude Haiku 4.5 (parallel, 5 workers) | More realistic than templates, ~$1 total cost |
| Embeddings        | Voyage AI voyage-3-lite (512 dim)      | Free 200M tokens, IVFFlat cosine index        |
| Analysis          | Claude Sonnet 4.6                      | Narrative generation, 3-issue summaries       |
| UI                | Streamlit                              | Fast prototyping, Python-native               |
| Connection        | psycopg2 via Session pooler            | IPv4-compatible, fast bulk insert             |

---

## How to run the full pipeline

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

Once data is seeded, you can re-run `app.py` at any time without re-seeding. Re-running `generate_*.py` wipes and re-seeds (safe — use `--no-wipe` to append).

---

## Known issues and gotchas

| Issue                                             | Impact                                   | Workaround                                    |
| ------------------------------------------------- | ---------------------------------------- | --------------------------------------------- |
| Voyage AI free tier: 3 RPM / 10K TPM              | Embedding crashed                        | Added payment method (still 200M free tokens) |
| Supabase direct connection is IPv6-only           | DNS resolution fail on Windows           | Use Session pooler URL instead                |
| psycopg2-binary 2.9.9 no wheels on Python 3.13    | pip install failure                      | Bumped to `>=2.9.10`                          |
| Claude Haiku tier 1 output rate limit (10K OTPM)  | Generation takes 10-15 min for 2000 rows | Accept or upgrade tier / reduce count         |
| `.env` file was read into transcript accidentally | Keys visible in session                  | Rotate Voyage + Anthropic keys if concerned   |

---

## What's next — menu of options

Prioritized by demo/value impact. Vali to pick.

### 🔥 High-impact

1. **Morning Brief** (⭐⭐⭐⭐⭐ · medium effort)
   The actual Emily BI product vision. Single "today" screen with:
   - Tomorrow's schedule + empty chair time
   - Calls to return today (prioritized by value)
   - High-value unreplied emails
   - Recalls due this week
   - Invoices to chase
   - Red flags (missed emergencies, unhappy patterns)

2. **Unified patient timeline** (⭐⭐⭐⭐⭐ · small effort)
   One chronological feed per patient: appointments + emails + calls + invoices interleaved by date. The "aha" moment of unified data.

3. **Case study generator** (⭐⭐⭐⭐⭐ · small effort)
   Button: "Pick a lost-revenue story". Claude picks a real fake patient, builds their full timeline, calculates exact money lost, writes as a narrative. **The single most convincing demo asset for sales.**

4. **Action queues with AI-drafted messages** (⭐⭐⭐⭐ · medium effort)
   Three queues: recall outreach / missed callbacks / invoice chasing. Each row shows the patient + auto-drafted personalized message. "Approve & send" (simulated). Moves from "here's a problem" to "here's the fix, 1 click".

5. **Deploy to Streamlit Community Cloud** (⭐⭐⭐⭐ · small-medium effort)
   Share a public URL with Mihai/Rudolf instead of asking them to run Python locally.

### 🛠 Supporting ideas

6. Sentiment trends over time (weekly aggregate call/email sentiment)
7. Treatment funnel: inquiry → consult → plan → completed → paid (shows drop-off)
8. Practitioner dashboard (revenue / no-show / completion per dentist)
9. Incremental sync simulator (test `sync_log` / `sync_state` machinery)
10. Export to Excel/PDF for weekly reports

### Production switch-over

11. Replace `.env` with Germany server credentials once Radu finishes Supabase install
12. Replace fake data generators with real Dentally API sync (using the architecture doc already written)
13. Replace fake email generator with Microsoft Graph API pull (Dan's Azure creds)
14. bOnline Playwright scraper for real call transcripts (no API available)

---

## Why we pivoted to simulation

Original plan: wait for (a) Radu to install Supabase on the Germany server, (b) Nicu to provide Dentally OAuth credentials. Both still blocked as of April 10.

**Vali's decision (April 10):** stop waiting. Build the entire pipeline against fake data on Supabase cloud. This lets us:

- Prove the architecture works end-to-end
- Design the UI without blockers
- Validate the lost-revenue detection logic
- Show Mihai and Rudolf a real, running product instead of a deck
- Drop in real data the moment the server is ready

All downstream code (analysis, UI, embeddings, sync engine) is identical between sim and prod — only the `.env` changes.
