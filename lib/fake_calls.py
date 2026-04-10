"""Fake bOnline call log generator (Claude Haiku powered).

Mirrors lib/fake_emails.py but for phone calls. Each call gets:
    - category, direction, state (answered/missed/voicemail/...)
    - patient linkage via phone number
    - Claude-generated short transcript + 1-line summary
"""

from __future__ import annotations

import json
import random
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from anthropic import Anthropic, APIStatusError

from lib.db import ANTHROPIC_API_KEY

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG = {
    "total_calls": 2000,
    "history_years": 2,
    "pct_inbound": 0.70,
    "pct_patient_match_by_phone": 0.85,
    "pct_patient_no_match": 0.15,     # true new leads or wrong numbers
    "max_workers": 5,
    "model": "claude-haiku-4-5-20251001",
    "clinic_number": "+442079460000",
    "clinic_name": "London Specialist Dentists",
}

# Inbound categories (weighted)
INBOUND_CATEGORIES = [
    ("appointment_inquiry",   22),
    ("treatment_inquiry",     12),
    ("cancellation",          15),
    ("reschedule",            12),
    ("complaint",              8),
    ("insurance",              8),
    ("emergency",              7),
    ("general_question",      11),
    ("prescription",           5),
]

# Outbound auto-calls
OUTBOUND_CATEGORIES = [
    ("appointment_reminder",  40),
    ("recall_call",           25),
    ("collections_call",      15),
    ("followup_call",         20),
]

# Inbound state distribution
INBOUND_STATE_WEIGHTS = [
    ("answered",   60),
    ("missed",     20),
    ("voicemail",  10),
    ("busy",        5),
    ("no_answer",   5),
]

# Category-specific callback rates (for missed/voicemail)
RETURN_RATE = {
    "appointment_inquiry": 0.70,
    "treatment_inquiry":   0.50,   # hot leads deserve follow-up — but 50% fall through
    "cancellation":        0.40,   # missed chance to rebook
    "reschedule":          0.60,
    "complaint":           0.80,   # usually returned (escalation)
    "insurance":           0.45,
    "emergency":           0.90,   # should always be returned
    "general_question":    0.35,
    "prescription":        0.60,
}

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

TODAY = date(2026, 4, 10)
NOW = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
START = TODAY - timedelta(days=365 * CONFIG["history_years"])

RECEPTIONISTS = ["Emma", "Sophie", "Oliver", "Priya", "Chloe"]


def weighted_choice(pairs: list[tuple[str, int]]) -> str:
    items, weights = zip(*pairs)
    return random.choices(items, weights=weights)[0]


def rand_call_time(start: date, end: date) -> tuple[datetime, bool]:
    """Pick a random timestamp; returns (ts, is_after_hours)."""
    delta = max((end - start).days, 1)
    d = start + timedelta(days=random.randint(0, delta))
    # Distribution biased toward business hours but some out-of-hours
    if random.random() < 0.85:
        hour = random.randint(8, 17)
        after_hours = False
    else:
        hour = random.choice([6, 7, 18, 19, 20, 21, 22, 23])
        after_hours = True
    ts = datetime(d.year, d.month, d.day, hour, random.randint(0, 59), tzinfo=timezone.utc)
    # Weekend check: Sat/Sun = after hours too
    if d.weekday() >= 5:
        after_hours = True
    return ts, after_hours


def _e164(phone: str | None) -> str:
    """Normalise a fake phone to E.164-ish form for simulation."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    if digits.startswith("0"):
        return "+44" + digits[1:]
    if not digits.startswith("+"):
        return "+44" + digits
    return digits


# ---------------------------------------------------------------------------
# CallPlan
# ---------------------------------------------------------------------------

@dataclass
class CallPlan:
    direction: str
    category: str
    state: str
    started_at: datetime
    after_hours: bool
    duration_seconds: int
    ring_seconds: int
    patient: dict | None
    match_method: str                 # 'mobile_phone' | 'home_phone' | 'work_phone' | 'none'
    is_returned: bool = False
    returned_at: datetime | None = None
    priority: str = "normal"
    agent_name: str | None = None
    call_id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:14]}")


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

FAKE_LEAD_NUMBERS = [
    "+447700900123", "+447700900456", "+447700900789",
    "+442079460100", "+442079460200", "+447911123456",
    "+447922654321", "+447733111222",
]
FAKE_LEAD_NAMES = [
    "Unknown", "Unknown", "Unknown",
    "Sarah", "Mark", "Ethan", "Grace", "Liam",
]


def plan_calls(patients: list[dict]) -> list[CallPlan]:
    total = CONFIG["total_calls"]
    n_inbound = int(total * CONFIG["pct_inbound"])
    n_outbound = total - n_inbound

    plans: list[CallPlan] = []
    patients_with_phone = [
        p for p in patients
        if p.get("mobile_phone") or p.get("home_phone") or p.get("work_phone")
    ]

    # ---- Inbound ----------------------------------------------------------
    for _ in range(n_inbound):
        category = weighted_choice(INBOUND_CATEGORIES)
        state = weighted_choice(INBOUND_STATE_WEIGHTS)
        ts, after_hours = rand_call_time(START, TODAY)

        # After-hours can never be "answered" by reception — force to missed/voicemail
        if after_hours and state == "answered":
            state = random.choice(["missed", "voicemail"])

        # Patient linkage
        if random.random() < CONFIG["pct_patient_match_by_phone"]:
            patient = random.choice(patients_with_phone)
            # Pick which phone number type matched
            options = []
            if patient.get("mobile_phone"):
                options.append("mobile_phone")
            if patient.get("home_phone"):
                options.append("home_phone")
            if patient.get("work_phone"):
                options.append("work_phone")
            match_method = random.choice(options) if options else "none"
        else:
            patient = None
            match_method = "none"

        # Call durations by state
        if state == "answered":
            duration = random.randint(60, 480)   # 1-8 minutes
            ring = random.randint(2, 15)
        elif state == "voicemail":
            duration = random.randint(15, 90)
            ring = random.randint(15, 30)
        elif state == "missed":
            duration = 0
            ring = random.randint(8, 25)
        elif state == "busy":
            duration = 0
            ring = 0
        else:  # no_answer
            duration = 0
            ring = random.randint(1, 5)

        # Callback flag (only if missed/voicemail)
        is_returned = False
        returned_at = None
        if state in ("missed", "voicemail", "busy", "no_answer"):
            rate = RETURN_RATE.get(category, 0.40)
            if random.random() < rate:
                is_returned = True
                returned_at = ts + timedelta(hours=random.randint(1, 72))
                if returned_at.date() > TODAY:
                    returned_at = None
                    is_returned = False

        priority = "high" if category in ("emergency", "complaint") else "normal"

        inbound_plan = CallPlan(
            direction="inbound",
            category=category,
            state=state,
            started_at=ts,
            after_hours=after_hours,
            duration_seconds=duration,
            ring_seconds=ring,
            patient=patient,
            match_method=match_method,
            is_returned=is_returned,
            returned_at=returned_at,
            priority=priority,
            agent_name=random.choice(RECEPTIONISTS) if state == "answered" else None,
        )
        plans.append(inbound_plan)

        # If returned, create the outbound callback record
        if is_returned and returned_at and patient:
            plans.append(CallPlan(
                direction="outbound",
                category="followup_call",
                state="answered",
                started_at=returned_at,
                after_hours=False,
                duration_seconds=random.randint(45, 240),
                ring_seconds=random.randint(3, 12),
                patient=patient,
                match_method=match_method,
                priority="normal",
                agent_name=random.choice(RECEPTIONISTS),
            ))

    # ---- Outbound (auto) --------------------------------------------------
    remaining_outbound = max(0, total - len(plans))
    for _ in range(remaining_outbound):
        category = weighted_choice(OUTBOUND_CATEGORIES)
        patient = random.choice(patients_with_phone)
        ts, _ = rand_call_time(START, TODAY)

        # Outbound auto-calls: 70% answered, 20% missed, 10% voicemail
        state = random.choices(
            ["answered", "missed", "voicemail"],
            weights=[70, 20, 10],
        )[0]
        if state == "answered":
            duration = random.randint(30, 180)
            ring = random.randint(2, 10)
        elif state == "voicemail":
            duration = random.randint(10, 60)
            ring = random.randint(15, 25)
        else:
            duration = 0
            ring = random.randint(8, 20)

        plans.append(CallPlan(
            direction="outbound",
            category=category,
            state=state,
            started_at=ts,
            after_hours=False,
            duration_seconds=duration,
            ring_seconds=ring,
            patient=patient,
            match_method="mobile_phone",
            priority="normal",
            agent_name=random.choice(RECEPTIONISTS) if state == "answered" else None,
        ))

    plans.sort(key=lambda p: p.started_at)
    return plans


# ---------------------------------------------------------------------------
# Claude Haiku — transcript + summary generator
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You generate short, realistic phone call transcripts for a UK dental clinic simulation.

Respond with ONLY a valid JSON object in this exact format, and nothing else:
{"summary": "one-line description of the call", "transcript": "short dialogue OR voicemail text"}

Rules:
- No markdown, no code fences, no preamble.
- Summary: one sentence, under 120 characters.
- Transcript style depends on call state:
    * ANSWERED: short dialogue, 3-6 lines, prefixed with [Caller] and [Reception] or [Dentist Office]
    * VOICEMAIL: a 2-4 sentence voicemail the caller left
    * MISSED / BUSY / NO_ANSWER: empty string "" for transcript
- Use informal British English. No filler, no fluff.
- Do not invent private medical history. Keep it realistic but non-specific."""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_thread_local = threading.local()


def _get_client() -> Anthropic:
    if not hasattr(_thread_local, "client"):
        _thread_local.client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _thread_local.client


def _prompt_for(plan: CallPlan) -> str:
    if plan.patient:
        p = plan.patient
        patient_ctx = f"Patient: {p['first_name']} {p['last_name']} (existing patient)."
        if p.get("medical_alert") and p.get("medical_alert_text"):
            patient_ctx += f" Medical alert: {p['medical_alert_text']}."
    else:
        patient_ctx = "Caller is NOT in the patient database — a stranger or wrong number."

    scenarios = {
        "appointment_inquiry":    "Wants to book a first appointment or routine checkup.",
        "treatment_inquiry":      "Asking about cost of a specific treatment (implant, Invisalign, crown, whitening).",
        "cancellation":           "Needs to cancel an upcoming appointment, briefly explains why.",
        "reschedule":             "Wants to move an upcoming appointment.",
        "complaint":              "Unhappy — pain after treatment, long wait, or billing dispute.",
        "insurance":              "Question about NHS eligibility, Denplan, or private insurance.",
        "emergency":              "Dental emergency — severe pain, knocked tooth, swelling.",
        "general_question":       "Mundane question — hours, parking, directions.",
        "prescription":           "Needs a prescription refill.",
        "appointment_reminder":   "Outbound: reminding patient about tomorrow's appointment.",
        "recall_call":            "Outbound: checkup is overdue, time to book.",
        "collections_call":       "Outbound: polite reminder about an unpaid invoice.",
        "followup_call":          "Outbound: following up after a recent visit, returning a missed call, or checking on treatment outcome.",
    }
    scenario = scenarios.get(plan.category, "A realistic dental clinic call.")

    state_note = ""
    if plan.state == "voicemail":
        state_note = "\nThe call went to voicemail — the caller leaves a message."
    elif plan.state in ("missed", "busy", "no_answer"):
        state_note = f"\nThe call state is {plan.state} — the caller never connected. Transcript should be empty string."
    else:
        state_note = "\nThe call was answered by reception; short dialogue."

    return (
        f"Context: {patient_ctx}\n"
        f"Direction: {plan.direction}\n"
        f"Category: {plan.category}\n"
        f"State: {plan.state}\n"
        f"Scenario: {scenario}"
        f"{state_note}\n\n"
        f"Generate the JSON now."
    )


def _fallback_content(plan: CallPlan) -> dict[str, str]:
    summaries = {
        "appointment_inquiry":    "Caller asking to book an appointment",
        "treatment_inquiry":      "Enquiry about treatment cost",
        "cancellation":           "Patient cancelling upcoming appointment",
        "reschedule":             "Patient wants to reschedule",
        "complaint":              "Complaint about recent treatment",
        "insurance":              "Insurance / payment plan question",
        "emergency":              "Dental emergency call",
        "general_question":       "General enquiry",
        "prescription":           "Prescription refill request",
        "appointment_reminder":   "Appointment reminder call",
        "recall_call":            "Recall reminder call",
        "collections_call":       "Unpaid invoice follow-up",
        "followup_call":          "Patient follow-up call",
    }
    return {
        "summary": summaries.get(plan.category, "Call"),
        "transcript": "" if plan.state in ("missed", "busy", "no_answer") else "[Caller] Hi. [Reception] How can I help?",
    }


def _parse_json(text: str) -> dict[str, str] | None:
    text = text.strip()
    if text.startswith("```"):
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
    if "summary" in obj and "transcript" in obj:
        return {
            "summary": str(obj["summary"]).strip(),
            "transcript": str(obj["transcript"]).strip(),
        }
    return None


def generate_call_content(plan: CallPlan) -> dict[str, str]:
    client = _get_client()
    try:
        response = client.messages.create(
            model=CONFIG["model"],
            max_tokens=250,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _prompt_for(plan)}],
        )
        parsed = _parse_json(response.content[0].text)
        if parsed:
            return parsed
    except APIStatusError:
        pass
    except Exception:  # noqa: BLE001
        pass
    return _fallback_content(plan)


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _pick_phone(patient: dict, method: str) -> str:
    if method == "mobile_phone":
        return _e164(patient.get("mobile_phone"))
    if method == "home_phone":
        return _e164(patient.get("home_phone"))
    if method == "work_phone":
        return _e164(patient.get("work_phone"))
    return _e164(patient.get("mobile_phone") or patient.get("home_phone") or patient.get("work_phone"))


def _build_call_row(plan: CallPlan, summary: str, transcript: str) -> dict[str, Any]:
    if plan.direction == "inbound":
        if plan.patient:
            from_number = _pick_phone(plan.patient, plan.match_method)
            from_name = f"{plan.patient['first_name']} {plan.patient['last_name']}"
        else:
            from_number = random.choice(FAKE_LEAD_NUMBERS)
            from_name = random.choice(FAKE_LEAD_NAMES)
        to_number = CONFIG["clinic_number"]
    else:
        from_number = CONFIG["clinic_number"]
        from_name = CONFIG["clinic_name"]
        to_number = _pick_phone(plan.patient, plan.match_method) if plan.patient else ""

    ended_at = plan.started_at + timedelta(seconds=plan.duration_seconds + plan.ring_seconds)

    sentiment = (
        "negative" if plan.category in ("complaint", "emergency")
        else "positive" if plan.category == "followup_call"
        else "neutral"
    )

    voicemail_left = plan.state == "voicemail"

    recording_url = (
        f"https://bonline-fake.internal/recordings/{plan.call_id}.mp3"
        if plan.state == "answered" else None
    )

    return {
        "call_id": plan.call_id,
        "direction": plan.direction,
        "from_number": from_number,
        "from_name": from_name,
        "to_number": to_number,
        "started_at": plan.started_at,
        "ended_at": ended_at,
        "duration_seconds": plan.duration_seconds,
        "ring_seconds": plan.ring_seconds,
        "state": plan.state,
        "answered": plan.state == "answered",
        "voicemail_left": voicemail_left,
        "after_hours": plan.after_hours,
        "recording_url": recording_url,
        "transcript": transcript,
        "summary": summary,
        "category": plan.category,
        "priority": plan.priority,
        "sentiment": sentiment,
        "is_returned": plan.is_returned,
        "returned_at": plan.returned_at,
        "patient_id": plan.patient["id"] if plan.patient else None,
        "match_method": plan.match_method,
        "agent_name": plan.agent_name,
        "created_at": plan.started_at,
        "updated_at": plan.started_at,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def generate_all_calls(
    patients: list[dict],
    seed: int = 42,
    progress_every: int = 100,
) -> list[dict[str, Any]]:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY missing from .env")

    random.seed(seed)
    print(f"[plan] Planning {CONFIG['total_calls']} calls for {len(patients)} patients...")
    plans = plan_calls(patients)
    print(f"[plan] Built {len(plans)} call plans")

    rows: list[dict[str, Any] | None] = [None] * len(plans)
    completed = 0
    lock = threading.Lock()

    def work(i: int, plan: CallPlan):
        content = generate_call_content(plan)
        return i, _build_call_row(plan, content["summary"], content["transcript"])

    print(f"[claude] Generating transcripts with {CONFIG['model']} "
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

    return [r for r in rows if r is not None]
