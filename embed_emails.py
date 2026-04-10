"""Step 4 — Build vector embeddings for semantic email search.

What it does:
    1. Reads all emails + linked patient name
    2. Builds a compact summary line per email
    3. Embeds with Voyage AI voyage-3-lite (512 dim) in batches of 128
    4. Upserts into email_embeddings

Usage:
    python embed_emails.py
    python embed_emails.py --limit 100   # test with a subset
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import voyageai
from psycopg2.extras import execute_values

from lib.db import VOYAGE_API_KEY, get_conn

MODEL = "voyage-3-lite"
BATCH_SIZE = 128


def build_email_summary(row: dict) -> str:
    """Short natural-language summary of an email (used as embed input)."""
    direction = row["direction"]
    category = row["category"]
    from_name = row["from_name"] or row["from_address"]
    patient_name = row.get("patient_name") or "(no patient link)"
    subject = row["subject"] or ""
    body = (row["body_text"] or "").replace("\n", " ")
    replied = "replied" if row["is_replied"] else "not replied"
    priority = row["priority"] or "normal"
    ts = row["received_at"] or row["sent_at"]
    ts_str = ts.date().isoformat() if ts else "unknown date"

    parts = [
        f"{direction.upper()} {category} email from {from_name}",
        f"about patient {patient_name}" if row.get("patient_id") else "no patient match",
        f"on {ts_str}, priority {priority}, {replied}.",
        f"Subject: {subject}.",
        f"Body: {body}",
    ]
    return " ".join(parts)


def fetch_emails_with_patient() -> list[dict]:
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                e.*,
                p.first_name || ' ' || p.last_name AS patient_name
            FROM emails e
            LEFT JOIN patients p ON p.id = e.patient_id
            ORDER BY e.id
        """)
        return [dict(r) for r in cur.fetchall()]


def embed_in_batches(texts: list[str]) -> list[list[float]]:
    client = voyageai.Client(api_key=VOYAGE_API_KEY)
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        result = client.embed(batch, model=MODEL, input_type="document")
        out.extend(result.embeddings)
        print(f"[embed] batch {i // BATCH_SIZE + 1}: {len(batch)} emails → {result.total_tokens} tokens")
    return out


def upsert_embeddings(rows: list[tuple[int, str, list[float]]]) -> None:
    sql = """
        INSERT INTO email_embeddings (email_id, summary_text, embedding, updated_at)
        VALUES %s
        ON CONFLICT (email_id) DO UPDATE SET
          summary_text = EXCLUDED.summary_text,
          embedding    = EXCLUDED.embedding,
          updated_at   = NOW()
    """
    data = [(eid, summary, str(vec), datetime.now(timezone.utc)) for eid, summary, vec in rows]
    with get_conn() as conn, conn.cursor() as cur:
        execute_values(cur, sql, data, page_size=500)
        conn.commit()
    print(f"[db] Upserted {len(rows)} email embeddings")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not VOYAGE_API_KEY:
        print("ERROR: VOYAGE_API_KEY missing")
        return 1

    print("[fetch] Loading emails from Supabase...")
    emails = fetch_emails_with_patient()
    if args.limit:
        emails = emails[: args.limit]
    print(f"[fetch] {len(emails)} emails to embed")
    if not emails:
        print("Nothing to do — run generate_emails.py first")
        return 1

    summaries = [build_email_summary(e) for e in emails]
    print(f"[fetch] Sample summary:\n  {summaries[0][:240]}...\n")

    print(f"[embed] Calling Voyage AI ({MODEL}, 512 dim)...")
    vectors = embed_in_batches(summaries)

    rows = [(e["id"], s, v) for e, s, v in zip(emails, summaries, vectors)]
    upsert_embeddings(rows)

    print("\n[done] Email embeddings complete. Refresh the Streamlit app to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
