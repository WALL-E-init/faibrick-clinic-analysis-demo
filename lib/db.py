"""Database connection helpers.

Loads credentials from .env and provides:
- get_conn(): direct psycopg2 connection for bulk inserts and SQL queries
- execute_sql_file(path): runs a .sql file (used to apply schema.sql)
"""

import os
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load .env from project root (one level up from lib/). Locally this gives us
# credentials. On Streamlit Community Cloud the .env file is absent — we fall
# back to `st.secrets` instead (configured via the Cloud dashboard).
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)


def _secret(name: str) -> str | None:
    """Try os.environ first, then Streamlit secrets if available."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st  # lazy — avoids forcing streamlit at import time
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001
        pass
    return None


DATABASE_URL = _secret("DATABASE_URL")
SUPABASE_URL = _secret("SUPABASE_URL")
SUPABASE_ANON_KEY = _secret("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = _secret("SUPABASE_SERVICE_ROLE_KEY")
VOYAGE_API_KEY = _secret("VOYAGE_API_KEY")
ANTHROPIC_API_KEY = _secret("ANTHROPIC_API_KEY")


def require_env():
    """Fail loudly if critical env vars are missing."""
    missing = []
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if missing:
        raise RuntimeError(
            f"Missing environment variables in .env: {', '.join(missing)}\n"
            f"Expected .env at: {ENV_PATH}"
        )


def get_conn(dict_cursor: bool = False):
    """Return a new psycopg2 connection to Supabase Postgres."""
    require_env()
    if dict_cursor:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return psycopg2.connect(DATABASE_URL)


def execute_sql_file(path: str | Path) -> None:
    """Execute every statement in a .sql file."""
    sql = Path(path).read_text(encoding="utf-8")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
    print(f"[OK] Executed {path}")


def table_count(table: str) -> int:
    """Return row count for a table (quick sanity check)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]
