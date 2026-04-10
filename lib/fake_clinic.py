"""Fake Dentally clinic data generator.

Builds a realistic UK dental clinic dataset that mimics what the real
Dentally API would return. Outputs plain Python dicts keyed by table name:

    {
      "practice":      [dict, ...],
      "sites":         [...],
      "practitioners": [...],
      "patients":      [...],
      "appointments":  [...],
      ...
    }

The generator bakes in realistic lost-revenue scenarios:
- FTA (failed to attend) appointments
- Cancelled appointments
- Patients overdue for recall
- Treatment plans with uncompleted items
- Unpaid invoices
- Churned patients
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from faker import Faker

fake = Faker("en_GB")

# ---------------------------------------------------------------------------
# Configuration (tweak here to change scale)
# ---------------------------------------------------------------------------

CONFIG = {
    "num_patients": 1000,
    "history_years": 2,
    "num_dentists": 2,
    "num_hygienists": 1,
    "num_rooms": 3,

    # Lost-revenue knobs (fractions)
    "pct_fta": 0.08,                 # 8% failed to attend
    "pct_cancelled": 0.10,           # 10% cancelled
    "pct_churned_patients": 0.05,    # 5% archived
    "pct_overdue_recall": 0.20,      # 20% recall overdue/unbooked
    "pct_unpaid_invoices": 0.12,     # 12% unpaid
    "pct_uncompleted_tp_items": 0.25,  # 25% of plan items not completed
}

# ---------------------------------------------------------------------------
# Reference data (pulled from UK dental industry research)
# ---------------------------------------------------------------------------

TREATMENT_CATALOG = [
    # (code, nomenclature, description, price, duration_min, nhs_band, region)
    ("EXAM",    "Examination",           "Routine check-up and oral health assessment", 55,   20, "1", "mouth"),
    ("HYG",     "Scale and Polish",      "Professional hygienist clean",                 65,   30, "1", "mouth"),
    ("XRAY",    "X-Ray",                 "Digital bitewing radiograph",                  25,   10, "1", "mouth"),
    ("FILLC",   "Composite Filling",     "White composite filling, single surface",      120,  40, "2", "tooth"),
    ("FILLCL",  "Composite Large",       "White composite filling, multi-surface",       180,  50, "2", "tooth"),
    ("FILLA",   "Amalgam Filling",       "Silver amalgam filling",                       95,   30, "2", "tooth"),
    ("RCT",     "Root Canal Treatment",  "Endodontic root canal therapy",                650,  90, "2", "tooth"),
    ("EXT",     "Extraction",            "Routine tooth extraction",                     90,   30, "2", "tooth"),
    ("EXTS",    "Surgical Extraction",   "Complicated surgical extraction",              250,  60, "2", "tooth"),
    ("CRWNP",   "Porcelain Crown",       "Porcelain fused to metal crown",               650,  60, "3", "tooth"),
    ("CRWNZ",   "Zirconia Crown",        "Full zirconia crown",                          895,  60, "3", "tooth"),
    ("VEN",     "Porcelain Veneer",      "Porcelain veneer, single tooth",               750,  60, "3", "tooth"),
    ("IMP",     "Dental Implant",        "Titanium implant + crown, single tooth",       2500, 120, "3", "tooth"),
    ("BRIDGE",  "Dental Bridge",         "3-unit porcelain bridge",                      1800, 90, "3", "mouth"),
    ("WHT",     "Teeth Whitening",       "Professional take-home whitening",             350,  45, None, "mouth"),
    ("INV",     "Invisalign",            "Invisalign clear aligner treatment",           3500, 60, None, "mouth"),
    ("DENT",    "Partial Denture",       "Cobalt chrome partial denture",                895,  60, "3", "mouth"),
    ("FULL",    "Full Denture",          "Full acrylic upper/lower denture",             1200, 60, "3", "mouth"),
    ("FLU",     "Fluoride Treatment",    "Topical fluoride application",                 35,   15, "1", "mouth"),
    ("EMG",     "Emergency Appointment", "Urgent pain or trauma assessment",             95,   30, "1", "mouth"),
]

CANCELLATION_REASONS = [
    ("Patient called to cancel", "patient"),
    ("Patient did not attend",   "patient"),
    ("Illness",                   "patient"),
    ("Work commitment",           "patient"),
    ("Practitioner unavailable",  "practice"),
    ("Emergency slot needed",     "practice"),
    ("Rescheduled by patient",    "patient"),
]

ACQUISITION_SOURCES = [
    ("Google Search",       "SEO / organic search"),
    ("Google Ads",          "Paid search"),
    ("Friend / Family",     "Word of mouth referral"),
    ("NHS Referral",        "Referred from NHS dentist"),
    ("Walk-in",             "Walked in off the street"),
    ("Facebook / Instagram","Social media"),
    ("Dentist Directory",   "Online directory listing"),
]

APPOINTMENT_REASONS = [
    "Check-up", "Scale & Polish", "Filling", "Crown Fit", "Crown Prep",
    "Emergency", "Consultation", "X-Ray", "Treatment", "Hygienist",
    "Whitening Review", "Implant Consultation", "Denture Fitting",
]

UK_TOWNS = ["London", "Camden", "Islington", "Hackney", "Westminster", "Chelsea", "Kensington"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = date(2026, 4, 10)
NOW = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
START_DATE = TODAY - timedelta(days=365 * CONFIG["history_years"])


class IdGen:
    """Sequential id generator, one counter per entity."""
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    def next(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 1000) + 1
        return self.counters[key]


def rand_datetime_between(start: date, end: date) -> datetime:
    delta = (end - start).days
    d = start + timedelta(days=random.randint(0, max(delta, 1)))
    return datetime(d.year, d.month, d.day, random.randint(8, 17), random.choice([0, 15, 30, 45]), tzinfo=timezone.utc)


def rand_date_between(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, max(delta, 1)))


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def gen_practice() -> dict[str, Any]:
    return {
        "id": 1,
        "name": "London Specialist Dentists",
        "address_line_1": "42 Harley Street",
        "address_line_2": "Marylebone",
        "postcode": "W1G 9PL",
        "town": "London",
        "email_address": "reception@lsd-dental.co.uk",
        "phone_number": "020 7946 0000",
        "website": "https://lsd-dental.co.uk",
        "nhs": True,
        "time_zone": "Europe/London",
        "opening_hours": {
            "monday":    {"open": "08:00", "close": "18:00"},
            "tuesday":   {"open": "08:00", "close": "18:00"},
            "wednesday": {"open": "08:00", "close": "18:00"},
            "thursday":  {"open": "08:00", "close": "19:00"},
            "friday":    {"open": "08:00", "close": "17:00"},
            "saturday":  {"open": "09:00", "close": "13:00"},
            "sunday":    None,
        },
    }


def gen_sites() -> list[dict[str, Any]]:
    return [{
        "id": 1,
        "active": True,
        "name": "Harley Street Main",
        "nickname": "HS Main",
        "address_line_1": "42 Harley Street",
        "postcode": "W1G 9PL",
        "town": "London",
        "phone_number": "020 7946 0000",
        "email_address": "reception@lsd-dental.co.uk",
        "practice_id": 1,
        "opening_hours": {"weekdays": "08:00-18:00"},
    }]


def gen_rooms(idgen: IdGen) -> list[dict[str, Any]]:
    rooms = []
    for i in range(1, CONFIG["num_rooms"] + 1):
        rooms.append({
            "id": idgen.next("room"),
            "name": f"Surgery {i}",
            "site_id": 1,
            "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "updated_at": NOW,
        })
    return rooms


def gen_practitioners(idgen: IdGen) -> list[dict[str, Any]]:
    prescribed = [
        ("Rudolf",   "Weber",    "dentist",   "GDC-284521"),
        ("Nicu",     "Pacuraru", "dentist",   "GDC-294818"),
        ("Amelia",   "Hughes",   "hygienist", "GDC-301245"),
    ]
    people = []
    for first, last, role, gdc in prescribed:
        pid = idgen.next("practitioner")
        people.append({
            "id": pid,
            "active": True,
            "gdc_number": gdc,
            "nhs_number": f"NHS-{random.randint(100000, 999999)}",
            "site_id": 1,
            "default_contract_id": 1,
            "user_id": pid + 5000,
            "user_first_name": first,
            "user_last_name": last,
            "user_email": f"{first.lower()}@lsd-dental.co.uk",
            "user_role": role,
            "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "updated_at": NOW,
        })
    return people


def gen_payment_plans() -> list[dict[str, Any]]:
    return [
        {
            "id": 1, "name": "NHS", "active": True,
            "dentist_recall_interval": 12, "hygienist_recall_interval": 6,
            "exam_duration": 20, "scale_and_polish_duration": 30,
            "site_id": 1, "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
        },
        {
            "id": 2, "name": "Private", "active": True,
            "dentist_recall_interval": 6, "hygienist_recall_interval": 4,
            "exam_duration": 30, "scale_and_polish_duration": 45,
            "site_id": 1, "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
        },
        {
            "id": 3, "name": "Denplan", "active": True,
            "dentist_recall_interval": 6, "hygienist_recall_interval": 4,
            "exam_duration": 30, "scale_and_polish_duration": 45,
            "site_id": 1, "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
        },
    ]


def gen_treatments() -> list[dict[str, Any]]:
    out = []
    for i, (code, nomen, desc, price, duration, nhs_band, region) in enumerate(TREATMENT_CATALOG, start=1):
        out.append({
            "id": i,
            "active": True,
            "code": code,
            "nomenclature": nomen,
            "patient_nomenclature": nomen,
            "description": desc,
            "region": region,
            "nhs_treatment_cat": f"Band {nhs_band}" if nhs_band else None,
            "treatment_category_id": int(nhs_band) if nhs_band else None,
            "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "updated_at": NOW,
        })
    return out


def gen_cancellation_reasons() -> list[dict[str, Any]]:
    return [{
        "id": i,
        "reason": reason,
        "reason_type": rtype,
        "archived": False,
        "created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
        "updated_at": NOW,
    } for i, (reason, rtype) in enumerate(CANCELLATION_REASONS, start=1)]


def gen_acquisition_sources() -> list[dict[str, Any]]:
    return [{
        "id": i,
        "name": name,
        "active": True,
        "notes": notes,
    } for i, (name, notes) in enumerate(ACQUISITION_SOURCES, start=1)]


def gen_patients(idgen: IdGen, dentists: list[int], hygienists: list[int]) -> list[dict[str, Any]]:
    patients = []
    for _ in range(CONFIG["num_patients"]):
        pid = idgen.next("patient")
        first = fake.first_name()
        last = fake.last_name()
        gender = random.choice(["male", "female"])
        dob = fake.date_of_birth(minimum_age=5, maximum_age=85)
        created = rand_datetime_between(START_DATE, TODAY - timedelta(days=7))
        is_churned = random.random() < CONFIG["pct_churned_patients"]
        payment_plan = random.choices([1, 2, 3], weights=[5, 3, 2])[0]

        # Recall interval depends on payment plan
        dentist_interval = 12 if payment_plan == 1 else 6
        hygienist_interval = 6 if payment_plan == 1 else 4

        # Recall date — 20% are overdue (in the past)
        overdue = random.random() < CONFIG["pct_overdue_recall"]
        if overdue:
            recall_date = TODAY - timedelta(days=random.randint(10, 180))
        else:
            recall_date = TODAY + timedelta(days=random.randint(1, dentist_interval * 30))

        patients.append({
            "id": pid,
            "title": random.choice(["Mr", "Mrs", "Ms", "Dr", "Miss"]),
            "first_name": first,
            "last_name": last,
            "preferred_name": first if random.random() > 0.8 else None,
            "date_of_birth": dob,
            "gender": gender,
            "email_address": f"{first.lower()}.{last.lower()}@{fake.free_email_domain()}",
            "home_phone": fake.phone_number(),
            "mobile_phone": fake.phone_number(),
            "work_phone": None,
            "preferred_phone_number": 3,
            "address_line_1": fake.street_address(),
            "address_line_2": None,
            "town": random.choice(UK_TOWNS),
            "county": "Greater London",
            "postcode": fake.postcode(),
            "dentist_id": random.choice(dentists),
            "hygienist_id": random.choice(hygienists),
            "dentist_recall_date": recall_date,
            "dentist_recall_interval": dentist_interval,
            "hygienist_recall_date": recall_date - timedelta(days=30),
            "hygienist_recall_interval": hygienist_interval,
            "medical_alert": random.random() < 0.12,
            "medical_alert_text": random.choice([
                None, "Penicillin allergy", "Diabetic", "High blood pressure",
                "Heart condition", "Pregnancy", "Anticoagulant medication",
            ]) if random.random() < 0.15 else None,
            "payment_plan_id": payment_plan,
            "acquisition_source_id": random.randint(1, len(ACQUISITION_SOURCES)),
            "marketing": random.random() < 0.6,
            "use_email": random.random() < 0.85,
            "use_sms": random.random() < 0.75,
            "recall_method": random.choice(["Email", "SMS", "Letter", "Phone"]),
            "active": not is_churned,
            "archived_reason": random.choice([
                "Moved away", "Dissatisfied", "Lapsed", "Transferred to another practice"
            ]) if is_churned else None,
            "site_id": 1,
            "nhs_number": f"NHS-{random.randint(1000000000, 9999999999)}" if payment_plan == 1 else None,
            "emergency_contact_name": fake.name(),
            "emergency_contact_phone": fake.phone_number(),
            "created_at": created,
            "updated_at": rand_datetime_between(created.date(), TODAY),
        })
    return patients


def gen_appointments(
    idgen: IdGen,
    patients: list[dict],
    practitioners: list[dict],
    rooms: list[dict],
) -> tuple[list[dict], dict[int, list[dict]]]:
    """Return (appointments list, index by patient_id)."""
    appointments = []
    by_patient: dict[int, list[dict]] = {}

    dentist_ids = [p["id"] for p in practitioners if p["user_role"] == "dentist"]
    hygienist_ids = [p["id"] for p in practitioners if p["user_role"] == "hygienist"]
    all_prac_ids = dentist_ids + hygienist_ids

    for patient in patients:
        if not patient["active"]:
            n_appts = random.randint(1, 4)   # churned patients have fewer
        else:
            n_appts = random.randint(3, 10)  # active patients 3-10 appts over 2y

        start_window = max(patient["created_at"].date(), START_DATE)
        pat_appts: list[dict] = []

        for _ in range(n_appts):
            aid = idgen.next("appointment")
            appt_date = rand_date_between(start_window, TODAY + timedelta(days=90))
            hour = random.randint(8, 17)
            start_time = datetime(appt_date.year, appt_date.month, appt_date.day, hour, 0, tzinfo=timezone.utc)
            duration = random.choice([20, 30, 45, 60])
            finish_time = start_time + timedelta(minutes=duration)
            reason = random.choice(APPOINTMENT_REASONS)
            is_hygienist_appt = "Hygienist" in reason or "Polish" in reason
            practitioner_id = random.choice(hygienist_ids if is_hygienist_appt else dentist_ids)

            # State logic:
            # - future appointments: Pending/Confirmed
            # - past appointments: mostly Completed, some FTA, some Cancelled
            state = "Pending"
            completed_at = cancelled_at = fta_at = None
            confirmed_at = start_time - timedelta(days=1)
            arrived_at = in_surgery_at = None
            cancel_reason_id = None

            if appt_date < TODAY:
                r = random.random()
                if r < CONFIG["pct_fta"]:
                    state = "Did not attend"
                    fta_at = start_time + timedelta(minutes=15)
                elif r < CONFIG["pct_fta"] + CONFIG["pct_cancelled"]:
                    state = "Cancelled"
                    cancelled_at = start_time - timedelta(days=random.randint(0, 3))
                    cancel_reason_id = random.randint(1, len(CANCELLATION_REASONS))
                else:
                    state = "Completed"
                    arrived_at = start_time
                    in_surgery_at = start_time + timedelta(minutes=5)
                    completed_at = finish_time
            else:
                state = random.choice(["Pending", "Confirmed"])
                if state == "Confirmed":
                    confirmed_at = NOW - timedelta(days=random.randint(1, 10))
                else:
                    confirmed_at = None

            appt = {
                "id": aid,
                "patient_id": patient["id"],
                "practitioner_id": practitioner_id,
                "room_id": random.choice(rooms)["id"],
                "reason": reason,
                "state": state,
                "duration": duration,
                "start_time": start_time,
                "finish_time": finish_time,
                "notes": random.choice([
                    None, "Patient reported sensitivity on UL6",
                    "Follow-up on composite filling", "Routine check",
                    "Patient anxious, consider sedation next time",
                    "Discussed whitening options", "Crown prep — continue next visit",
                ]) if random.random() < 0.4 else None,
                "pending_at": start_time - timedelta(days=14),
                "confirmed_at": confirmed_at,
                "arrived_at": arrived_at,
                "in_surgery_at": in_surgery_at,
                "completed_at": completed_at,
                "cancelled_at": cancelled_at,
                "did_not_attend_at": fta_at,
                "appointment_cancellation_reason_id": cancel_reason_id,
                "payment_plan_id": patient["payment_plan_id"],
                "site_id": 1,
                "created_at": start_time - timedelta(days=random.randint(7, 30)),
                "updated_at": max(
                    completed_at or cancelled_at or fta_at or confirmed_at or start_time,
                    start_time - timedelta(days=14),
                ),
            }
            pat_appts.append(appt)
            appointments.append(appt)

        by_patient[patient["id"]] = pat_appts

    return appointments, by_patient


def gen_patient_stats(patients: list[dict], appts_by_patient: dict[int, list[dict]]) -> list[dict]:
    stats = []
    for p in patients:
        pid = p["id"]
        appts = sorted(appts_by_patient.get(pid, []), key=lambda a: a["start_time"])
        completed = [a for a in appts if a["state"] == "Completed"]
        cancelled = [a for a in appts if a["state"] == "Cancelled"]
        fta = [a for a in appts if a["state"] == "Did not attend"]
        future = [a for a in appts if a["start_time"].date() > TODAY and a["state"] in ("Pending", "Confirmed")]
        past_exams = [a for a in completed if "Check" in (a["reason"] or "") or "Exam" in (a["reason"] or "")]
        past_polish = [a for a in completed if "Polish" in (a["reason"] or "")]

        def last_date(items):
            return max(items, key=lambda a: a["start_time"])["start_time"].date() if items else None

        def first_date(items):
            return min(items, key=lambda a: a["start_time"])["start_time"].date() if items else None

        def next_date(items):
            return min(items, key=lambda a: a["start_time"])["start_time"].date() if items else None

        stats.append({
            "patient_id": pid,
            "first_appointment_date": first_date(appts),
            "first_exam_date": first_date(past_exams),
            "last_appointment_date": last_date(completed),
            "last_exam_date": last_date(past_exams),
            "last_scale_and_polish_date": last_date(past_polish),
            "last_cancelled_appointment_date": last_date(cancelled),
            "last_fta_appointment_date": last_date(fta),
            "next_appointment_date": next_date(future),
            "next_exam_date": next_date([a for a in future if "Check" in (a["reason"] or "") or "Exam" in (a["reason"] or "")]),
            "next_scale_and_polish_date": next_date([a for a in future if "Polish" in (a["reason"] or "")]),
            "total_invoiced": 0,  # filled later after invoices generated
            "total_paid": 0,
            "created_at": p["created_at"],
            "updated_at": NOW,
        })
    return stats


def gen_treatment_plans_and_items(
    idgen: IdGen,
    patients: list[dict],
    treatments: list[dict],
    practitioners: list[dict],
) -> tuple[list[dict], list[dict]]:
    plans: list[dict] = []
    items: list[dict] = []
    dentist_ids = [p["id"] for p in practitioners if p["user_role"] == "dentist"]

    for patient in patients:
        # 60% of patients have at least one treatment plan
        if random.random() > 0.6:
            continue

        n_plans = random.choices([1, 2, 3], weights=[6, 3, 1])[0]
        for _ in range(n_plans):
            plan_id = idgen.next("treatment_plan")
            practitioner_id = random.choice(dentist_ids)
            start = rand_date_between(
                max(patient["created_at"].date(), START_DATE),
                TODAY - timedelta(days=1),
            )
            # n items per plan
            n_items = random.choices([1, 2, 3, 4, 5], weights=[4, 3, 2, 2, 1])[0]
            plan_completed = True
            completed_at = None
            last_completed_at = None
            nhs_uda = 0.0
            nhs_done_uda = 0.0
            private_val = 0.0

            for _ in range(n_items):
                item_id = idgen.next("tp_item")
                treatment = random.choice(treatments)
                # Uncompleted items = lost revenue opportunity
                is_completed = random.random() > CONFIG["pct_uncompleted_tp_items"]
                completed_date = rand_datetime_between(start, TODAY) if is_completed else None
                price = next(
                    (t[3] for t in TREATMENT_CATALOG if t[0] == treatment["code"]),
                    100,
                )

                if treatment["nhs_treatment_cat"]:
                    uda = {"Band 1": 1.0, "Band 2": 3.0, "Band 3": 12.0}.get(treatment["nhs_treatment_cat"], 0)
                    nhs_uda += uda
                    if is_completed:
                        nhs_done_uda += uda
                else:
                    private_val += price

                if not is_completed:
                    plan_completed = False
                else:
                    if completed_date and (last_completed_at is None or completed_date > last_completed_at):
                        last_completed_at = completed_date
                        completed_at = completed_date

                items.append({
                    "id": item_id,
                    "patient_id": patient["id"],
                    "practitioner_id": practitioner_id,
                    "treatment_plan_id": plan_id,
                    "treatment_id": treatment["id"],
                    "nomenclature": treatment["nomenclature"],
                    "code": treatment["code"],
                    "notes": None,
                    "duration": next(
                        (t[4] for t in TREATMENT_CATALOG if t[0] == treatment["code"]),
                        30,
                    ),
                    "price": price,
                    "region": treatment["region"],
                    "teeth": random.choice([[], [11], [16, 17], [26], [36, 37, 38]]),
                    "surfaces": random.choice([[], ["O"], ["M", "O"], ["B", "L"]]),
                    "completed": is_completed,
                    "completed_at": completed_date,
                    "charged": is_completed,
                    "appear_on_invoice": is_completed,
                    "invoice_id": None,  # linked after invoice generation
                    "payment_plan_id": patient["payment_plan_id"],
                    "created_at": datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
                    "updated_at": NOW,
                })

            plans.append({
                "id": plan_id,
                "patient_id": patient["id"],
                "practitioner_id": practitioner_id,
                "completed": plan_completed,
                "completed_at": completed_at,
                "start_date": start,
                "end_date": completed_at.date() if completed_at else None,
                "last_completed_at": last_completed_at,
                "nickname": random.choice([
                    "Hygiene course", "Restorative work", "Cosmetic treatment",
                    "Initial assessment", "Emergency treatment", "Full mouth rehab",
                ]),
                "nhs_uda_value": nhs_uda,
                "nhs_completed_uda_value": nhs_done_uda,
                "private_treatment_value": private_val,
                "created_at": datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
                "updated_at": NOW,
            })
    return plans, items


def gen_invoices_items_payments(
    idgen: IdGen,
    patients: list[dict],
    tp_items: list[dict],
    practitioners: list[dict],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Group completed tp_items into invoices, then generate payments. Also returns accounts."""
    invoices: list[dict] = []
    inv_items: list[dict] = []
    payments: list[dict] = []
    accounts: list[dict] = []

    # Group completed items by patient
    by_patient: dict[int, list[dict]] = {}
    for it in tp_items:
        if it["completed"]:
            by_patient.setdefault(it["patient_id"], []).append(it)

    dentist_ids = [p["id"] for p in practitioners if p["user_role"] == "dentist"]

    for patient in patients:
        pid = patient["id"]
        items = by_patient.get(pid, [])

        # One account per patient
        total_planned_nhs = 0.0
        total_planned_priv = 0.0
        account_id = idgen.next("account")

        total_invoiced = 0.0
        total_paid = 0.0

        if items:
            # Bundle items into 1-3 invoices
            chunks = max(1, min(3, len(items) // 3))
            random.shuffle(items)
            groups = [items[i::chunks] for i in range(chunks)]

            for group in groups:
                if not group:
                    continue
                invoice_id = idgen.next("invoice")
                amount = sum(float(it["price"]) for it in group)
                nhs_amount = sum(float(it["price"]) for it in group if patient["payment_plan_id"] == 1)
                dated_on = max(it["completed_at"] for it in group).date() if group[0]["completed_at"] else TODAY
                is_unpaid = random.random() < CONFIG["pct_unpaid_invoices"]
                paid_on = None if is_unpaid else dated_on + timedelta(days=random.randint(0, 30))
                amount_outstanding = amount if is_unpaid else 0.0

                invoices.append({
                    "id": invoice_id,
                    "patient_id": pid,
                    "account_id": account_id,
                    "site_id": 1,
                    "amount": round(amount, 2),
                    "amount_outstanding": round(amount_outstanding, 2),
                    "dated_on": dated_on,
                    "due_on": dated_on + timedelta(days=30),
                    "paid": not is_unpaid,
                    "paid_on": paid_on,
                    "reference": f"INV-{invoice_id:06d}",
                    "status": "Unpaid" if is_unpaid else "Paid",
                    "nhs_amount": round(nhs_amount, 2),
                    "sent_at": datetime(dated_on.year, dated_on.month, dated_on.day, tzinfo=timezone.utc),
                    "created_at": datetime(dated_on.year, dated_on.month, dated_on.day, tzinfo=timezone.utc),
                    "updated_at": NOW,
                })
                total_invoiced += amount
                if not is_unpaid:
                    total_paid += amount

                for it in group:
                    it["invoice_id"] = invoice_id
                    inv_items.append({
                        "id": idgen.next("invoice_item"),
                        "invoice_id": invoice_id,
                        "practitioner_id": it["practitioner_id"],
                        "name": it["nomenclature"],
                        "item_price": it["price"],
                        "total_price": it["price"],
                        "quantity": 1,
                        "nhs_charge": it["price"] if patient["payment_plan_id"] == 1 else 0,
                        "treatment_plan_id": it["treatment_plan_id"],
                        "treatment_plan_item_id": it["id"],
                        "created_at": datetime(dated_on.year, dated_on.month, dated_on.day, tzinfo=timezone.utc),
                        "updated_at": NOW,
                    })

                # Payment (only if paid)
                if not is_unpaid and paid_on:
                    payments.append({
                        "id": idgen.next("payment"),
                        "patient_id": pid,
                        "account_id": account_id,
                        "practitioner_id": random.choice(dentist_ids),
                        "site_id": 1,
                        "amount": round(amount, 2),
                        "amount_unexplained": 0.0,
                        "dated_on": paid_on,
                        "method": random.choice(["Credit Card", "Debit Card", "Cash", "BACS", "Cheque"]),
                        "status": "Paid",
                        "fully_explained": True,
                        "payment_plan_id": patient["payment_plan_id"],
                        "created_at": datetime(paid_on.year, paid_on.month, paid_on.day, tzinfo=timezone.utc),
                        "updated_at": NOW,
                    })

        accounts.append({
            "id": account_id,
            "patient_id": pid,
            "patient_name": f"{patient['first_name']} {patient['last_name']}",
            "current_balance": round(total_invoiced - total_paid, 2),
            "opening_balance": 0.0,
            "planned_nhs_treatment_value": round(total_planned_nhs, 2),
            "planned_private_treatment_value": round(total_planned_priv, 2),
        })

    return invoices, inv_items, payments, accounts


def gen_recalls(idgen: IdGen, patients: list[dict]) -> list[dict]:
    recalls = []
    for patient in patients:
        if not patient["active"]:
            continue
        # Every patient has at least one recall
        status_pick = random.choices(
            ["Booked", "Completed", "Unbooked", "Missed", "Skipped"],
            weights=[30, 30, 20, 15, 5],
        )[0]
        due = patient["dentist_recall_date"]
        reminded_at = None
        if status_pick in ("Booked", "Unbooked", "Missed"):
            reminded_at = datetime.combine(due - timedelta(days=14), datetime.min.time(), tzinfo=timezone.utc)

        recalls.append({
            "id": idgen.next("recall"),
            "patient_id": patient["id"],
            "due_date": due,
            "recall_type": random.choice(["Dentist", "Hygienist", "Dentist + Hygienist"]),
            "recall_method": patient["recall_method"],
            "status": status_pick,
            "prebooked": status_pick == "Booked",
            "times_contacted": random.randint(0, 3) if status_pick != "Completed" else 1,
            "first_reminder_sent_at": reminded_at,
            "last_reminded_at": reminded_at,
            "appointment_id": None,
            "created_at": patient["created_at"],
            "updated_at": NOW,
        })
    return recalls


def gen_nhs_claims(idgen: IdGen, plans: list[dict], patients_by_id: dict[int, dict]) -> list[dict]:
    claims = []
    for plan in plans:
        patient = patients_by_id.get(plan["patient_id"])
        if not patient or patient["payment_plan_id"] != 1:  # NHS plan only
            continue
        status = random.choices(
            ["completed", "submitted", "error", "queried", "withdrawn"],
            weights=[70, 15, 5, 5, 5],
        )[0]
        expected = plan["nhs_uda_value"]
        awarded = plan["nhs_completed_uda_value"] if status == "completed" else 0.0
        claims.append({
            "id": idgen.next("nhs_claim"),
            "patient_id": plan["patient_id"],
            "practitioner_id": plan["practitioner_id"],
            "treatment_plan_id": plan["id"],
            "contract_id": 1,
            "site_id": 1,
            "claim_status": status,
            "expected_uda": expected,
            "awarded_uda": awarded,
            "uda_band": "Band 2",
            "submitted_date": plan["start_date"] + timedelta(days=7),
            "approval_date": (plan["start_date"] + timedelta(days=30)) if status == "completed" else None,
            "patient_charge": 26.80,
            "dentist_charge": float(expected) * 25.0,
            "created_at": datetime(plan["start_date"].year, plan["start_date"].month, plan["start_date"].day, tzinfo=timezone.utc),
            "updated_at": NOW,
        })
    return claims


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_all(seed: int = 42) -> dict[str, list[dict]]:
    """Generate a full fake Dentally dataset. Deterministic by seed."""
    random.seed(seed)
    Faker.seed(seed)
    idgen = IdGen()

    print(f"[gen] Generating fake clinic: {CONFIG['num_patients']} patients, {CONFIG['history_years']}y history...")

    practice = gen_practice()
    sites = gen_sites()
    rooms = gen_rooms(idgen)
    practitioners = gen_practitioners(idgen)
    payment_plans = gen_payment_plans()
    treatments = gen_treatments()
    cancel_reasons = gen_cancellation_reasons()
    acq_sources = gen_acquisition_sources()

    dentist_ids = [p["id"] for p in practitioners if p["user_role"] == "dentist"]
    hygienist_ids = [p["id"] for p in practitioners if p["user_role"] == "hygienist"]

    patients = gen_patients(idgen, dentist_ids, hygienist_ids)
    print(f"[gen]   patients: {len(patients)}")

    appointments, appts_by_patient = gen_appointments(idgen, patients, practitioners, rooms)
    print(f"[gen]   appointments: {len(appointments)}")

    patient_stats = gen_patient_stats(patients, appts_by_patient)

    plans, tp_items = gen_treatment_plans_and_items(idgen, patients, treatments, practitioners)
    print(f"[gen]   treatment_plans: {len(plans)}  items: {len(tp_items)}")

    invoices, inv_items, payments, accounts = gen_invoices_items_payments(
        idgen, patients, tp_items, practitioners,
    )
    print(f"[gen]   invoices: {len(invoices)}  invoice_items: {len(inv_items)}  payments: {len(payments)}")

    # Backfill total_invoiced/paid into patient_stats
    inv_by_patient: dict[int, float] = {}
    paid_by_patient: dict[int, float] = {}
    for inv in invoices:
        inv_by_patient[inv["patient_id"]] = inv_by_patient.get(inv["patient_id"], 0) + float(inv["amount"])
        if inv["paid"]:
            paid_by_patient[inv["patient_id"]] = paid_by_patient.get(inv["patient_id"], 0) + float(inv["amount"])
    for s in patient_stats:
        s["total_invoiced"] = round(inv_by_patient.get(s["patient_id"], 0), 2)
        s["total_paid"] = round(paid_by_patient.get(s["patient_id"], 0), 2)

    recalls = gen_recalls(idgen, patients)
    print(f"[gen]   recalls: {len(recalls)}")

    patients_by_id = {p["id"]: p for p in patients}
    nhs_claims = gen_nhs_claims(idgen, plans, patients_by_id)
    print(f"[gen]   nhs_claims: {len(nhs_claims)}")

    return {
        "practice": [practice],
        "sites": sites,
        "rooms": rooms,
        "practitioners": practitioners,
        "payment_plans": payment_plans,
        "treatments": treatments,
        "appointment_cancellation_reasons": cancel_reasons,
        "acquisition_sources": acq_sources,
        "patients": patients,
        "patient_stats": patient_stats,
        "appointments": appointments,
        "treatment_plans": plans,
        "treatment_plan_items": tp_items,
        "recalls": recalls,
        "accounts": accounts,
        "invoices": invoices,
        "invoice_items": inv_items,
        "payments": payments,
        "nhs_claims": nhs_claims,
    }
