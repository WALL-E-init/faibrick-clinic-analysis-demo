"""Unified patient timeline.

One chronological feed per patient combining:
- appointments (booked, completed, cancelled, FTA)
- emails (inbound + outbound)
- calls (inbound + outbound)
- invoices (issued, paid, outstanding)
- payments
- recalls (due)

Each event is a dict with: ts, kind, icon, title, subtitle, meta.
Sorted newest-first by default. Use sort_ascending=True for oldest-first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from lib.db import get_conn


def get_patient_timeline(
    patient_id: int,
    sort_ascending: bool = False,
    limit_per_source: int = 100,
) -> list[dict[str, Any]]:
    """Return a merged list of timeline events for a patient."""
    events: list[dict[str, Any]] = []

    with get_conn(dict_cursor=True) as conn, conn.cursor() as cur:
        # --- appointments ---
        cur.execute(
            """
            SELECT id, reason, state, duration, start_time, finish_time, notes,
                   cancelled_at, did_not_attend_at, completed_at
            FROM appointments
            WHERE patient_id = %s
            ORDER BY start_time DESC
            LIMIT %s
            """,
            (patient_id, limit_per_source),
        )
        for a in cur.fetchall():
            state = (a["state"] or "").lower()
            if "attend" in state and "not" in state:
                icon = "❌"
                title = f"No-show: {a['reason'] or 'appointment'}"
            elif state == "cancelled":
                icon = "🚫"
                title = f"Cancelled: {a['reason'] or 'appointment'}"
            elif state == "completed":
                icon = "✅"
                title = f"Completed: {a['reason'] or 'appointment'}"
            else:
                icon = "📅"
                title = f"Booked: {a['reason'] or 'appointment'}"
            events.append({
                "ts": a["start_time"],
                "kind": "appointment",
                "icon": icon,
                "title": title,
                "subtitle": f"{a['duration'] or 0} min · state: {state}",
                "meta": f"notes: {a['notes']}" if a.get("notes") else "",
                "ref_id": a["id"],
            })

        # --- invoices ---
        cur.execute(
            """
            SELECT id, amount, amount_outstanding, dated_on, due_on, paid, paid_on, reference
            FROM invoices
            WHERE patient_id = %s
            ORDER BY dated_on DESC
            LIMIT %s
            """,
            (patient_id, limit_per_source),
        )
        for inv in cur.fetchall():
            dated = inv["dated_on"]
            ts = datetime.combine(dated, datetime.min.time()) if dated else None
            if inv["paid"]:
                icon = "💷"
                title = f"Invoice paid: £{float(inv['amount'] or 0):,.2f}"
                subtitle = f"Ref {inv['reference'] or inv['id']} · paid on {inv['paid_on']}"
            else:
                icon = "⚠️"
                title = f"Invoice UNPAID: £{float(inv['amount_outstanding'] or 0):,.2f} outstanding"
                subtitle = f"Ref {inv['reference'] or inv['id']} · due {inv['due_on']}"
            events.append({
                "ts": ts,
                "kind": "invoice",
                "icon": icon,
                "title": title,
                "subtitle": subtitle,
                "meta": "",
                "ref_id": inv["id"],
            })

        # --- emails ---
        try:
            cur.execute(
                """
                SELECT id, direction, category, subject, body_preview, from_name,
                       received_at, sent_at, is_replied, priority
                FROM emails
                WHERE patient_id = %s
                ORDER BY COALESCE(received_at, sent_at) DESC
                LIMIT %s
                """,
                (patient_id, limit_per_source),
            )
            for e in cur.fetchall():
                ts = e["received_at"] or e["sent_at"]
                if e["direction"] == "inbound":
                    icon = "📩"
                    title = f"Email in: {e['subject']}"
                    replied = "✅ replied" if e["is_replied"] else "⏳ UNREPLIED"
                    subtitle = f"from {e['from_name']} · {e['category']} · {replied}"
                else:
                    icon = "📤"
                    title = f"Email sent: {e['subject']}"
                    subtitle = f"category: {e['category']}"
                events.append({
                    "ts": ts,
                    "kind": "email",
                    "icon": icon,
                    "title": title,
                    "subtitle": subtitle,
                    "meta": e["body_preview"] or "",
                    "ref_id": e["id"],
                })
        except Exception:  # noqa: BLE001
            pass  # emails table not populated

        # --- calls ---
        try:
            cur.execute(
                """
                SELECT id, direction, category, state, started_at, duration_seconds,
                       summary, is_returned, priority, from_name
                FROM calls
                WHERE patient_id = %s
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (patient_id, limit_per_source),
            )
            for c in cur.fetchall():
                state = c["state"]
                if c["direction"] == "inbound":
                    if state == "answered":
                        icon = "📞"
                        title = f"Call in (answered): {c['summary'] or c['category']}"
                    elif state == "missed":
                        returned = "✅ returned" if c["is_returned"] else "⏳ NEVER RETURNED"
                        icon = "📵"
                        title = f"Missed call: {c['summary'] or c['category']} · {returned}"
                    elif state == "voicemail":
                        returned = "✅ returned" if c["is_returned"] else "⏳ NOT RETURNED"
                        icon = "🎙️"
                        title = f"Voicemail: {c['summary'] or c['category']} · {returned}"
                    else:
                        icon = "🔕"
                        title = f"Call in ({state}): {c['summary'] or c['category']}"
                else:
                    icon = "📤"
                    title = f"Call out: {c['summary'] or c['category']}"
                events.append({
                    "ts": c["started_at"],
                    "kind": "call",
                    "icon": icon,
                    "title": title,
                    "subtitle": f"{c['category']} · {c['duration_seconds'] or 0}s · {c['priority']}",
                    "meta": "",
                    "ref_id": c["id"],
                })
        except Exception:  # noqa: BLE001
            pass  # calls table not populated

        # --- recalls ---
        cur.execute(
            """
            SELECT id, due_date, recall_type, status, times_contacted
            FROM recalls
            WHERE patient_id = %s
            ORDER BY due_date DESC
            LIMIT %s
            """,
            (patient_id, limit_per_source),
        )
        for r in cur.fetchall():
            due = r["due_date"]
            ts = datetime.combine(due, datetime.min.time()) if due else None
            status = r["status"] or ""
            icon = "🔔" if status.lower() not in ("missed", "unbooked") else "⚠️"
            events.append({
                "ts": ts,
                "kind": "recall",
                "icon": icon,
                "title": f"Recall due ({r['recall_type']}): {status}",
                "subtitle": f"contacted {r['times_contacted'] or 0}x",
                "meta": "",
                "ref_id": r["id"],
            })

    # Sort and drop events with no timestamp
    events = [e for e in events if e["ts"] is not None]
    events.sort(key=lambda e: e["ts"], reverse=not sort_ascending)
    return events


def timeline_year_buckets(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group events by year-month label for display."""
    out: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        key = e["ts"].strftime("%Y-%m")
        out.setdefault(key, []).append(e)
    return out
