"""Lost revenue analysis — SQL aggregates + Claude-powered recommendations."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from anthropic import Anthropic

from lib.db import ANTHROPIC_API_KEY, get_conn


# ---------------------------------------------------------------------------
# SQL aggregates — fast, deterministic
# ---------------------------------------------------------------------------

def lost_revenue_metrics() -> dict[str, Any]:
    """Run all lost revenue queries in one round trip and return a dict."""
    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        # 1. Patients overdue for recall
        cur.execute("""
            SELECT COUNT(*) AS n
            FROM patients p
            LEFT JOIN patient_stats s ON s.patient_id = p.id
            WHERE p.active = TRUE
              AND (s.next_appointment_date IS NULL OR s.next_appointment_date < CURRENT_DATE)
              AND s.last_appointment_date < CURRENT_DATE - INTERVAL '6 months'
        """)
        overdue_patients = cur.fetchone()["n"]

        # 2. FTA appointments (last 12 months)
        cur.execute("""
            SELECT COUNT(*) AS n, COALESCE(AVG(duration), 0) AS avg_duration
            FROM appointments
            WHERE state = 'Did not attend'
              AND start_time > CURRENT_DATE - INTERVAL '12 months'
        """)
        fta_row = cur.fetchone()
        fta_count = fta_row["n"]
        fta_avg_duration = float(fta_row["avg_duration"] or 0)

        # 3. Cancelled appointments (last 12 months)
        cur.execute("""
            SELECT COUNT(*) AS n
            FROM appointments
            WHERE state = 'Cancelled'
              AND start_time > CURRENT_DATE - INTERVAL '12 months'
        """)
        cancelled_count = cur.fetchone()["n"]

        # 4. Uncompleted treatment plan items (lost treatment value)
        cur.execute("""
            SELECT COUNT(*) AS n, COALESCE(SUM(price), 0) AS total_value
            FROM treatment_plan_items
            WHERE completed = FALSE AND price > 0
        """)
        row = cur.fetchone()
        uncompleted_items = row["n"]
        uncompleted_value = float(row["total_value"])

        # 5. Unpaid invoices
        cur.execute("""
            SELECT COUNT(*) AS n, COALESCE(SUM(amount_outstanding), 0) AS total
            FROM invoices
            WHERE paid = FALSE
        """)
        row = cur.fetchone()
        unpaid_count = row["n"]
        unpaid_total = float(row["total"])

        # 6. Missed recalls
        cur.execute("""
            SELECT COUNT(*) AS n
            FROM recalls
            WHERE status IN ('Missed', 'Unbooked')
        """)
        missed_recalls = cur.fetchone()["n"]

        # 7. Churned patients
        cur.execute("""
            SELECT COUNT(*) AS n
            FROM patients
            WHERE active = FALSE
        """)
        churned = cur.fetchone()["n"]

        # 8. NHS claim problems
        cur.execute("""
            SELECT COUNT(*) AS n
            FROM nhs_claims
            WHERE claim_status IN ('error', 'queried', 'invalid', 'withdrawn')
        """)
        nhs_problems = cur.fetchone()["n"]

        # 9. Total revenue (last 12 months)
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM invoices
            WHERE dated_on > CURRENT_DATE - INTERVAL '12 months'
        """)
        annual_revenue = float(cur.fetchone()["total"])

        # 10. Average appointment value (proxy from invoice items)
        cur.execute("""
            SELECT COALESCE(AVG(total_price), 0) AS avg_price
            FROM invoice_items
        """)
        avg_appt_value = float(cur.fetchone()["avg_price"])

        # ---- Call-based signals (only if calls table has data) ----
        call_metrics = {
            "total_calls": 0,
            "total_inbound_calls": 0,
            "missed_not_returned": 0,
            "voicemails_not_returned": 0,
            "after_hours_missed": 0,
            "unmatched_inbound_calls": 0,
            "unreturned_treatment_calls": 0,
            "unreturned_emergency_calls": 0,
            "avg_ring_seconds": 0.0,
        }
        try:
            cur.execute("SELECT COUNT(*) AS n FROM calls")
            call_metrics["total_calls"] = cur.fetchone()["n"]

            if call_metrics["total_calls"] > 0:
                cur.execute("""
                    SELECT COUNT(*) AS n FROM calls WHERE direction = 'inbound'
                """)
                call_metrics["total_inbound_calls"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM calls
                    WHERE direction = 'inbound'
                      AND state = 'missed'
                      AND is_returned = FALSE
                """)
                call_metrics["missed_not_returned"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM calls
                    WHERE direction = 'inbound'
                      AND state = 'voicemail'
                      AND is_returned = FALSE
                """)
                call_metrics["voicemails_not_returned"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM calls
                    WHERE direction = 'inbound'
                      AND after_hours = TRUE
                      AND state IN ('missed', 'voicemail', 'no_answer')
                      AND is_returned = FALSE
                """)
                call_metrics["after_hours_missed"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM calls
                    WHERE direction = 'inbound'
                      AND patient_id IS NULL
                """)
                call_metrics["unmatched_inbound_calls"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM calls
                    WHERE direction = 'inbound'
                      AND category = 'treatment_inquiry'
                      AND state IN ('missed', 'voicemail')
                      AND is_returned = FALSE
                """)
                call_metrics["unreturned_treatment_calls"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM calls
                    WHERE direction = 'inbound'
                      AND category = 'emergency'
                      AND state IN ('missed', 'voicemail')
                      AND is_returned = FALSE
                """)
                call_metrics["unreturned_emergency_calls"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COALESCE(AVG(ring_seconds), 0) AS s
                    FROM calls
                    WHERE direction = 'inbound' AND state = 'answered'
                """)
                call_metrics["avg_ring_seconds"] = float(cur.fetchone()["s"] or 0)
        except Exception:  # noqa: BLE001
            pass

        # ---- Email-based signals (only if emails table has data) ----
        email_metrics = {
            "unreplied_inbound": 0,
            "unreplied_treatment_inquiries": 0,
            "unreplied_complaints": 0,
            "unmatched_leads": 0,
            "avg_reply_hours": 0.0,
            "total_inbound": 0,
        }
        try:
            cur.execute("SELECT COUNT(*) AS n FROM emails WHERE direction = 'inbound'")
            email_metrics["total_inbound"] = cur.fetchone()["n"]

            if email_metrics["total_inbound"] > 0:
                cur.execute("""
                    SELECT COUNT(*) AS n FROM emails
                    WHERE direction = 'inbound' AND is_replied = FALSE
                """)
                email_metrics["unreplied_inbound"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM emails
                    WHERE direction = 'inbound'
                      AND category = 'treatment_inquiry'
                      AND is_replied = FALSE
                """)
                email_metrics["unreplied_treatment_inquiries"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM emails
                    WHERE direction = 'inbound'
                      AND category = 'complaint'
                      AND is_replied = FALSE
                """)
                email_metrics["unreplied_complaints"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COUNT(*) AS n FROM emails
                    WHERE direction = 'inbound'
                      AND patient_id IS NULL
                """)
                email_metrics["unmatched_leads"] = cur.fetchone()["n"]

                cur.execute("""
                    SELECT COALESCE(
                        AVG(EXTRACT(EPOCH FROM (replied_at - received_at)) / 3600.0),
                        0
                    ) AS h
                    FROM emails
                    WHERE direction = 'inbound' AND is_replied = TRUE
                """)
                email_metrics["avg_reply_hours"] = float(cur.fetchone()["h"] or 0)
        except Exception:  # noqa: BLE001
            pass  # emails table not populated yet

    # Estimate lost revenue
    # Assumption: each FTA / cancelled appointment = lost avg appointment value
    fta_value = fta_count * avg_appt_value
    cancelled_value = cancelled_count * avg_appt_value
    overdue_value = overdue_patients * avg_appt_value
    churn_value = churned * avg_appt_value * 6  # rough LTV loss

    # Email-based value estimates
    # Treatment inquiries are HIGH value (implants, Invisalign = £2500+)
    unreplied_tx_value = email_metrics["unreplied_treatment_inquiries"] * 1500
    # Unmatched leads could be new patients (LTV = ~12 appointments over years)
    unmatched_lead_value = email_metrics["unmatched_leads"] * avg_appt_value * 3
    # Unreplied complaints = churn risk (6 future visits lost)
    complaint_churn_value = email_metrics["unreplied_complaints"] * avg_appt_value * 6

    email_lost_total = unreplied_tx_value + unmatched_lead_value + complaint_churn_value

    # Call-based value estimates
    # Missed treatment-inquiry calls are the golden ones — £1500 LTV each
    unreturned_tx_calls_value = call_metrics["unreturned_treatment_calls"] * 1500
    # Unmatched inbound calls = potential new patients (LTV)
    unmatched_calls_value = call_metrics["unmatched_inbound_calls"] * avg_appt_value * 3
    # General missed + not returned inbound = one lost appointment each
    missed_returnless_value = (
        call_metrics["missed_not_returned"] + call_metrics["voicemails_not_returned"]
    ) * avg_appt_value
    calls_lost_total = unreturned_tx_calls_value + unmatched_calls_value + missed_returnless_value

    total_lost_estimate = (
        fta_value + cancelled_value + uncompleted_value + unpaid_total
        + overdue_value + churn_value + email_lost_total + calls_lost_total
    )

    return {
        "annual_revenue": annual_revenue,
        "avg_appointment_value": avg_appt_value,
        "overdue_patients": overdue_patients,
        "overdue_value_estimate": overdue_value,
        "fta_count": fta_count,
        "fta_value_estimate": fta_value,
        "fta_avg_duration": fta_avg_duration,
        "cancelled_count": cancelled_count,
        "cancelled_value_estimate": cancelled_value,
        "uncompleted_tp_items": uncompleted_items,
        "uncompleted_tp_value": uncompleted_value,
        "unpaid_invoices": unpaid_count,
        "unpaid_total": unpaid_total,
        "missed_recalls": missed_recalls,
        "churned_patients": churned,
        "churn_value_estimate": churn_value,
        "nhs_claim_problems": nhs_problems,
        # Email signals
        "total_inbound_emails": email_metrics["total_inbound"],
        "unreplied_inbound": email_metrics["unreplied_inbound"],
        "unreplied_treatment_inquiries": email_metrics["unreplied_treatment_inquiries"],
        "unreplied_tx_value": unreplied_tx_value,
        "unreplied_complaints": email_metrics["unreplied_complaints"],
        "complaint_churn_value": complaint_churn_value,
        "unmatched_leads": email_metrics["unmatched_leads"],
        "unmatched_lead_value": unmatched_lead_value,
        "avg_reply_hours": email_metrics["avg_reply_hours"],
        "email_lost_total": email_lost_total,
        # Call signals
        "total_calls": call_metrics["total_calls"],
        "total_inbound_calls": call_metrics["total_inbound_calls"],
        "missed_not_returned": call_metrics["missed_not_returned"],
        "voicemails_not_returned": call_metrics["voicemails_not_returned"],
        "missed_returnless_value": missed_returnless_value,
        "after_hours_missed": call_metrics["after_hours_missed"],
        "unmatched_inbound_calls": call_metrics["unmatched_inbound_calls"],
        "unmatched_calls_value": unmatched_calls_value,
        "unreturned_treatment_calls": call_metrics["unreturned_treatment_calls"],
        "unreturned_tx_calls_value": unreturned_tx_calls_value,
        "unreturned_emergency_calls": call_metrics["unreturned_emergency_calls"],
        "avg_ring_seconds": call_metrics["avg_ring_seconds"],
        "calls_lost_total": calls_lost_total,
        "total_lost_estimate": total_lost_estimate,
    }


# ---------------------------------------------------------------------------
# Claude-powered narrative
# ---------------------------------------------------------------------------

CLAUDE_SYSTEM_PROMPT = """You are a dental practice revenue consultant analyzing Emily BI data for London Specialist Dentists, a UK dental clinic.

You will be given hard numbers from the practice management system. Your job:
1. Identify the 3 biggest revenue leaks in plain English
2. For each, say how much money is on the table and why it's leaking
3. Recommend ONE specific action for each leak that the practice can do THIS WEEK
4. Keep it brief — total response under 400 words
5. No fluff, no generic advice. Tie every recommendation to a specific number from the data.
6. Use £ symbol for money, not GBP
7. Write for a busy practice owner, not a consultant"""


def claude_lost_revenue_narrative(metrics: dict[str, Any]) -> str:
    """Use Claude to turn the numbers into actionable narrative."""
    if not ANTHROPIC_API_KEY:
        return "(Anthropic API key missing — set ANTHROPIC_API_KEY in .env to enable AI analysis)"

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    email_block = ""
    if metrics.get("total_inbound_emails", 0) > 0:
        email_block = f"""
EMAIL INBOX SIGNALS ({metrics['total_inbound_emails']} inbound emails):
- {metrics['unreplied_inbound']} inbound emails never replied to (avg reply time: {metrics['avg_reply_hours']:.1f} hours)
- {metrics['unreplied_treatment_inquiries']} UNREPLIED TREATMENT INQUIRIES (implants/Invisalign/crowns) → est. £{metrics['unreplied_tx_value']:,.0f} lost
- {metrics['unmatched_leads']} emails from non-patients (potential new leads lost) → est. £{metrics['unmatched_lead_value']:,.0f}
- {metrics['unreplied_complaints']} unanswered complaints (churn risk) → est. £{metrics['complaint_churn_value']:,.0f}
"""

    call_block = ""
    if metrics.get("total_inbound_calls", 0) > 0:
        call_block = f"""
PHONE SYSTEM SIGNALS ({metrics['total_inbound_calls']} inbound calls, avg ring time {metrics['avg_ring_seconds']:.1f}s):
- {metrics['missed_not_returned']} missed calls never returned → est. £{metrics['missed_returnless_value']:,.0f}
- {metrics['voicemails_not_returned']} voicemails left and never returned (part of above estimate)
- {metrics['unreturned_treatment_calls']} MISSED TREATMENT-INQUIRY CALLS never returned → est. £{metrics['unreturned_tx_calls_value']:,.0f}
- {metrics['unmatched_inbound_calls']} calls from non-patient numbers (possible new leads) → est. £{metrics['unmatched_calls_value']:,.0f}
- {metrics['after_hours_missed']} after-hours calls missed with no callback
- {metrics['unreturned_emergency_calls']} missed EMERGENCY calls never returned (high risk)
"""

    prompt = f"""Here is the current state of London Specialist Dentists (12-month window):

FINANCIAL:
- Annual revenue: £{metrics['annual_revenue']:,.0f}
- Average appointment value: £{metrics['avg_appointment_value']:.0f}

CLINICAL LOST REVENUE LEAKS:
- {metrics['overdue_patients']} patients overdue for a checkup (no future appointment, last visit 6+ months ago) → est. £{metrics['overdue_value_estimate']:,.0f}
- {metrics['fta_count']} no-shows in last 12 months → est. £{metrics['fta_value_estimate']:,.0f}
- {metrics['cancelled_count']} cancelled appointments → est. £{metrics['cancelled_value_estimate']:,.0f}
- {metrics['uncompleted_tp_items']} uncompleted treatment plan items worth £{metrics['uncompleted_tp_value']:,.0f}
- {metrics['unpaid_invoices']} unpaid invoices totalling £{metrics['unpaid_total']:,.0f}
- {metrics['missed_recalls']} recalls missed or unbooked
- {metrics['churned_patients']} churned patients (estimated lifetime value loss: £{metrics['churn_value_estimate']:,.0f})
- {metrics['nhs_claim_problems']} NHS claims with problems (error/queried/invalid)
{email_block}{call_block}
TOTAL ESTIMATED LOST REVENUE: £{metrics['total_lost_estimate']:,.0f}

Write the 3-issue analysis now. If email or call signals are present, prioritise those — they are the most actionable leaks for the practice to fix this week."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=CLAUDE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
