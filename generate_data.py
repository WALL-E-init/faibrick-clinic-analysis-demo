"""Step 1 — Generate fake clinic data and load into Supabase.

Usage:
    python generate_data.py            # generate + seed (wipes existing rows first)
    python generate_data.py --no-wipe  # append without wiping
    python generate_data.py --json-only  # just write data/*.json, don't seed

What it does:
    1. Generates a deterministic fake UK dental clinic (1000 patients, 2y)
    2. Optionally saves each table to data/<table>.json (for inspection)
    3. Truncates existing rows in Supabase
    4. Bulk-inserts everything using psycopg2 execute_values (fast)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from psycopg2.extras import execute_values, Json

from lib.db import get_conn
from lib.fake_clinic import generate_all

# Insert order — setup first, then patients, then everything referencing them
INSERT_ORDER = [
    "practice",
    "sites",
    "rooms",
    "practitioners",
    "payment_plans",
    "treatments",
    "appointment_cancellation_reasons",
    "acquisition_sources",
    "patients",
    "patient_stats",
    "accounts",
    "appointments",
    "treatment_plans",
    "treatment_plan_items",
    "recalls",
    "invoices",
    "invoice_items",
    "payments",
    "nhs_claims",
]

# Tables that have JSONB columns — psycopg2 needs Json() wrapping
JSONB_COLUMNS = {
    "practice": {"opening_hours"},
    "sites": {"opening_hours"},
    "treatment_plan_items": {"teeth", "surfaces"},
}


def json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def save_json(tables: dict[str, list[dict]]) -> None:
    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)
    for name, rows in tables.items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(rows, default=json_default, indent=2), encoding="utf-8")
    print(f"[json] Saved {len(tables)} files to {out_dir}/")


def wipe_all(cur) -> None:
    """Truncate all tables (fast + resets state). Keeps schema."""
    tables = ", ".join(reversed(INSERT_ORDER))  # reverse so dependents go first
    cur.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    print(f"[wipe] Truncated {len(INSERT_ORDER)} tables")


def prepare_row(table: str, row: dict) -> dict:
    """Wrap JSONB fields with psycopg2.extras.Json."""
    jsonb_cols = JSONB_COLUMNS.get(table, set())
    if not jsonb_cols:
        return row
    out = dict(row)
    for col in jsonb_cols:
        if col in out and out[col] is not None:
            out[col] = Json(out[col])
    return out


def bulk_insert(cur, table: str, rows: list[dict]) -> None:
    if not rows:
        print(f"[seed] {table}: (empty, skipping)")
        return
    prepared = [prepare_row(table, r) for r in rows]
    columns = list(prepared[0].keys())
    cols_sql = ", ".join(columns)
    values_template = "(" + ", ".join(["%s"] * len(columns)) + ")"
    sql = f"INSERT INTO {table} ({cols_sql}) VALUES %s"
    data = [tuple(r[c] for c in columns) for r in prepared]
    execute_values(cur, sql, data, template=values_template, page_size=500)
    print(f"[seed] {table}: {len(rows)} rows")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wipe", action="store_true", help="Don't truncate before inserting")
    parser.add_argument("--json-only", action="store_true", help="Generate JSON files only, don't touch DB")
    parser.add_argument("--save-json", action="store_true", help="Also save JSON files (for inspection)")
    args = parser.parse_args()

    tables = generate_all()

    if args.save_json or args.json_only:
        save_json(tables)

    if args.json_only:
        return 0

    print("[db] Connecting to Supabase...")
    with get_conn() as conn:
        with conn.cursor() as cur:
            if not args.no_wipe:
                wipe_all(cur)
            for table_name in INSERT_ORDER:
                rows = tables.get(table_name, [])
                bulk_insert(cur, table_name, rows)
        conn.commit()

    print("\n[done] Seeding complete. Next step: python embed.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
