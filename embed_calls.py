"""Step 6 — Build vector embeddings for semantic call search."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import voyageai
from psycopg2.extras import execute_values

from lib.db import VOYAGE_API_KEY, get_conn

MODEL = "voyage-3-lite"
BATCH_SIZE = 128


def build_call_summary(row: dict) -> str:
    direction = row["direction"]
    category = row["category"]
    state = row["state"]
    patient = row.get("patient_name") or "(no patient link)"
    agent = row.get("agent_name") or "-"
    returned = "returned" if row["is_returned"] else ("NOT returned" if state in ("missed", "voicemail") else "")
    after = "after-hours" if row.get("after_hours") else "business-hours"
    ts = row.get("started_at")
    ts_str = ts.date().isoformat() if ts else "unknown date"
    summary = row.get("summary") or ""
    transcript = (row.get("transcript") or "").replace("\n", " ")

    return (
        f"{direction.upper()} {category} call, state={state}, {after}, "
        f"agent {agent}, patient {patient}, {returned} on {ts_str}. "
        f"Summary: {summary}. Transcript: {transcript}"
    )


def fetch_calls_with_patient() -> list[dict]:
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                c.*,
                p.first_name || ' ' || p.last_name AS patient_name
            FROM calls c
            LEFT JOIN patients p ON p.id = c.patient_id
            ORDER BY c.id
        """)
        return [dict(r) for r in cur.fetchall()]


def embed_in_batches(texts: list[str]) -> list[list[float]]:
    client = voyageai.Client(api_key=VOYAGE_API_KEY)
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        result = client.embed(batch, model=MODEL, input_type="document")
        out.extend(result.embeddings)
        print(f"[embed] batch {i // BATCH_SIZE + 1}: {len(batch)} calls → {result.total_tokens} tokens")
    return out


def upsert_embeddings(rows: list[tuple[int, str, list[float]]]) -> None:
    sql = """
        INSERT INTO call_embeddings (call_id, summary_text, embedding, updated_at)
        VALUES %s
        ON CONFLICT (call_id) DO UPDATE SET
          summary_text = EXCLUDED.summary_text,
          embedding    = EXCLUDED.embedding,
          updated_at   = NOW()
    """
    data = [(cid, summary, str(vec), datetime.now(timezone.utc)) for cid, summary, vec in rows]
    with get_conn() as conn, conn.cursor() as cur:
        execute_values(cur, sql, data, page_size=500)
        conn.commit()
    print(f"[db] Upserted {len(rows)} call embeddings")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not VOYAGE_API_KEY:
        print("ERROR: VOYAGE_API_KEY missing")
        return 1

    print("[fetch] Loading calls from Supabase...")
    calls = fetch_calls_with_patient()
    if args.limit:
        calls = calls[: args.limit]
    print(f"[fetch] {len(calls)} calls to embed")
    if not calls:
        print("Nothing to do — run generate_calls.py first")
        return 1

    summaries = [build_call_summary(c) for c in calls]
    print(f"[fetch] Sample summary:\n  {summaries[0][:240]}...\n")

    print(f"[embed] Calling Voyage AI ({MODEL}, 512 dim)...")
    vectors = embed_in_batches(summaries)

    rows = [(c["id"], s, v) for c, s, v in zip(calls, summaries, vectors)]
    upsert_embeddings(rows)

    print("\n[done] Call embeddings complete. Refresh the Streamlit app to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
