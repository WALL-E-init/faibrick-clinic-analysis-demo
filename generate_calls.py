"""Step 5 — Generate simulated phone calls and load into Supabase.

Depends on:
    - patients already seeded (run generate_data.py first)
    - schema_calls.sql applied to Supabase

Usage:
    python generate_calls.py             # 2000 calls (~5-15 min)
    python generate_calls.py --count 200 # smaller batch for testing
    python generate_calls.py --no-wipe   # append instead of wiping first
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from psycopg2.extras import execute_values

from lib.db import get_conn
from lib.fake_calls import CONFIG, generate_all_calls


def fetch_patients() -> list[dict[str, Any]]:
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, first_name, last_name,
                   mobile_phone, home_phone, work_phone,
                   medical_alert, medical_alert_text
            FROM patients
            WHERE active = TRUE
        """)
        return [dict(row) for row in cur.fetchall()]


def bulk_insert_calls(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("[seed] calls: (empty)")
        return
    columns = list(rows[0].keys())
    cols_sql = ", ".join(columns)
    template = "(" + ", ".join(["%s"] * len(columns)) + ")"
    sql = f"INSERT INTO calls ({cols_sql}) VALUES %s"
    data = [tuple(r[c] for c in columns) for r in rows]
    with get_conn() as conn, conn.cursor() as cur:
        execute_values(cur, sql, data, template=template, page_size=500)
        conn.commit()
    print(f"[seed] calls: {len(rows)} rows")


def wipe_calls() -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE calls, call_embeddings RESTART IDENTITY CASCADE")
        conn.commit()
    print("[wipe] Truncated calls + call_embeddings")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--no-wipe", action="store_true")
    args = parser.parse_args()

    if args.count:
        CONFIG["total_calls"] = args.count

    print("[db] Loading patients...")
    patients = fetch_patients()
    print(f"[db] {len(patients)} active patients loaded")
    if not patients:
        print("ERROR: no patients in DB — run generate_data.py first")
        return 1

    rows = generate_all_calls(patients)

    if not args.no_wipe:
        wipe_calls()

    bulk_insert_calls(rows)

    print("\n[done] Calls loaded. Next step: python embed_calls.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
