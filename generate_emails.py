"""Step 3 — Generate simulated emails and load into Supabase.

Depends on:
    - patients already seeded (run generate_data.py first)
    - schema_emails.sql applied to Supabase (adds emails + email_embeddings)

Usage:
    python generate_emails.py             # full 2000 emails (takes ~5-15 min)
    python generate_emails.py --count 200 # smaller batch for testing
    python generate_emails.py --no-wipe   # append instead of wiping first
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from psycopg2.extras import execute_values, Json

from lib.db import get_conn
from lib.fake_emails import CONFIG, generate_all_emails


JSONB_COLS = {"cc_addresses"}


def fetch_patients() -> list[dict[str, Any]]:
    """Fetch the patient list from Supabase for email planning."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, first_name, last_name, email_address, date_of_birth,
                   gender, payment_plan_id, medical_alert, medical_alert_text
            FROM patients
            WHERE active = TRUE
        """)
        return [dict(row) for row in cur.fetchall()]


def prepare_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for col in JSONB_COLS:
        if col in out and out[col] is not None:
            out[col] = Json(out[col])
    return out


def bulk_insert_emails(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("[seed] emails: (empty)")
        return
    prepared = [prepare_row(r) for r in rows]
    columns = list(prepared[0].keys())
    cols_sql = ", ".join(columns)
    template = "(" + ", ".join(["%s"] * len(columns)) + ")"
    sql = f"INSERT INTO emails ({cols_sql}) VALUES %s"
    data = [tuple(r[c] for c in columns) for r in prepared]

    with get_conn() as conn, conn.cursor() as cur:
        execute_values(cur, sql, data, template=template, page_size=500)
        conn.commit()
    print(f"[seed] emails: {len(rows)} rows")


def wipe_emails() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE emails, email_embeddings RESTART IDENTITY CASCADE")
        conn.commit()
    print("[wipe] Truncated emails + email_embeddings")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=None, help="Override total email count")
    parser.add_argument("--no-wipe", action="store_true", help="Append instead of wiping")
    args = parser.parse_args()

    if args.count:
        CONFIG["total_emails"] = args.count

    print("[db] Loading patients...")
    patients = fetch_patients()
    print(f"[db] {len(patients)} active patients loaded")
    if not patients:
        print("ERROR: no patients in DB — run generate_data.py first")
        return 1

    rows = generate_all_emails(patients)

    if not args.no_wipe:
        wipe_emails()

    bulk_insert_emails(rows)

    print("\n[done] Emails loaded. Next step: python embed_emails.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
