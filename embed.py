"""Step 2 — Build vector embeddings for semantic patient search.

What it does:
    1. For each patient, builds a plain-text summary of their clinical + financial story
    2. Sends batches to Voyage AI (voyage-3-lite, 512 dim) for embedding
    3. Upserts results into patient_embeddings table

Usage:
    python embed.py
    python embed.py --limit 50   # only embed 50 patients (for testing)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any

import voyageai
from psycopg2.extras import execute_values

from lib.db import VOYAGE_API_KEY, get_conn

MODEL = "voyage-3-lite"
BATCH_SIZE = 128  # Voyage's max inputs per call


def build_patient_summary(ctx: dict[str, Any]) -> str:
    """Build a natural-language summary of a patient's history for embedding."""
    p = ctx["patient"]
    s = ctx.get("stats") or {}
    appts = ctx.get("appointments", [])
    plans = ctx.get("plans", [])
    invoices = ctx.get("invoices", [])
    recalls = ctx.get("recalls", [])

    age = None
    if p.get("date_of_birth"):
        dob = p["date_of_birth"]
        today = datetime.now(timezone.utc).date()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    parts: list[str] = []
    parts.append(
        f"{p.get('title') or ''} {p['first_name']} {p['last_name']}, "
        f"{age}yo {p.get('gender') or ''}, {'active' if p.get('active') else 'inactive (archived)'} patient."
    )

    if p.get("payment_plan_id"):
        plan_name = {1: "NHS", 2: "Private", 3: "Denplan"}.get(p["payment_plan_id"], "unknown")
        parts.append(f"Payment plan: {plan_name}.")

    if p.get("medical_alert") and p.get("medical_alert_text"):
        parts.append(f"Medical alert: {p['medical_alert_text']}.")

    if s.get("first_appointment_date"):
        parts.append(f"First visited {s['first_appointment_date']}.")
    if s.get("last_appointment_date"):
        parts.append(f"Last visit {s['last_appointment_date']}.")
    if s.get("last_exam_date"):
        parts.append(f"Last exam {s['last_exam_date']}.")
    if s.get("next_appointment_date"):
        parts.append(f"Next appointment {s['next_appointment_date']}.")
    else:
        parts.append("No future appointment booked.")

    completed = [a for a in appts if a["state"] == "Completed"]
    fta = [a for a in appts if a["state"] == "Did not attend"]
    cancelled = [a for a in appts if a["state"] == "Cancelled"]
    parts.append(
        f"{len(completed)} completed appointments, {len(fta)} no-shows, {len(cancelled)} cancellations."
    )

    # Treatments performed
    reasons = [a["reason"] for a in completed if a["reason"]]
    if reasons:
        top_reasons = list(dict.fromkeys(reasons))[:5]
        parts.append(f"Treatments: {', '.join(top_reasons)}.")

    # Plans with uncompleted items = lost revenue
    incomplete_plans = [pl for pl in plans if not pl["completed"]]
    if incomplete_plans:
        total_unfinished = sum(float(pl["private_treatment_value"] or 0) for pl in incomplete_plans)
        parts.append(f"{len(incomplete_plans)} uncompleted treatment plans worth £{total_unfinished:.0f}.")

    # Financial
    if s.get("total_invoiced"):
        parts.append(f"Total invoiced £{float(s['total_invoiced']):.0f}, paid £{float(s['total_paid'] or 0):.0f}.")

    unpaid = [inv for inv in invoices if not inv["paid"]]
    if unpaid:
        total_due = sum(float(inv["amount_outstanding"] or 0) for inv in unpaid)
        parts.append(f"{len(unpaid)} unpaid invoices totalling £{total_due:.0f}.")

    # Recall status
    for r in recalls:
        if r["status"] in ("Unbooked", "Missed"):
            parts.append(f"Recall overdue ({r['status']}), due {r['due_date']}.")
            break

    # Notes from appointments (short)
    notes = [a["notes"] for a in appts if a.get("notes")]
    if notes:
        parts.append("Notes: " + " / ".join(notes[:3]))

    return " ".join(parts)


def fetch_all_context() -> list[dict[str, Any]]:
    """Fetch everything needed to build summaries. One dict per patient."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM patients")
        patients = cur.fetchall()

        cur.execute("SELECT * FROM patient_stats")
        stats = {row["patient_id"]: row for row in cur.fetchall()}

        cur.execute("SELECT patient_id, state, reason, notes, start_time FROM appointments")
        appts_by_patient: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            appts_by_patient.setdefault(row["patient_id"], []).append(row)

        cur.execute("SELECT patient_id, completed, private_treatment_value FROM treatment_plans")
        plans_by_patient: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            plans_by_patient.setdefault(row["patient_id"], []).append(row)

        cur.execute("SELECT patient_id, paid, amount_outstanding FROM invoices")
        inv_by_patient: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            inv_by_patient.setdefault(row["patient_id"], []).append(row)

        cur.execute("SELECT patient_id, status, due_date FROM recalls")
        recalls_by_patient: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            recalls_by_patient.setdefault(row["patient_id"], []).append(row)

    return [
        {
            "patient": dict(p),
            "stats": dict(stats.get(p["id"], {})) if stats.get(p["id"]) else {},
            "appointments": appts_by_patient.get(p["id"], []),
            "plans": plans_by_patient.get(p["id"], []),
            "invoices": inv_by_patient.get(p["id"], []),
            "recalls": recalls_by_patient.get(p["id"], []),
        }
        for p in patients
    ]


def embed_in_batches(texts: list[str]) -> list[list[float]]:
    client = voyageai.Client(api_key=VOYAGE_API_KEY)
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        result = client.embed(batch, model=MODEL, input_type="document")
        all_vecs.extend(result.embeddings)
        print(f"[embed] batch {i // BATCH_SIZE + 1}: {len(batch)} patients → {result.total_tokens} tokens")
    return all_vecs


def upsert_embeddings(rows: list[tuple[int, str, list[float]]]) -> None:
    sql = """
        INSERT INTO patient_embeddings (patient_id, summary_text, embedding, updated_at)
        VALUES %s
        ON CONFLICT (patient_id) DO UPDATE SET
          summary_text = EXCLUDED.summary_text,
          embedding    = EXCLUDED.embedding,
          updated_at   = NOW()
    """
    # pgvector accepts vectors as Python lists → cast to str representation
    data = [(pid, summary, str(vec), datetime.now(timezone.utc)) for pid, summary, vec in rows]
    with get_conn() as conn, conn.cursor() as cur:
        execute_values(cur, sql, data, page_size=500)
        conn.commit()
    print(f"[db] Upserted {len(rows)} patient embeddings")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only embed N patients (for testing)")
    args = parser.parse_args()

    if not VOYAGE_API_KEY:
        print("ERROR: VOYAGE_API_KEY missing from .env")
        return 1

    print("[fetch] Loading patient context from Supabase...")
    contexts = fetch_all_context()
    if args.limit:
        contexts = contexts[: args.limit]
    print(f"[fetch] Building summaries for {len(contexts)} patients")

    summaries = [build_patient_summary(ctx) for ctx in contexts]
    print(f"[fetch] Sample summary:\n  {summaries[0][:300]}...\n")

    print(f"[embed] Calling Voyage AI ({MODEL}, 512 dim)...")
    vectors = embed_in_batches(summaries)

    rows = [
        (ctx["patient"]["id"], summary, vec)
        for ctx, summary, vec in zip(contexts, summaries, vectors)
    ]
    upsert_embeddings(rows)

    print("\n[done] Embedding complete. Next step: streamlit run app.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
