"""Fake dental clinic mailbox generator (Claude Haiku powered).

Pipeline:
    1. plan_emails(patients, count)       — deterministic planner: decides
                                             what each email is about, who it's
                                             from/to, whether it got replied to
    2. generate_email_body(plan, client)  — one Claude Haiku call, returns
                                             {"subject": ..., "body": ...}
    3. generate_all_emails(...)           — orchestrator, runs Claude calls
                                             in parallel via ThreadPoolExecutor

Output: list of dicts matching the `emails` table schema.
"""

from __future__ import annotations

import json
import random
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from anthropic import Anthropic, APIStatusError

from lib.db import ANTHROPIC_API_KEY

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG = {
    "total_emails": 2000,
    "history_years": 2,
    "pct_inbound": 0.45,
    "pct_patient_match_by_email": 0.85,   # of inbound: match by address
    "pct_patient_match_by_name": 0.10,    # of inbound: from their "other" email
    # remaining 5% = unmatched (non-patient leads)
    "max_workers": 5,
    "model": "claude-haiku-4-5-20251001",
    "clinic_email": "reception@lsd-dental.co.uk",
    "clinic_domain": "lsd-dental.co.uk",
}

# Inbound category distribution (weighted random pick)
INBOUND_CATEGORIES = [
    ("appointment_inquiry",   20),
    ("treatment_inquiry",     15),
    ("cancellation",          15),
    ("reschedule",            10),
    ("complaint",             10),
    ("insurance",             10),
    ("general_question",      10),
    ("prescription",           5),
    ("positive_feedback",      5),
]

# Auto-sent outbound (everything NOT a reply to inbound)
AUTO_OUTBOUND_CATEGORIES = [
    ("appointment_confirmation", 35),
    ("recall_reminder",          25),
    ("invoice_reminder",         20),
    ("treatment_followup",       20),
]

# Unreply rate (lost revenue signal) — higher for valuable categories
UNREPLY_RATE = {
    "treatment_inquiry":   0.30,   # lost high-value leads
    "appointment_inquiry": 0.15,
    "cancellation":        0.25,   # missed rebook opportunity
    "reschedule":          0.20,
    "complaint":           0.10,   # usually replied but some fester
    "insurance":           0.25,
    "general_question":    0.30,
    "prescription":        0.15,
    "positive_feedback":   0.50,   # feedback often not replied to
}

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

TODAY = date(2026, 4, 10)
NOW = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
START = TODAY - timedelta(days=365 * CONFIG["history_years"])


def weighted_choice(pairs: list[tuple[str, int]]) -> str:
    items, weights = zip(*pairs)
    return random.choices(items, weights=weights)[0]


def rand_dt_between(start: date, end: date) -> datetime:
    delta = max((end - start).days, 1)
    d = start + timedelta(days=random.randint(0, delta))
    return datetime(
        d.year, d.month, d.day,
        random.randint(6, 22),
        random.randint(0, 59),
        tzinfo=timezone.utc,
    )


# ---------------------------------------------------------------------------
# EmailPlan — deterministic plan for one email
# ---------------------------------------------------------------------------

@dataclass
class EmailPlan:
    direction: str                    # "inbound" | "outbound"
    category: str
    received_at: datetime
    patient: dict | None              # the linked patient (None if unmatched)
    match_method: str                 # "email_address" | "name" | "none"
    is_reply: bool = False            # outbound-only: is this a reply to an inbound?
    thread_id: str = field(default_factory=lambda: f"thr_{uuid.uuid4().hex[:12]}")
    reply_to_message_id: str | None = None
    inbound_ref_category: str | None = None  # if is_reply, what was asked
    has_attachment: bool = False
    priority: str = "normal"
    # Pre-generated values so we can persist even if Claude fails
    fallback_subject: str = ""
    fallback_body: str = ""


# ---------------------------------------------------------------------------
# Planner — builds 2000 EmailPlan objects
# ---------------------------------------------------------------------------

FAKE_LEAD_EMAILS = [
    "curious.new.patient@gmail.com",
    "implant.question@outlook.com",
    "invisalign.cost@yahoo.co.uk",
    "worried.toothache@gmail.com",
    "nhs.referral@btinternet.com",
    "relocating.to.london@gmail.com",
    "harley.street.search@hotmail.co.uk",
]


def _build_patient_email_variant(patient: dict) -> str:
    """Generate an alternate email that's NOT the one we have on file."""
    first = patient["first_name"].lower()
    last = patient["last_name"].lower()
    variants = [
        f"{first}{last}@outlook.com",
        f"{first}.{last}{random.randint(1, 99)}@gmail.com",
        f"{first[0]}{last}@yahoo.co.uk",
        f"{last}.{first}@hotmail.co.uk",
    ]
    return random.choice(variants)


def plan_emails(patients: list[dict]) -> list[EmailPlan]:
    """Create a list of EmailPlan objects (deterministic, seedable)."""
    total = CONFIG["total_emails"]
    n_inbound = int(total * CONFIG["pct_inbound"])
    n_outbound_auto = total - n_inbound  # outbound = auto-sent + replies
    # But a portion of outbound will be replies to inbounds; the rest are auto-sent.
    # We'll determine replies once we plan inbounds.

    plans: list[EmailPlan] = []
    active_patients = [p for p in patients if p.get("email_address")]

    # ---- Inbound + replies ------------------------------------------------
    for _ in range(n_inbound):
        category = weighted_choice(INBOUND_CATEGORIES)
        ts = rand_dt_between(START, TODAY)

        # Decide patient linkage
        r = random.random()
        if r < CONFIG["pct_patient_match_by_email"]:
            patient = random.choice(active_patients)
            match_method = "email_address"
        elif r < CONFIG["pct_patient_match_by_email"] + CONFIG["pct_patient_match_by_name"]:
            patient = random.choice(active_patients)
            match_method = "name"
        else:
            patient = None
            match_method = "none"

        is_replied = random.random() > UNREPLY_RATE.get(category, 0.20)
        priority = "high" if category in ("complaint", "treatment_inquiry") else "normal"

        inbound_plan = EmailPlan(
            direction="inbound",
            category=category,
            received_at=ts,
            patient=patient,
            match_method=match_method,
            priority=priority,
            has_attachment=random.random() < 0.08,
        )
        plans.append(inbound_plan)

        # Matching reply outbound (if replied)
        if is_replied:
            reply_ts = ts + timedelta(hours=random.randint(1, 48))
            if reply_ts.date() > TODAY:
                continue
            plans.append(EmailPlan(
                direction="outbound",
                category="reply",
                received_at=reply_ts,
                patient=patient,
                match_method=match_method,
                is_reply=True,
                thread_id=inbound_plan.thread_id,
                inbound_ref_category=category,
                priority="normal",
            ))
            # Record the reply linkage on the inbound (we'll set message_ids later)
            inbound_plan.reply_to_message_id = "__REPLY__"  # marker, filled post-gen

    # ---- Auto-outbound (fills remaining slots) ----------------------------
    replies_so_far = sum(1 for p in plans if p.direction == "outbound")
    remaining_outbound = max(0, total - len(plans))

    for _ in range(remaining_outbound):
        category = weighted_choice(AUTO_OUTBOUND_CATEGORIES)
        patient = random.choice(active_patients)
        ts = rand_dt_between(START, TODAY)
        plans.append(EmailPlan(
            direction="outbound",
            category=category,
            received_at=ts,
            patient=patient,
            match_method="email_address",
            priority="normal",
        ))

    # Sort by time so the feed looks chronological when inserted
    plans.sort(key=lambda p: p.received_at)
    return plans


# ---------------------------------------------------------------------------
# Claude Haiku — subject + body generator
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You generate short, realistic emails for a UK dental clinic simulation.

Respond with ONLY a valid JSON object in this exact format, and nothing else:
{"subject": "short subject line", "body": "2-4 sentence email body"}

Rules:
- No markdown, no code fences, no preamble.
- The body is 2-4 sentences. Informal British English spelling (colour, optimise, etc.).
- Do NOT include greetings like "Dear Dr. X" or signatures — just the message content.
- Don't reveal you are an AI or that this is a simulation.
- For replies, sound like a polite busy receptionist.
- Keep it natural. No generic filler like "please don't hesitate to contact us"."""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
# Thread-local storage for Anthropic clients (1 per worker thread)
_thread_local = threading.local()


def _get_client() -> Anthropic:
    if not hasattr(_thread_local, "client"):
        _thread_local.client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _thread_local.client


def _prompt_for(plan: EmailPlan) -> str:
    """Build the user prompt for Haiku based on the plan."""
    if plan.patient:
        p = plan.patient
        dob = p.get("date_of_birth")
        age = None
        if dob:
            age = TODAY.year - dob.year - ((TODAY.month, TODAY.day) < (dob.month, dob.day))
        patient_ctx = (
            f"Name: {p['first_name']} {p['last_name']}. "
            f"{age}yo {p.get('gender') or ''}. "
            f"Payment plan id: {p.get('payment_plan_id')}. "
        )
        if p.get("medical_alert") and p.get("medical_alert_text"):
            patient_ctx += f"Medical alert: {p['medical_alert_text']}. "
    else:
        patient_ctx = "Sender is NOT an existing patient — this is a new enquiry from a stranger."

    category_instructions = {
        "appointment_inquiry": "Patient wants to book a routine checkup or first appointment. Ask about availability or prices.",
        "treatment_inquiry": "Asks about cost/procedure for a specific treatment (e.g. Invisalign, implants, crowns, veneers, whitening).",
        "cancellation": "Needs to cancel their upcoming appointment. Give a brief reason.",
        "reschedule": "Wants to move their upcoming appointment to a different day or time.",
        "complaint": "Has a complaint — pain after treatment, long wait, billing dispute, or unhappy with outcome.",
        "insurance": "Question about insurance coverage, Denplan, or NHS eligibility.",
        "general_question": "A mundane question — opening hours, parking, directions, forms needed.",
        "prescription": "Needs a prescription refill (painkillers, antibiotics after extraction).",
        "positive_feedback": "Thanks the team for a great experience.",
        "appointment_confirmation": "Automated outbound confirming an upcoming appointment with date/time/dentist.",
        "recall_reminder": "Outbound reminder that a checkup is due. Warm but not pushy.",
        "invoice_reminder": "Outbound chasing an unpaid invoice. Polite but firm. Include a reference number.",
        "treatment_followup": "Outbound follow-up after a recent treatment, asking how they're feeling.",
        "reply": f"Reply from reception to a patient's earlier email about: {plan.inbound_ref_category}. Be helpful and brief.",
    }

    instruction = category_instructions.get(plan.category, "A short, realistic email.")

    return (
        f"Context: {patient_ctx}\n"
        f"Direction: {plan.direction}\n"
        f"Category: {plan.category}\n"
        f"Scenario: {instruction}\n\n"
        f"Generate the email now. Respond with JSON only."
    )


def _fallback_content(plan: EmailPlan) -> dict[str, str]:
    """Hardcoded fallback if Claude call fails — keeps pipeline moving."""
    subject_map = {
        "appointment_inquiry":      "Appointment booking request",
        "treatment_inquiry":        "Treatment question",
        "cancellation":             "Need to cancel appointment",
        "reschedule":               "Can I reschedule",
        "complaint":                "Issue with recent treatment",
        "insurance":                "Insurance question",
        "general_question":         "Quick question",
        "prescription":             "Prescription request",
        "positive_feedback":        "Thank you",
        "appointment_confirmation": "Your appointment is confirmed",
        "recall_reminder":          "Time for your dental checkup",
        "invoice_reminder":         "Outstanding invoice reminder",
        "treatment_followup":       "How are you feeling after your visit",
        "reply":                    "Re: your enquiry",
    }
    return {
        "subject": subject_map.get(plan.category, "Dental clinic"),
        "body": "Hi, hope this finds you well. Wanted to get in touch. Thanks.",
    }


def _parse_json(text: str) -> dict[str, str] | None:
    """Extract the first JSON object from Claude's response."""
    text = text.strip()
    if text.startswith("```"):
        # strip markdown fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if "subject" in obj and "body" in obj:
        return {"subject": str(obj["subject"]).strip(), "body": str(obj["body"]).strip()}
    return None


def generate_email_body(plan: EmailPlan) -> dict[str, str]:
    """Single Claude Haiku call for one email. Falls back to template on failure."""
    client = _get_client()
    try:
        response = client.messages.create(
            model=CONFIG["model"],
            max_tokens=250,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _prompt_for(plan)}],
        )
        text = response.content[0].text
        parsed = _parse_json(text)
        if parsed:
            return parsed
    except APIStatusError:
        pass
    except Exception:  # noqa: BLE001
        pass
    return _fallback_content(plan)


# ---------------------------------------------------------------------------
# Orchestrator — runs Claude calls in parallel and produces final row dicts
# ---------------------------------------------------------------------------

def _build_email_row(plan: EmailPlan, subject: str, body: str, idx: int) -> dict[str, Any]:
    """Convert a plan + generated content into an emails-table row."""
    message_id = f"msg_{uuid.uuid4().hex}"
    clinic_email = CONFIG["clinic_email"]
    clinic_domain = CONFIG["clinic_domain"]

    # Determine from/to
    if plan.direction == "inbound":
        if plan.patient and plan.match_method == "email_address":
            from_addr = plan.patient["email_address"]
            from_name = f"{plan.patient['first_name']} {plan.patient['last_name']}"
        elif plan.patient and plan.match_method == "name":
            from_addr = _build_patient_email_variant(plan.patient)
            from_name = f"{plan.patient['first_name']} {plan.patient['last_name']}"
        else:
            from_addr = random.choice(FAKE_LEAD_EMAILS)
            from_name = from_addr.split("@")[0].replace(".", " ").title()
        to_addr = clinic_email
        folder = "inbox"
        received_at = plan.received_at
        sent_at = plan.received_at
    else:
        from_addr = clinic_email
        from_name = "London Specialist Dentists — Reception"
        if plan.patient:
            to_addr = plan.patient["email_address"] or f"patient{plan.patient['id']}@{clinic_domain}"
        else:
            to_addr = "unknown@example.com"
        folder = "sent"
        received_at = None
        sent_at = plan.received_at

    # Reply marker handled post-processing; here is_replied is False by default.
    return {
        "message_id": message_id,
        "thread_id": plan.thread_id,
        "folder": folder,
        "direction": plan.direction,
        "from_address": from_addr,
        "from_name": from_name,
        "to_address": to_addr,
        "cc_addresses": [],
        "subject": subject,
        "body_text": body,
        "body_preview": (body[:120] + ("…" if len(body) > 120 else "")),
        "received_at": received_at,
        "sent_at": sent_at,
        "is_read": True if plan.direction == "outbound" else random.random() < 0.9,
        "is_replied": False,  # patched after threading pass
        "replied_at": None,
        "reply_to_message_id": None,
        "category": plan.category,
        "priority": plan.priority,
        "sentiment": (
            "negative" if plan.category == "complaint"
            else "positive" if plan.category == "positive_feedback"
            else "neutral"
        ),
        "has_attachment": plan.has_attachment,
        "patient_id": plan.patient["id"] if plan.patient else None,
        "match_method": plan.match_method,
        "created_at": plan.received_at,
        "updated_at": plan.received_at,
    }


def _link_threads(rows: list[dict[str, Any]], plans: list[EmailPlan]) -> None:
    """Patch inbound rows with is_replied + replied_at from their reply outbound."""
    # Build thread_id → [row indexes]
    thread_to_rows: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        thread_to_rows.setdefault(r["thread_id"], []).append(i)

    for thr_id, idxs in thread_to_rows.items():
        inbound_idx = None
        reply_idx = None
        for i in idxs:
            if rows[i]["direction"] == "inbound":
                inbound_idx = i
            elif rows[i]["direction"] == "outbound" and rows[i]["category"] == "reply":
                reply_idx = i
        if inbound_idx is not None and reply_idx is not None:
            rows[inbound_idx]["is_replied"] = True
            rows[inbound_idx]["replied_at"] = rows[reply_idx]["sent_at"]
            rows[reply_idx]["reply_to_message_id"] = rows[inbound_idx]["message_id"]


def generate_all_emails(
    patients: list[dict],
    seed: int = 42,
    progress_every: int = 100,
) -> list[dict[str, Any]]:
    """Generate the full fake mailbox. Parallelizes Claude calls."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY missing from .env")

    random.seed(seed)
    print(f"[plan] Planning {CONFIG['total_emails']} emails for {len(patients)} patients...")
    plans = plan_emails(patients)
    print(f"[plan] Built {len(plans)} email plans (inbound/outbound/replies)")

    rows: list[dict[str, Any] | None] = [None] * len(plans)
    completed = 0
    lock = threading.Lock()

    def work(i: int, plan: EmailPlan):
        content = generate_email_body(plan)
        return i, _build_email_row(plan, content["subject"], content["body"], i)

    print(f"[claude] Generating email bodies with {CONFIG['model']} "
          f"({CONFIG['max_workers']} workers)...")
    with ThreadPoolExecutor(max_workers=CONFIG["max_workers"]) as ex:
        futures = [ex.submit(work, i, plan) for i, plan in enumerate(plans)]
        for fut in as_completed(futures):
            i, row = fut.result()
            rows[i] = row
            with lock:
                completed += 1
                if completed % progress_every == 0 or completed == len(plans):
                    print(f"[claude]   progress: {completed}/{len(plans)}")

    final_rows = [r for r in rows if r is not None]
    _link_threads(final_rows, plans)
    print(f"[done] Generated {len(final_rows)} emails")
    return final_rows
