"""Streamlit UI for Faibrick Clinic Analysis prototype.

Run with:
    streamlit run app.py

Four tabs:
    1. Browse patients — with filters + patient detail (including emails)
    2. Semantic search — toggle between patient and email search
    3. Emails — full mailbox view with filters
    4. Lost revenue — metrics + Claude narrative
"""

from __future__ import annotations

import pandas as pd
import requests
import streamlit as st

from lib.analysis import claude_lost_revenue_narrative, lost_revenue_metrics
from lib.case_study import generate_case_study, list_candidates, pick_candidate
from lib.db import VOYAGE_API_KEY, get_conn
from lib.timeline import get_patient_timeline

st.set_page_config(
    page_title="Faibrick — Clinic Analysis",
    page_icon="🦷",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Data fetchers (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def load_patients_df() -> pd.DataFrame:
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                p.id,
                p.first_name || ' ' || p.last_name AS name,
                p.date_of_birth,
                p.email_address,
                p.mobile_phone,
                p.active,
                p.medical_alert,
                p.payment_plan_id,
                pp.name AS payment_plan,
                pr.user_first_name || ' ' || pr.user_last_name AS dentist,
                s.last_appointment_date,
                s.next_appointment_date,
                s.total_invoiced,
                s.total_paid
            FROM patients p
            LEFT JOIN payment_plans pp ON pp.id = p.payment_plan_id
            LEFT JOIN practitioners pr ON pr.id = p.dentist_id
            LEFT JOIN patient_stats s ON s.patient_id = p.id
            ORDER BY p.last_name, p.first_name
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows)


@st.cache_data(ttl=60)
def load_emails_df() -> pd.DataFrame:
    """All emails + linked patient name."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                e.id,
                e.direction,
                e.category,
                e.from_name,
                e.from_address,
                e.to_address,
                e.subject,
                e.body_preview,
                e.received_at,
                e.sent_at,
                e.is_read,
                e.is_replied,
                e.priority,
                e.sentiment,
                e.patient_id,
                e.match_method,
                p.first_name || ' ' || p.last_name AS patient_name
            FROM emails e
            LEFT JOIN patients p ON p.id = e.patient_id
            ORDER BY COALESCE(e.received_at, e.sent_at) DESC
        """)
        return pd.DataFrame(cur.fetchall())


@st.cache_data(ttl=60)
def load_calls_df() -> pd.DataFrame:
    """All calls + linked patient name."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                c.id,
                c.direction,
                c.category,
                c.state,
                c.from_number,
                c.from_name,
                c.to_number,
                c.started_at,
                c.duration_seconds,
                c.ring_seconds,
                c.after_hours,
                c.is_returned,
                c.priority,
                c.sentiment,
                c.summary,
                c.agent_name,
                c.patient_id,
                c.match_method,
                p.first_name || ' ' || p.last_name AS patient_name
            FROM calls c
            LEFT JOIN patients p ON p.id = c.patient_id
            ORDER BY c.started_at DESC
        """)
        return pd.DataFrame(cur.fetchall())


@st.cache_data(ttl=60)
def load_summary_counts() -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        counts = {}
        for table in [
            "patients", "appointments", "treatment_plans",
            "invoices", "payments", "recalls", "patient_embeddings",
        ]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
        # Emails may not exist yet — guard
        try:
            cur.execute("SELECT COUNT(*) FROM emails")
            counts["emails"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM email_embeddings")
            counts["email_embeddings"] = cur.fetchone()[0]
        except Exception:  # noqa: BLE001
            counts["emails"] = 0
            counts["email_embeddings"] = 0
        # Calls may not exist yet — guard
        try:
            cur.execute("SELECT COUNT(*) FROM calls")
            counts["calls"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM call_embeddings")
            counts["call_embeddings"] = cur.fetchone()[0]
        except Exception:  # noqa: BLE001
            counts["calls"] = 0
            counts["call_embeddings"] = 0
    return counts


def load_patient_detail(patient_id: int) -> dict:
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
        patient = cur.fetchone()
        cur.execute("SELECT * FROM patient_stats WHERE patient_id = %s", (patient_id,))
        stats = cur.fetchone()
        cur.execute(
            "SELECT * FROM appointments WHERE patient_id = %s ORDER BY start_time DESC LIMIT 20",
            (patient_id,),
        )
        appts = cur.fetchall()
        cur.execute(
            "SELECT * FROM treatment_plans WHERE patient_id = %s ORDER BY start_date DESC",
            (patient_id,),
        )
        plans = cur.fetchall()
        cur.execute(
            "SELECT * FROM invoices WHERE patient_id = %s ORDER BY dated_on DESC",
            (patient_id,),
        )
        invoices = cur.fetchall()
        cur.execute(
            "SELECT summary_text FROM patient_embeddings WHERE patient_id = %s",
            (patient_id,),
        )
        emb = cur.fetchone()
        # Emails for this patient
        emails = []
        try:
            cur.execute(
                """SELECT id, direction, category, from_name, subject, body_preview,
                          received_at, sent_at, is_replied, priority, thread_id
                   FROM emails
                   WHERE patient_id = %s
                   ORDER BY COALESCE(received_at, sent_at) DESC
                   LIMIT 30""",
                (patient_id,),
            )
            emails = [dict(r) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            pass

        # Calls for this patient
        calls = []
        try:
            cur.execute(
                """SELECT id, direction, category, state, started_at, duration_seconds,
                          ring_seconds, is_returned, priority, summary, agent_name,
                          after_hours
                   FROM calls
                   WHERE patient_id = %s
                   ORDER BY started_at DESC
                   LIMIT 30""",
                (patient_id,),
            )
            calls = [dict(r) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            pass

    return {
        "patient": dict(patient) if patient else None,
        "stats": dict(stats) if stats else None,
        "appointments": [dict(r) for r in appts],
        "plans": [dict(r) for r in plans],
        "invoices": [dict(r) for r in invoices],
        "emails": emails,
        "calls": calls,
        "summary": emb["summary_text"] if emb else None,
    }


def load_email_body(email_id: int) -> dict | None:
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM emails WHERE id = %s", (email_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def load_call_detail(call_id: int) -> dict | None:
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.*, p.first_name || ' ' || p.last_name AS patient_name
            FROM calls c
            LEFT JOIN patients p ON p.id = c.patient_id
            WHERE c.id = %s
        """, (call_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _embed_query(query: str) -> str | None:
    """Embed a single query with Voyage AI via plain HTTP.

    We deliberately avoid the voyageai SDK here because it transitively uses
    pydantic.v1, which breaks on Python 3.14 (Streamlit Community Cloud's
    current default). The local embed scripts still use the SDK because they
    run on Python 3.13.
    """
    if not VOYAGE_API_KEY:
        st.error("VOYAGE_API_KEY missing in .env / Streamlit secrets")
        return None
    try:
        resp = requests.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {VOYAGE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "input": [query],
                "model": "voyage-3-lite",
                "input_type": "query",
            },
            timeout=30,
        )
        resp.raise_for_status()
        embedding = resp.json()["data"][0]["embedding"]
    except Exception as e:  # noqa: BLE001
        st.error(f"Voyage embedding failed: {e}")
        return None
    return str(embedding)


def semantic_search_patients(query: str, k: int = 10) -> pd.DataFrame:
    qvec = _embed_query(query)
    if qvec is None:
        return pd.DataFrame()
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                pe.patient_id,
                p.first_name || ' ' || p.last_name AS name,
                pe.summary_text,
                1 - (pe.embedding <=> %s::vector) AS similarity
            FROM patient_embeddings pe
            JOIN patients p ON p.id = pe.patient_id
            ORDER BY pe.embedding <=> %s::vector
            LIMIT %s
        """, (qvec, qvec, k))
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def semantic_search_emails(query: str, k: int = 15) -> pd.DataFrame:
    qvec = _embed_query(query)
    if qvec is None:
        return pd.DataFrame()
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                ee.email_id,
                e.direction,
                e.category,
                e.from_name,
                e.subject,
                e.body_preview,
                e.is_replied,
                e.priority,
                p.first_name || ' ' || p.last_name AS patient_name,
                1 - (ee.embedding <=> %s::vector) AS similarity
            FROM email_embeddings ee
            JOIN emails e ON e.id = ee.email_id
            LEFT JOIN patients p ON p.id = e.patient_id
            ORDER BY ee.embedding <=> %s::vector
            LIMIT %s
        """, (qvec, qvec, k))
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def semantic_search_calls(query: str, k: int = 15) -> pd.DataFrame:
    qvec = _embed_query(query)
    if qvec is None:
        return pd.DataFrame()
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT
                ce.call_id,
                c.direction,
                c.category,
                c.state,
                c.from_name,
                c.summary,
                c.is_returned,
                c.priority,
                c.started_at,
                p.first_name || ' ' || p.last_name AS patient_name,
                1 - (ce.embedding <=> %s::vector) AS similarity
            FROM call_embeddings ce
            JOIN calls c ON c.id = ce.call_id
            LEFT JOIN patients p ON p.id = c.patient_id
            ORDER BY ce.embedding <=> %s::vector
            LIMIT %s
        """, (qvec, qvec, k))
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def category_icon(cat: str | None) -> str:
    return {
        "appointment_inquiry": "📅",
        "treatment_inquiry": "💰",
        "cancellation": "❌",
        "reschedule": "🔁",
        "complaint": "⚠️",
        "insurance": "🛡️",
        "general_question": "❓",
        "prescription": "💊",
        "positive_feedback": "💚",
        "emergency": "🚨",
        "appointment_confirmation": "✅",
        "appointment_reminder": "🔔",
        "recall_reminder": "🔔",
        "recall_call": "🔔",
        "invoice_reminder": "💷",
        "collections_call": "💷",
        "treatment_followup": "🩺",
        "followup_call": "🩺",
        "reply": "↩️",
    }.get(cat or "", "📧")


def call_state_icon(state: str | None) -> str:
    return {
        "answered": "📞",
        "missed": "📵",
        "voicemail": "🎙️",
        "busy": "🔕",
        "no_answer": "🔕",
    }.get(state or "", "📞")


def fmt_seconds(s: int | None) -> str:
    if not s:
        return "0s"
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


# ---------------------------------------------------------------------------
# Sidebar — sanity check counts
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("🦷 Faibrick — Clinic Analysis")
    st.caption("Dentally + Email + Call simulation (fake data)")
    try:
        counts = load_summary_counts()
        st.metric("Patients", f"{counts['patients']:,}")
        st.metric("Appointments", f"{counts['appointments']:,}")
        st.metric("Treatment plans", f"{counts['treatment_plans']:,}")
        st.metric("Invoices", f"{counts['invoices']:,}")
        st.metric("Emails", f"{counts.get('emails', 0):,}")
        st.metric("Calls", f"{counts.get('calls', 0):,}")
        st.divider()
        st.caption("Vector embeddings")
        st.metric("Patient vectors", f"{counts['patient_embeddings']:,}")
        st.metric("Email vectors", f"{counts.get('email_embeddings', 0):,}")
        st.metric("Call vectors", f"{counts.get('call_embeddings', 0):,}")
    except Exception as e:  # noqa: BLE001
        st.error(f"DB connection failed: {e}")


(
    tab_browse,
    tab_search,
    tab_emails,
    tab_calls,
    tab_revenue,
    tab_case_study,
    tab_actions,
) = st.tabs([
    "👥 Patients",
    "🔍 Semantic search",
    "📧 Emails",
    "📞 Calls",
    "💰 Lost revenue",
    "📖 Case study",
    "📋 Action queues",
])


# ---------------------------------------------------------------------------
# Tab 1: Browse patients
# ---------------------------------------------------------------------------

with tab_browse:
    st.subheader("Patient list")
    try:
        df = load_patients_df()

        col1, col2, col3 = st.columns(3)
        with col1:
            show_inactive = st.checkbox("Include inactive patients", value=False)
        with col2:
            plan_filter = st.multiselect(
                "Payment plan",
                options=sorted(df["payment_plan"].dropna().unique().tolist()),
            )
        with col3:
            dentist_filter = st.multiselect(
                "Dentist",
                options=sorted(df["dentist"].dropna().unique().tolist()),
            )

        filtered = df.copy()
        if not show_inactive:
            filtered = filtered[filtered["active"] == True]  # noqa: E712
        if plan_filter:
            filtered = filtered[filtered["payment_plan"].isin(plan_filter)]
        if dentist_filter:
            filtered = filtered[filtered["dentist"].isin(dentist_filter)]

        st.caption(f"Showing {len(filtered):,} of {len(df):,} patients")
        st.dataframe(
            filtered[[
                "id", "name", "date_of_birth", "payment_plan", "dentist",
                "last_appointment_date", "next_appointment_date",
                "total_invoiced", "total_paid", "active",
            ]],
            use_container_width=True,
            height=400,
            hide_index=True,
        )

        st.divider()
        st.subheader("Patient detail")
        patient_id = st.number_input(
            "Enter patient ID (from the table above)",
            min_value=1,
            value=int(filtered["id"].iloc[0]) if len(filtered) else 1,
            step=1,
        )
        if st.button("Load patient"):
            detail = load_patient_detail(patient_id)
            if not detail["patient"]:
                st.warning(f"No patient with id {patient_id}")
            else:
                p = detail["patient"]
                st.markdown(f"### {p['title'] or ''} {p['first_name']} {p['last_name']}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Active", "Yes" if p["active"] else "No")
                c2.metric("Payment plan", p.get("payment_plan_id") or "—")
                c3.metric("DOB", str(p["date_of_birth"]))
                if detail["summary"]:
                    st.info(detail["summary"])

                with st.expander("🕑 Unified timeline (all sources, newest first)", expanded=True):
                    try:
                        tl = get_patient_timeline(patient_id)
                    except Exception as ex:  # noqa: BLE001
                        st.error(f"Timeline error: {ex}")
                        tl = []
                    if not tl:
                        st.caption("No timeline events for this patient.")
                    else:
                        st.caption(
                            f"{len(tl)} events — combining appointments, emails, calls, "
                            f"invoices, and recalls."
                        )
                        current_month = None
                        for ev in tl:
                            month_key = ev["ts"].strftime("%B %Y")
                            if month_key != current_month:
                                st.markdown(f"**{month_key}**")
                                current_month = month_key
                            date_str = ev["ts"].strftime("%d %b")
                            st.markdown(
                                f"<div style='padding: 4px 0 4px 16px; "
                                f"border-left: 2px solid #444; margin-left: 8px;'>"
                                f"<span style='color:#888;'>{date_str}</span> &nbsp; "
                                f"{ev['icon']} <b>{ev['title']}</b><br>"
                                f"<span style='color:#aaa; font-size: 0.85em;'>"
                                f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{ev['subtitle']}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                with st.expander("Appointments", expanded=False):
                    st.dataframe(pd.DataFrame(detail["appointments"]), use_container_width=True)
                with st.expander("Treatment plans", expanded=False):
                    st.dataframe(pd.DataFrame(detail["plans"]), use_container_width=True)
                with st.expander("Invoices", expanded=False):
                    st.dataframe(pd.DataFrame(detail["invoices"]), use_container_width=True)
                with st.expander(f"📧 Emails ({len(detail['emails'])})", expanded=True):
                    if not detail["emails"]:
                        st.caption("No emails linked to this patient")
                    else:
                        for em in detail["emails"]:
                            icon = category_icon(em["category"])
                            arrow = "📩" if em["direction"] == "inbound" else "📤"
                            replied = "✅ replied" if em["is_replied"] else ("⏳ unreplied" if em["direction"] == "inbound" else "")
                            ts = em.get("received_at") or em.get("sent_at")
                            with st.container(border=True):
                                st.markdown(
                                    f"{arrow} {icon} **{em['subject']}** "
                                    f"<span style='color: #888;'>· {em['category']} · {ts.date() if ts else ''} {replied}</span>",
                                    unsafe_allow_html=True,
                                )
                                st.caption(em["body_preview"])
                with st.expander(f"📞 Calls ({len(detail['calls'])})", expanded=True):
                    if not detail["calls"]:
                        st.caption("No calls linked to this patient")
                    else:
                        for c in detail["calls"]:
                            state_ic = call_state_icon(c["state"])
                            cat_ic = category_icon(c["category"])
                            arrow = "📥" if c["direction"] == "inbound" else "📤"
                            returned = ""
                            if c["state"] in ("missed", "voicemail"):
                                returned = "✅ returned" if c["is_returned"] else "⏳ NOT returned"
                            after = "🌙 after-hours" if c.get("after_hours") else ""
                            with st.container(border=True):
                                st.markdown(
                                    f"{arrow} {state_ic} {cat_ic} **{c['summary'] or c['category']}** "
                                    f"<span style='color: #888;'>· {c['category']} · "
                                    f"{c['started_at'].date() if c.get('started_at') else ''} · "
                                    f"{fmt_seconds(c['duration_seconds'])} · {returned} {after}</span>",
                                    unsafe_allow_html=True,
                                )
    except Exception as e:  # noqa: BLE001
        st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Tab 2: Semantic search
# ---------------------------------------------------------------------------

with tab_search:
    st.subheader("Semantic search")
    st.caption(
        "Plain English search. Choose whether to search patients or the email inbox."
    )

    mode = st.radio(
        "Search mode",
        options=["Patients", "Emails", "Calls"],
        horizontal=True,
    )

    examples_patients = [
        "patients who had crowns or implants and haven't been back in a year",
        "diabetic patients with multiple unpaid invoices",
        "patients with accepted treatment plans they never completed",
        "nervous patients who cancelled multiple appointments",
    ]
    examples_emails = [
        "unanswered questions about implants or Invisalign",
        "complaints about pain after treatment",
        "emails from new people asking about prices",
        "cancellation emails without reschedule",
        "angry patients disputing invoices",
    ]
    examples_calls = [
        "missed calls about implants or Invisalign never returned",
        "emergency calls after hours",
        "angry patients calling about billing",
        "calls from non-patients asking about prices",
        "voicemails about broken teeth",
    ]
    with st.expander("Example queries"):
        examples = {"Patients": examples_patients, "Emails": examples_emails, "Calls": examples_calls}[mode]
        for ex in examples:
            st.code(ex)

    query = st.text_input("Your query", placeholder="Ask in plain English…")
    k = st.slider("Number of results", min_value=5, max_value=30, value=10)

    if st.button("Search", type="primary") and query.strip():
        with st.spinner("Searching..."):
            if mode == "Patients":
                results = semantic_search_patients(query, k)
            elif mode == "Emails":
                results = semantic_search_emails(query, k)
            else:
                results = semantic_search_calls(query, k)

        if results.empty:
            st.warning("No results")
        elif mode == "Patients":
            for _, row in results.iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.markdown(f"**{row['name']}** (id: {row['patient_id']})")
                        st.caption(row["summary_text"])
                    with c2:
                        st.metric("Match", f"{row['similarity']:.3f}")
        elif mode == "Emails":
            for _, row in results.iterrows():
                icon = category_icon(row["category"])
                arrow = "📩" if row["direction"] == "inbound" else "📤"
                replied = "✅" if row["is_replied"] else ("⏳ NOT REPLIED" if row["direction"] == "inbound" else "")
                with st.container(border=True):
                    c1, c2 = st.columns([5, 1])
                    with c1:
                        patient_line = f" · 👤 {row['patient_name']}" if row.get("patient_name") else " · 👤 (unmatched)"
                        st.markdown(
                            f"{arrow} {icon} **{row['subject']}** "
                            f"<span style='color: #888;'>· {row['category']}{patient_line} · {replied}</span>",
                            unsafe_allow_html=True,
                        )
                        st.caption(f"From: {row['from_name']}")
                        st.caption(row["body_preview"])
                    with c2:
                        st.metric("Match", f"{row['similarity']:.3f}")
        else:
            for _, row in results.iterrows():
                cat_ic = category_icon(row["category"])
                state_ic = call_state_icon(row["state"])
                arrow = "📥" if row["direction"] == "inbound" else "📤"
                returned = ""
                if row["state"] in ("missed", "voicemail"):
                    returned = "✅ returned" if row["is_returned"] else "⏳ NOT returned"
                with st.container(border=True):
                    c1, c2 = st.columns([5, 1])
                    with c1:
                        patient_line = f" · 👤 {row['patient_name']}" if row.get("patient_name") else " · 👤 (unmatched)"
                        st.markdown(
                            f"{arrow} {state_ic} {cat_ic} **{row['summary'] or row['category']}** "
                            f"<span style='color: #888;'>· {row['category']} · {row['state']}{patient_line} · {returned}</span>",
                            unsafe_allow_html=True,
                        )
                        st.caption(f"From: {row['from_name']}")
                    with c2:
                        st.metric("Match", f"{row['similarity']:.3f}")


# ---------------------------------------------------------------------------
# Tab 3: Emails
# ---------------------------------------------------------------------------

with tab_emails:
    st.subheader("Clinic mailbox")
    st.caption("Simulated `emily@lsd-dental.co.uk` inbox. Filter, browse, click to read.")

    try:
        edf = load_emails_df()
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load emails: {e}")
        edf = pd.DataFrame()

    if edf.empty:
        st.info(
            "No emails yet. Run `python generate_emails.py` then `python embed_emails.py` "
            "to populate the inbox."
        )
    else:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            direction = st.multiselect("Direction", ["inbound", "outbound"])
        with c2:
            category = st.multiselect(
                "Category",
                sorted(edf["category"].dropna().unique().tolist()),
            )
        with c3:
            reply_filter = st.selectbox(
                "Reply status",
                ["All", "Only unreplied inbound", "Only replied", "Only sent by us"],
            )
        with c4:
            match_filter = st.selectbox(
                "Patient match",
                ["All", "Matched only", "Unmatched only"],
            )

        filtered = edf.copy()
        if direction:
            filtered = filtered[filtered["direction"].isin(direction)]
        if category:
            filtered = filtered[filtered["category"].isin(category)]
        if reply_filter == "Only unreplied inbound":
            filtered = filtered[(filtered["direction"] == "inbound") & (filtered["is_replied"] == False)]  # noqa: E712
        elif reply_filter == "Only replied":
            filtered = filtered[filtered["is_replied"] == True]  # noqa: E712
        elif reply_filter == "Only sent by us":
            filtered = filtered[filtered["direction"] == "outbound"]
        if match_filter == "Matched only":
            filtered = filtered[filtered["patient_id"].notna()]
        elif match_filter == "Unmatched only":
            filtered = filtered[filtered["patient_id"].isna()]

        # Headline counts
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Total", f"{len(edf):,}")
        h2.metric("Unreplied inbound", int(((edf["direction"] == "inbound") & (edf["is_replied"] == False)).sum()))  # noqa: E712
        h3.metric("Unmatched", int(edf["patient_id"].isna().sum()))
        h4.metric("Showing", f"{len(filtered):,}")

        st.dataframe(
            filtered[[
                "id", "direction", "category", "from_name", "subject",
                "patient_name", "received_at", "is_replied", "priority",
            ]],
            use_container_width=True,
            height=400,
            hide_index=True,
        )

        st.divider()
        st.subheader("Read email")
        email_id = st.number_input(
            "Enter email ID (from the table above)",
            min_value=1,
            value=int(filtered["id"].iloc[0]) if len(filtered) else 1,
            step=1,
        )
        if st.button("Open email"):
            em = load_email_body(email_id)
            if not em:
                st.warning("Not found")
            else:
                icon = category_icon(em["category"])
                arrow = "📩" if em["direction"] == "inbound" else "📤"
                st.markdown(f"### {arrow} {icon} {em['subject']}")
                cc1, cc2, cc3 = st.columns(3)
                cc1.caption(f"From: **{em['from_name']}** <{em['from_address']}>")
                cc2.caption(f"To: {em['to_address']}")
                cc3.caption(f"Category: {em['category']} · {em['priority']}")
                ts = em.get("received_at") or em.get("sent_at")
                st.caption(f"Date: {ts}")
                if em["is_replied"]:
                    st.success(f"Replied on {em['replied_at']}")
                elif em["direction"] == "inbound":
                    st.warning("Not replied to")
                st.markdown("---")
                st.write(em["body_text"])


# ---------------------------------------------------------------------------
# Tab 4: Calls
# ---------------------------------------------------------------------------

with tab_calls:
    st.subheader("Phone call log")
    st.caption("Simulated bOnline call log. Filter, browse, click a call to read the transcript.")

    try:
        cdf = load_calls_df()
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load calls: {e}")
        cdf = pd.DataFrame()

    if cdf.empty:
        st.info(
            "No calls yet. Run `python generate_calls.py` then `python embed_calls.py` "
            "to populate the call log."
        )
    else:
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            call_direction = st.multiselect("Direction", ["inbound", "outbound"], key="calls_dir")
        with c2:
            call_state = st.multiselect(
                "State",
                ["answered", "missed", "voicemail", "busy", "no_answer"],
                key="calls_state",
            )
        with c3:
            call_category = st.multiselect(
                "Category",
                sorted(cdf["category"].dropna().unique().tolist()),
                key="calls_cat",
            )
        with c4:
            call_filter = st.selectbox(
                "Quick filter",
                ["All", "Missed + not returned", "After-hours missed", "Unmatched (non-patient)", "Emergency"],
                key="calls_quick",
            )

        filtered_calls = cdf.copy()
        if call_direction:
            filtered_calls = filtered_calls[filtered_calls["direction"].isin(call_direction)]
        if call_state:
            filtered_calls = filtered_calls[filtered_calls["state"].isin(call_state)]
        if call_category:
            filtered_calls = filtered_calls[filtered_calls["category"].isin(call_category)]
        if call_filter == "Missed + not returned":
            filtered_calls = filtered_calls[
                (filtered_calls["state"].isin(["missed", "voicemail"])) &
                (filtered_calls["is_returned"] == False)  # noqa: E712
            ]
        elif call_filter == "After-hours missed":
            filtered_calls = filtered_calls[
                (filtered_calls["after_hours"] == True) &  # noqa: E712
                (filtered_calls["state"].isin(["missed", "voicemail", "no_answer"]))
            ]
        elif call_filter == "Unmatched (non-patient)":
            filtered_calls = filtered_calls[filtered_calls["patient_id"].isna()]
        elif call_filter == "Emergency":
            filtered_calls = filtered_calls[filtered_calls["category"] == "emergency"]

        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Total", f"{len(cdf):,}")
        missed_unreturned = int(
            ((cdf["state"].isin(["missed", "voicemail"])) & (cdf["is_returned"] == False)).sum()  # noqa: E712
        )
        h2.metric("Missed + not returned", missed_unreturned)
        h3.metric("Unmatched", int(cdf["patient_id"].isna().sum()))
        h4.metric("Showing", f"{len(filtered_calls):,}")

        display_cols = [
            "id", "direction", "category", "state",
            "from_name", "summary", "patient_name",
            "started_at", "duration_seconds", "is_returned", "priority",
        ]
        st.dataframe(
            filtered_calls[display_cols],
            use_container_width=True,
            height=400,
            hide_index=True,
        )

        st.divider()
        st.subheader("Call detail")
        call_id_in = st.number_input(
            "Enter call ID (from the table above)",
            min_value=1,
            value=int(filtered_calls["id"].iloc[0]) if len(filtered_calls) else 1,
            step=1,
            key="call_detail_input",
        )
        if st.button("Open call", key="open_call_btn"):
            c = load_call_detail(call_id_in)
            if not c:
                st.warning("Not found")
            else:
                state_ic = call_state_icon(c["state"])
                cat_ic = category_icon(c["category"])
                arrow = "📥" if c["direction"] == "inbound" else "📤"
                st.markdown(f"### {arrow} {state_ic} {cat_ic} {c['summary'] or c['category']}")

                cc1, cc2, cc3 = st.columns(3)
                cc1.caption(f"From: **{c['from_name']}** · {c['from_number']}")
                cc2.caption(f"To: {c['to_number']}")
                cc3.caption(f"Category: {c['category']} · {c['priority']}")

                dd1, dd2, dd3, dd4 = st.columns(4)
                dd1.metric("State", c["state"])
                dd2.metric("Duration", fmt_seconds(c["duration_seconds"]))
                dd3.metric("Ring", fmt_seconds(c["ring_seconds"]))
                dd4.metric("After hours", "Yes" if c["after_hours"] else "No")

                st.caption(f"Started: {c['started_at']} · Agent: {c['agent_name'] or '—'}")
                if c.get("patient_name"):
                    st.caption(f"Matched patient: {c['patient_name']} (via {c['match_method']})")
                else:
                    st.caption("Unmatched — not in patient database")

                if c["state"] in ("missed", "voicemail"):
                    if c["is_returned"]:
                        st.success(f"Returned on {c['returned_at']}")
                    else:
                        st.warning("Never returned — lost revenue signal")

                st.markdown("---")
                if c["transcript"]:
                    st.markdown("**Transcript**")
                    st.code(c["transcript"], language=None)
                else:
                    st.caption("(No transcript — caller did not connect)")


# ---------------------------------------------------------------------------
# Tab 5: Lost Revenue
# ---------------------------------------------------------------------------

with tab_revenue:
    st.subheader("Lost revenue analysis")
    st.caption("Where the practice is leaking money. Numbers are live from the database.")

    with st.spinner("Calculating..."):
        m = lost_revenue_metrics()

    c1, c2, c3 = st.columns(3)
    c1.metric("Annual revenue", f"£{m['annual_revenue']:,.0f}")
    c2.metric("Estimated lost revenue", f"£{m['total_lost_estimate']:,.0f}")
    c3.metric("Avg appointment value", f"£{m['avg_appointment_value']:.0f}")

    st.divider()
    st.markdown("### Clinical leaks")

    g1, g2 = st.columns(2)
    with g1:
        st.metric(
            "Overdue patients",
            f"{m['overdue_patients']:,}",
            help="Active patients with no future appointment and last visit 6+ months ago",
        )
        st.caption(f"Estimated value: £{m['overdue_value_estimate']:,.0f}")

        st.metric("No-shows (12mo)", f"{m['fta_count']:,}")
        st.caption(f"Estimated value: £{m['fta_value_estimate']:,.0f}")

        st.metric("Cancellations (12mo)", f"{m['cancelled_count']:,}")
        st.caption(f"Estimated value: £{m['cancelled_value_estimate']:,.0f}")

        st.metric("NHS claims with problems", f"{m['nhs_claim_problems']:,}")

    with g2:
        st.metric(
            "Uncompleted treatment plan items",
            f"{m['uncompleted_tp_items']:,}",
            help="Treatment items that were planned but never completed",
        )
        st.caption(f"Value: £{m['uncompleted_tp_value']:,.0f}")

        st.metric("Unpaid invoices", f"{m['unpaid_invoices']:,}")
        st.caption(f"Outstanding: £{m['unpaid_total']:,.0f}")

        st.metric("Missed/unbooked recalls", f"{m['missed_recalls']:,}")

        st.metric("Churned patients", f"{m['churned_patients']:,}")
        st.caption(f"LTV loss estimate: £{m['churn_value_estimate']:,.0f}")

    # Email leaks (only if emails exist)
    if m.get("total_inbound_emails", 0) > 0:
        st.divider()
        st.markdown("### 📧 Email leaks")
        e1, e2 = st.columns(2)
        with e1:
            st.metric(
                "Unreplied treatment inquiries",
                f"{m['unreplied_treatment_inquiries']:,}",
                help="High-value inquiries (implants, Invisalign, crowns) never answered",
            )
            st.caption(f"Est. lost value: £{m['unreplied_tx_value']:,.0f}")

            st.metric("Unanswered complaints", f"{m['unreplied_complaints']:,}")
            st.caption(f"Churn risk value: £{m['complaint_churn_value']:,.0f}")
        with e2:
            st.metric(
                "Unmatched leads (non-patients)",
                f"{m['unmatched_leads']:,}",
                help="Emails from people not yet in the patient DB",
            )
            st.caption(f"Est. acquisition value: £{m['unmatched_lead_value']:,.0f}")

            st.metric("Unreplied inbound (all)", f"{m['unreplied_inbound']:,}")
            st.caption(f"Avg reply time: {m['avg_reply_hours']:.1f} hours")

    # Call leaks (only if calls exist)
    if m.get("total_inbound_calls", 0) > 0:
        st.divider()
        st.markdown("### 📞 Phone call leaks")
        k1, k2 = st.columns(2)
        with k1:
            st.metric(
                "Missed treatment-inquiry calls",
                f"{m['unreturned_treatment_calls']:,}",
                help="Golden leads — implant/Invisalign/crown questions that rang out",
            )
            st.caption(f"Est. lost value: £{m['unreturned_tx_calls_value']:,.0f}")

            st.metric("Missed calls never returned", f"{m['missed_not_returned']:,}")
            st.caption(f"Est. lost value: £{m['missed_returnless_value']:,.0f}")

            st.metric("Missed EMERGENCY calls", f"{m['unreturned_emergency_calls']:,}",
                      help="High-risk, often severe pain or trauma")
        with k2:
            st.metric(
                "Unmatched inbound calls",
                f"{m['unmatched_inbound_calls']:,}",
                help="Callers whose number is not in the patient DB",
            )
            st.caption(f"Est. acquisition value: £{m['unmatched_calls_value']:,.0f}")

            st.metric("After-hours calls missed", f"{m['after_hours_missed']:,}")
            st.caption("Consider an after-hours answering service")

            st.metric("Avg ring time (answered)", f"{m['avg_ring_seconds']:.1f}s")

    st.divider()
    st.markdown("### AI analysis")
    if st.button("Ask Claude what to do about it", type="primary"):
        with st.spinner("Claude is thinking..."):
            narrative = claude_lost_revenue_narrative(m)
        st.markdown(narrative)


# ---------------------------------------------------------------------------
# Tab 6: Case study generator
# ---------------------------------------------------------------------------

with tab_case_study:
    st.subheader("Patient case study")
    st.caption(
        "Picks a real patient with a juicy lost-revenue story and asks Claude to "
        "write it up as a sales case study. The best demo asset in the app."
    )

    try:
        top_candidates = list_candidates(limit=15)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load candidates: {e}")
        top_candidates = []

    if not top_candidates:
        st.info(
            "No lost-revenue candidates found yet. Make sure you've run "
            "`generate_data.py`, `generate_emails.py`, and `generate_calls.py`."
        )
    else:
        st.markdown("**Top 15 patients by estimated lost value:**")
        cand_df = pd.DataFrame([
            {
                "id": c["patient_id"],
                "name": c["name"],
                "lost_value": f"£{float(c['lost_value']):,.0f}",
                "unpaid": f"£{float(c['unpaid_value']):,.0f}",
                "uncompleted_tp": f"£{float(c['uncompleted_value']):,.0f}",
                "tx_email_unreplied": c["tx_email_count"],
                "tx_call_missed": c["tx_call_count"],
                "missed_calls": c["missed_call_count"],
            }
            for c in top_candidates
        ])
        st.dataframe(cand_df, use_container_width=True, hide_index=True, height=300)

        st.divider()
        c_left, c_right = st.columns([1, 3])
        with c_left:
            if st.button("🎲 Pick a random story", type="primary"):
                cand = pick_candidate(randomize=True)
                if cand:
                    st.session_state["case_study_pid"] = cand["patient_id"]
                    st.session_state.pop("case_study_text", None)
        with c_right:
            manual_id = st.number_input(
                "…or enter a specific patient id",
                min_value=1,
                value=int(top_candidates[0]["patient_id"]),
                step=1,
            )
            if st.button("Use this patient"):
                st.session_state["case_study_pid"] = int(manual_id)
                st.session_state.pop("case_study_text", None)

        pid = st.session_state.get("case_study_pid")
        if pid:
            st.info(f"Building case study for patient **{pid}** …")
            if "case_study_text" not in st.session_state:
                with st.spinner("Claude is writing the narrative..."):
                    try:
                        st.session_state["case_study_text"] = generate_case_study(pid)
                    except Exception as e:  # noqa: BLE001
                        st.session_state["case_study_text"] = f"Error: {e}"
            st.markdown("---")
            st.markdown(st.session_state["case_study_text"])
            if st.button("🔁 Regenerate"):
                st.session_state.pop("case_study_text", None)
                st.rerun()


# ---------------------------------------------------------------------------
# Tab 7: Action queues (with Claude-drafted messages)
# ---------------------------------------------------------------------------

with tab_actions:
    from lib.actions import (
        callback_queue,
        draft_message,
        invoice_queue,
        recall_queue,
    )

    st.subheader("Action queues")
    st.caption(
        "Three daily work lists with AI-drafted personalized messages. "
        "Click **Draft message** to generate, then simulate **Approve & send**."
    )

    # Init session store for drafts and sent items
    if "action_drafts" not in st.session_state:
        st.session_state["action_drafts"] = {}
    if "action_sent" not in st.session_state:
        st.session_state["action_sent"] = set()

    queue_tab1, queue_tab2, queue_tab3 = st.tabs([
        "🔔 Recall outreach",
        "📞 Missed callbacks",
        "💷 Invoice chasing",
    ])

    def render_queue(queue_name: str, rows: list[dict], context_builder):
        if not rows:
            st.info("Queue is empty — nothing to do here!")
            return
        st.caption(f"{len(rows)} items in queue. Top priority first.")
        for i, row in enumerate(rows):
            key = f"{queue_name}:{row['action_id']}"
            sent = key in st.session_state["action_sent"]
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(f"**{row['patient_name']}** — {row['headline']}")
                    st.caption(row["detail"])
                with c2:
                    if sent:
                        st.success("✅ Sent")
                    else:
                        if st.button("✍️ Draft", key=f"draft_{key}"):
                            with st.spinner("Claude drafting…"):
                                ctx = context_builder(row)
                                msg = draft_message(queue_name, row, ctx)
                                st.session_state["action_drafts"][key] = msg
                                st.rerun()
                if key in st.session_state["action_drafts"] and not sent:
                    st.text_area(
                        "Draft message",
                        value=st.session_state["action_drafts"][key],
                        height=160,
                        key=f"msg_{key}",
                    )
                    bc1, bc2, _ = st.columns([1, 1, 3])
                    with bc1:
                        if st.button("✅ Approve & send", key=f"send_{key}", type="primary"):
                            st.session_state["action_sent"].add(key)
                            st.rerun()
                    with bc2:
                        if st.button("🔁 Redraft", key=f"redraft_{key}"):
                            del st.session_state["action_drafts"][key]
                            st.rerun()

    with queue_tab1:
        st.markdown("### Patients overdue for recall")
        st.caption(
            "Active patients with no future appointment whose last visit was 6+ months ago. "
            "Prioritised by time since last visit."
        )
        try:
            recalls = recall_queue(limit=15)
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not load recall queue: {e}")
            recalls = []

        def recall_ctx(row):
            return (
                f"{row['patient_name']} last visited on {row['last_visit']} "
                f"({row['months_since']} months ago). "
                f"Dentist: {row.get('dentist') or 'the clinic'}. "
                f"They are on the {row.get('payment_plan') or 'standard'} plan."
            )
        render_queue("recall", recalls, recall_ctx)

    with queue_tab2:
        st.markdown("### Missed inbound calls never returned")
        st.caption(
            "Missed or voicemail calls from patients (or unknown numbers) that "
            "were never called back. Treatment inquiries and emergencies first."
        )
        try:
            callbacks = callback_queue(limit=15)
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not load callback queue: {e}")
            callbacks = []

        def callback_ctx(row):
            return (
                f"Call from {row['from_name']} at {row['started_at']}. "
                f"Category: {row['category']}. "
                f"Summary: {row['summary'] or '—'}. "
                f"{'Left a voicemail.' if row['state'] == 'voicemail' else 'Call was missed.'}"
            )
        render_queue("callback", callbacks, callback_ctx)

    with queue_tab3:
        st.markdown("### Invoices to chase")
        st.caption(
            "Unpaid invoices, biggest first. Claude will draft a polite but firm "
            "collections message."
        )
        try:
            invoices = invoice_queue(limit=15)
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not load invoice queue: {e}")
            invoices = []

        def invoice_ctx(row):
            return (
                f"{row['patient_name']} has an unpaid invoice of £{row['amount_outstanding']:,.2f} "
                f"(ref {row['reference']}) issued on {row['dated_on']}, due on {row['due_on']}. "
                f"Currently {row['days_overdue']} days overdue."
            )
        render_queue("invoice", invoices, invoice_ctx)
