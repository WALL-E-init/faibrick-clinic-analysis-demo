"""Action queues with Claude-drafted personalized messages.

Three queues:
    - recall_queue()    — patients overdue for a checkup
    - callback_queue()  — missed inbound calls never returned
    - invoice_queue()   — unpaid invoices to chase

Each row is a plain dict with an `action_id` (stable key for session state),
a `patient_name`, a short `headline`, a longer `detail`, plus any fields the
context builder needs to prompt Claude for a personalised message.

`draft_message(queue_name, row, context_text)` calls Claude Haiku to produce
a short, warm, sendable message tailored to the row.
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic

from lib.db import ANTHROPIC_API_KEY, get_conn


# ---------------------------------------------------------------------------
# Queue builders
# ---------------------------------------------------------------------------

def recall_queue(limit: int = 15) -> list[dict[str, Any]]:
    """Active patients with no future appointment, last visit 6+ months ago."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                p.id AS patient_id,
                p.first_name || ' ' || p.last_name AS patient_name,
                p.email_address,
                p.mobile_phone,
                pp.name AS payment_plan,
                pr.user_first_name || ' ' || pr.user_last_name AS dentist,
                s.last_appointment_date AS last_visit,
                (CURRENT_DATE - s.last_appointment_date) / 30 AS months_since
            FROM patients p
            LEFT JOIN patient_stats s ON s.patient_id = p.id
            LEFT JOIN payment_plans pp ON pp.id = p.payment_plan_id
            LEFT JOIN practitioners pr ON pr.id = p.dentist_id
            WHERE p.active = TRUE
              AND (s.next_appointment_date IS NULL OR s.next_appointment_date < CURRENT_DATE)
              AND s.last_appointment_date IS NOT NULL
              AND s.last_appointment_date < CURRENT_DATE - INTERVAL '6 months'
            ORDER BY s.last_appointment_date ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "action_id": f"recall-{r['patient_id']}",
            "patient_name": r["patient_name"],
            "headline": f"Overdue for recall — last visit {r['last_visit']}",
            "detail": (
                f"{r['months_since']} months since last visit · "
                f"dentist: {r['dentist'] or '—'} · plan: {r['payment_plan'] or '—'}"
            ),
            **r,
        })
    return out


def callback_queue(limit: int = 15) -> list[dict[str, Any]]:
    """Missed or voicemail inbound calls never returned, prioritised by category."""
    try:
        with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id AS call_id,
                    c.from_name,
                    c.from_number,
                    c.started_at,
                    c.state,
                    c.category,
                    c.summary,
                    c.priority,
                    c.patient_id,
                    COALESCE(p.first_name || ' ' || p.last_name, c.from_name) AS patient_name
                FROM calls c
                LEFT JOIN patients p ON p.id = c.patient_id
                WHERE c.direction = 'inbound'
                  AND c.state IN ('missed', 'voicemail')
                  AND c.is_returned = FALSE
                ORDER BY
                    CASE c.category
                        WHEN 'emergency'         THEN 1
                        WHEN 'treatment_inquiry' THEN 2
                        WHEN 'complaint'         THEN 3
                        WHEN 'appointment_inquiry' THEN 4
                        ELSE 5
                    END,
                    c.started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        is_vm = r["state"] == "voicemail"
        out.append({
            "action_id": f"callback-{r['call_id']}",
            "patient_name": r["patient_name"] or "Unknown caller",
            "headline": (
                f"{'Voicemail' if is_vm else 'Missed call'} — {r['category']}"
            ),
            "detail": (
                f"{r['started_at'].strftime('%Y-%m-%d %H:%M') if r.get('started_at') else ''} · "
                f"priority: {r['priority']} · "
                f"{r['summary'] or '(no summary)'}"
            ),
            **r,
        })
    return out


def invoice_queue(limit: int = 15) -> list[dict[str, Any]]:
    """Unpaid invoices, biggest first."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                i.id AS invoice_id,
                i.reference,
                i.amount,
                i.amount_outstanding,
                i.dated_on,
                i.due_on,
                (CURRENT_DATE - i.due_on) AS days_overdue,
                i.patient_id,
                p.first_name || ' ' || p.last_name AS patient_name,
                p.email_address
            FROM invoices i
            JOIN patients p ON p.id = i.patient_id
            WHERE i.paid = FALSE
              AND i.amount_outstanding > 0
            ORDER BY i.amount_outstanding DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    out: list[dict[str, Any]] = []
    for r in rows:
        r["amount_outstanding"] = float(r["amount_outstanding"] or 0)
        r["amount"] = float(r["amount"] or 0)
        overdue_days = r["days_overdue"] or 0
        out.append({
            "action_id": f"invoice-{r['invoice_id']}",
            "patient_name": r["patient_name"],
            "headline": (
                f"£{r['amount_outstanding']:,.2f} outstanding · ref {r['reference']}"
            ),
            "detail": (
                f"Issued {r['dated_on']} · due {r['due_on']} · "
                f"{overdue_days} days overdue"
            ),
            **r,
        })
    return out


# ---------------------------------------------------------------------------
# Claude message drafter
# ---------------------------------------------------------------------------

_SYSTEM_PROMPTS: dict[str, str] = {
    "recall": """You draft short, friendly recall outreach messages from London Specialist Dentists.

Rules:
- Under 100 words
- Warm but not saccharine
- Mention how long it's been since their last visit
- Offer to book a checkup with their usual dentist
- Sign off as "The team at London Specialist Dentists"
- Plain text only. No bullet points, no headings, no emojis.
- Do NOT include a subject line.
- Write it as if it's going by SMS — conversational and short.""",

    "callback": """You draft short, warm scripts for a receptionist to use when returning a missed patient call.

Rules:
- Under 90 words
- This is a voicemail script OR an SMS follow-up
- Apologise briefly that the call was missed
- Reference the reason for their call (if known)
- Offer a clear next step (call us back or we'll ring at a given time)
- No corporate speak. Sound human.
- Plain text only. No bullet points, no headings, no emojis.
- Do NOT include a subject line.""",

    "invoice": """You draft polite but firm invoice reminder messages from London Specialist Dentists.

Rules:
- Under 110 words
- Friendly, not aggressive
- State the exact amount, invoice reference, and days overdue
- Offer payment options (online, phone, in person)
- Offer to discuss if there's a problem
- Sign off as "Accounts team, London Specialist Dentists"
- Plain text only. No bullet points, no headings, no emojis.
- Do NOT include a subject line.""",
}


def draft_message(queue_name: str, row: dict[str, Any], context_text: str) -> str:
    """Ask Claude Haiku to write the message for this specific row."""
    if not ANTHROPIC_API_KEY:
        return "(Anthropic API key missing — set ANTHROPIC_API_KEY in .env to enable AI drafting)"

    system_prompt = _SYSTEM_PROMPTS.get(queue_name)
    if not system_prompt:
        return f"(No draft prompt configured for queue: {queue_name})"

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                "Write the message now. Here is the context for this specific patient:\n\n"
                f"{context_text}\n\n"
                "Output only the message body — no preface, no explanation."
            ),
        }],
    )
    return response.content[0].text.strip()
