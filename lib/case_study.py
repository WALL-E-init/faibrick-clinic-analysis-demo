"""Case study generator.

Picks a real fake patient with a juicy lost-revenue story, builds their full
context (timeline + financials), and asks Claude Sonnet to write a narrative
case study suitable for a sales deck.

Exposes:
    pick_candidate()        — returns a dict with patient_id + lost_value + reason
    list_candidates(limit)  — returns top N candidates ordered by estimated lost value
    build_case_study(pid)   — runs the full pipeline, returns a markdown narrative
"""

from __future__ import annotations

import random
from typing import Any

from anthropic import Anthropic

from lib.db import ANTHROPIC_API_KEY, get_conn
from lib.timeline import get_patient_timeline


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def list_candidates(limit: int = 20) -> list[dict[str, Any]]:
    """Return a ranked list of patients with the biggest lost-revenue stories.

    Score = sum of:
      • unpaid invoice outstanding amount
      • uncompleted treatment plan item value
      • 1500 per unreplied treatment inquiry email
      • 1500 per missed/unreturned treatment inquiry call
      • 800 per missed+not-returned call in general
    """
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH
            unpaid AS (
                SELECT patient_id, COALESCE(SUM(amount_outstanding), 0) AS v
                FROM invoices
                WHERE paid = FALSE
                GROUP BY patient_id
            ),
            uncompleted AS (
                SELECT patient_id, COALESCE(SUM(price), 0) AS v
                FROM treatment_plan_items
                WHERE completed = FALSE AND price > 0
                GROUP BY patient_id
            ),
            tx_email AS (
                SELECT patient_id, COUNT(*) AS n
                FROM emails
                WHERE direction = 'inbound'
                  AND category = 'treatment_inquiry'
                  AND is_replied = FALSE
                  AND patient_id IS NOT NULL
                GROUP BY patient_id
            ),
            tx_call AS (
                SELECT patient_id, COUNT(*) AS n
                FROM calls
                WHERE direction = 'inbound'
                  AND category = 'treatment_inquiry'
                  AND state IN ('missed', 'voicemail')
                  AND is_returned = FALSE
                  AND patient_id IS NOT NULL
                GROUP BY patient_id
            ),
            missed_call AS (
                SELECT patient_id, COUNT(*) AS n
                FROM calls
                WHERE direction = 'inbound'
                  AND state IN ('missed', 'voicemail')
                  AND is_returned = FALSE
                  AND patient_id IS NOT NULL
                GROUP BY patient_id
            )
            SELECT
                p.id AS patient_id,
                p.first_name || ' ' || p.last_name AS name,
                COALESCE(unpaid.v, 0) AS unpaid_value,
                COALESCE(uncompleted.v, 0) AS uncompleted_value,
                COALESCE(tx_email.n, 0) AS tx_email_count,
                COALESCE(tx_call.n, 0) AS tx_call_count,
                COALESCE(missed_call.n, 0) AS missed_call_count,
                (
                    COALESCE(unpaid.v, 0)
                  + COALESCE(uncompleted.v, 0)
                  + COALESCE(tx_email.n, 0) * 1500
                  + COALESCE(tx_call.n, 0) * 1500
                  + COALESCE(missed_call.n, 0) * 800
                ) AS lost_value
            FROM patients p
            LEFT JOIN unpaid      ON unpaid.patient_id = p.id
            LEFT JOIN uncompleted ON uncompleted.patient_id = p.id
            LEFT JOIN tx_email    ON tx_email.patient_id = p.id
            LEFT JOIN tx_call     ON tx_call.patient_id = p.id
            LEFT JOIN missed_call ON missed_call.patient_id = p.id
            WHERE (
                COALESCE(unpaid.v, 0)
              + COALESCE(uncompleted.v, 0)
              + COALESCE(tx_email.n, 0)
              + COALESCE(tx_call.n, 0)
              + COALESCE(missed_call.n, 0)
            ) > 0
            ORDER BY lost_value DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return rows


def pick_candidate(randomize: bool = True) -> dict[str, Any] | None:
    """Pick one candidate. By default, random pick from top 10 for demo variety."""
    candidates = list_candidates(limit=10)
    if not candidates:
        return None
    if randomize:
        return random.choice(candidates)
    return candidates[0]


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _patient_header(cur, patient_id: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT
            p.id,
            p.title,
            p.first_name || ' ' || p.last_name AS name,
            p.date_of_birth,
            p.email_address,
            p.mobile_phone,
            p.active,
            p.medical_alert,
            p.medical_alert_text,
            pp.name AS payment_plan,
            pr.user_first_name || ' ' || pr.user_last_name AS dentist,
            s.first_appointment_date,
            s.last_appointment_date,
            s.next_appointment_date,
            s.total_invoiced,
            s.total_paid
        FROM patients p
        LEFT JOIN payment_plans pp ON pp.id = p.payment_plan_id
        LEFT JOIN practitioners pr ON pr.id = p.dentist_id
        LEFT JOIN patient_stats s  ON s.patient_id = p.id
        WHERE p.id = %s
        """,
        (patient_id,),
    )
    return dict(cur.fetchone() or {})


def build_context_text(patient_id: int) -> str:
    """Build the full plain-text patient context for Claude."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        header = _patient_header(cur, patient_id)
        if not header:
            return ""

        # Lost-value breakdown
        cur.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(amount_outstanding), 0) AS total
            FROM invoices WHERE patient_id = %s AND paid = FALSE
            """,
            (patient_id,),
        )
        unpaid = cur.fetchone()

        cur.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(price), 0) AS total
            FROM treatment_plan_items
            WHERE patient_id = %s AND completed = FALSE AND price > 0
            """,
            (patient_id,),
        )
        uncompleted = cur.fetchone()

        # Treatment plan item details (top 5 by price)
        cur.execute(
            """
            SELECT nomenclature, price, created_at
            FROM treatment_plan_items
            WHERE patient_id = %s AND completed = FALSE AND price > 0
            ORDER BY price DESC
            LIMIT 5
            """,
            (patient_id,),
        )
        tp_items = [dict(r) for r in cur.fetchall()]

    # Timeline: last 25 events
    timeline = get_patient_timeline(patient_id)[:25]

    lines: list[str] = []
    lines.append(f"PATIENT: {header.get('title') or ''} {header['name']} (id: {header['id']})")
    lines.append(f"DOB: {header.get('date_of_birth')}")
    lines.append(f"Dentist: {header.get('dentist') or '—'}")
    lines.append(f"Payment plan: {header.get('payment_plan') or '—'}")
    lines.append(f"First visit: {header.get('first_appointment_date') or '—'}")
    lines.append(f"Last visit: {header.get('last_appointment_date') or '—'}")
    lines.append(f"Next booked: {header.get('next_appointment_date') or 'NONE'}")
    lines.append(f"Active: {header.get('active')}")
    if header.get("medical_alert"):
        lines.append(f"Medical alert: {header.get('medical_alert_text') or 'yes'}")
    lines.append(
        f"Lifetime invoiced: £{float(header.get('total_invoiced') or 0):,.2f} · "
        f"paid: £{float(header.get('total_paid') or 0):,.2f}"
    )

    lines.append("")
    lines.append("LOST REVENUE BREAKDOWN:")
    lines.append(
        f"- Unpaid invoices: {unpaid['n']} totalling £{float(unpaid['total'] or 0):,.2f}"
    )
    lines.append(
        f"- Uncompleted treatment plan items: {uncompleted['n']} "
        f"worth £{float(uncompleted['total'] or 0):,.2f}"
    )
    if tp_items:
        lines.append("  Top uncompleted items:")
        for it in tp_items:
            lines.append(f"    • {it['nomenclature']} — £{float(it['price'] or 0):,.2f}")

    lines.append("")
    lines.append("RECENT TIMELINE (newest first, max 25 events):")
    for ev in timeline:
        ts = ev["ts"].strftime("%Y-%m-%d")
        lines.append(f"  [{ts}] {ev['icon']} {ev['title']} — {ev['subtitle']}")
        if ev.get("meta"):
            snippet = ev["meta"][:140]
            lines.append(f"       ↳ {snippet}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude case study
# ---------------------------------------------------------------------------

CASE_STUDY_SYSTEM_PROMPT = """You are a dental practice revenue consultant writing a patient case study for a sales deck.

You will be given one real patient's full context from London Specialist Dentists. Your job:

1. Write a short, punchy narrative case study (under 350 words)
2. Structure: **The patient** → **What went wrong** → **What it cost the practice** → **What Emily BI would have caught**
3. Be specific with dates, amounts, treatment names. NO generic language.
4. Every claim must tie back to a real event in the timeline
5. Use £ symbol for money
6. End with a one-line "Moral of the story" that a practice owner will remember
7. Markdown formatting is fine. Use bold for section headers.
8. Do NOT disclaim or hedge. This is a demonstration using simulated data — write it like it's real."""


def generate_case_study(patient_id: int) -> str:
    """Full pipeline: build context, call Claude, return narrative markdown."""
    context = build_context_text(patient_id)
    if not context:
        return f"(No patient found with id {patient_id})"

    if not ANTHROPIC_API_KEY:
        return (
            "(Anthropic API key missing — set ANTHROPIC_API_KEY in .env to enable "
            "case study generation)\n\n"
            f"Raw context:\n\n```\n{context}\n```"
        )

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=CASE_STUDY_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                "Write the case study now. Here is the patient data:\n\n" + context
            ),
        }],
    )
    return response.content[0].text
