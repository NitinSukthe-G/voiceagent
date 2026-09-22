import json
import random
from datetime import datetime, timedelta

import db

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# The hospital and its doctors change rarely, so they are read once here.
# Bookings change on every call and are read fresh each time.
_db = db.connect()
H = _db.hospital.find_one({}, {"_id": 0})
DOCTORS = list(_db.doctors.find({}, {"_id": 0}).sort("id"))
DEPARTMENTS = {x["name"]: x["about"]
               for x in _db.departments.find({}, {"_id": 0})}
if not H or not DOCTORS:
    raise RuntimeError("The database is empty. Run: python seed.py")


# ---------- storage ----------

def load():
    """Every booking, without Mongo's _id so results stay JSON-serializable."""
    return list(_db.bookings.find({}, {"_id": 0}))


def record(event, booking_id, detail=""):
    """One document per booking event: BOOKED, RESCHEDULED, CANCELLED."""
    _db.appointments.insert_one({
        "at": datetime.now(), "event": event,
        "booking_id": booking_id, "detail": detail,
    })


def describe(b):
    day = DAYS[datetime.strptime(b["date"], "%Y-%m-%d").weekday()]
    return (f"{b['name']} ({b['age']})  {b['phone']}  {b['doctor']}, "
            f"{find_doctor(b['doctor_id'])['dept']}  {day} {b['date']} {b['time']}  "
            f"{b['visit']}  Rs {b['fee']}")


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def hhmm(total):
    return f"{total // 60:02d}:{total % 60:02d}"


def find_doctor(doctor_id):
    for d in DOCTORS:
        if d["id"] == doctor_id.upper():
            return d
    return None


def all_slots(doc):
    """Every 15 min slot of a doctor, lunch removed."""
    lunch_a, lunch_b = [minutes(x) for x in H["lunch"].split("-")]
    t = minutes(doc["start"])
    out = []
    while t + H["slot_minutes"] <= minutes(doc["end"]):
        if not (lunch_a <= t < lunch_b):
            out.append(hhmm(t))
        t += H["slot_minutes"]
    return out


def date_problem(doc, day):
    """Returns an error message, or None if the date is fine."""
    today = datetime.now().date()
    name = DAYS[day.weekday()]
    if day < today:
        return "That date is in the past."
    if day > today + timedelta(days=H["max_days_ahead"]):
        return "Bookings open only 14 days ahead."
    if name in H["closed_days"]:
        return "OPD is closed on Sunday. Emergency is open."
    if name not in doc["days"]:
        return f"{doc['name']} works only on {', '.join(doc['days'])}."
    return None


def slot_dt(date_str, time_str):
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")


def taken_times(doctor_id, date_str, rows, skip_id=None):
    return {
        b["time"] for b in rows
        if b["doctor_id"] == doctor_id and b["date"] == date_str
        and b["status"] == "booked" and b["id"] != skip_id
    }


def validate_slot(doc, date_str, time_str, rows, skip_id=None):
    """All booking rules for one slot. Returns error or None."""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
        when = slot_dt(date_str, time_str)
    except ValueError:
        return "Date must be YYYY-MM-DD and time HH:MM."
    problem = date_problem(doc, day)
    if problem:
        return problem
    lunch_a, lunch_b = [minutes(x) for x in H["lunch"].split("-")]
    if lunch_a <= minutes(time_str) < lunch_b:
        return "No appointments during lunch, 1 to 2 PM."
    if time_str not in all_slots(doc):
        return "That time is not a valid slot for this doctor."
    min_ahead = timedelta(minutes=H["min_minutes_ahead"])
    if when < datetime.now() + min_ahead:
        return "Slot must be at least 30 minutes from now."
    if time_str in taken_times(doc["id"], date_str, rows, skip_id):
        return "That slot is already booked."
    return None


# ---------- the tools the LLM can call ----------

def list_doctors(dept=""):
    rows = [d for d in DOCTORS
            if not dept or dept.lower() in d["dept"].lower()]
    if not rows:
        return {"error": "No such department.",
                "departments": sorted({d["dept"] for d in DOCTORS})}
    about = {d["dept"]: DEPARTMENTS.get(d["dept"], "") for d in rows}
    return {"doctors": rows, "about": about}


def check_slots(doctor_id, date):
    doc = find_doctor(doctor_id)
    if not doc:
        return {"error": "Unknown doctor id."}
    rows = load()
    free = [t for t in all_slots(doc)
            if validate_slot(doc, date, t, rows) is None]
    if not free:
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
            reason = date_problem(doc, day)
        except ValueError:
            reason = "Date must be YYYY-MM-DD."
        return {"doctor": doc["name"], "date": date, "free": [],
                "reason": reason or "Fully booked."}
    return {"doctor": doc["name"], "date": date, "free": free}


def book(name, age, phone, doctor_id, date, time, visit="new"):
    doc = find_doctor(doctor_id)
    if not doc:
        return {"error": "Unknown doctor id."}

    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) != 10:
        return {"error": "Phone number must have 10 digits."}

    try:
        age = int(age)
    except (TypeError, ValueError):
        return {"error": "Age must be a number."}
    if not 0 <= age <= 120:
        return {"error": "Age looks wrong."}
    if age < H["pediatric_age_below"] and doc["dept"] != "Pediatrics":
        return {"error": "Children under 14 must book Pediatrics "
                         "(Dr. Anjali Mehta, D04)."}

    rows = load()
    problem = validate_slot(doc, date, time, rows)
    if problem:
        return {"error": problem}

    same_day = [b for b in rows
                if b["phone"] == digits and b["status"] == "booked"
                and b["name"].lower() == name.lower()
                and b["doctor_id"] == doc["id"] and b["date"] == date]
    if same_day:
        return {"error": "This patient already has an appointment "
                         "with this doctor on that day."}

    fee = doc["fee"]
    note = ""
    if visit == "follow-up":
        day = datetime.strptime(date, "%Y-%m-%d").date()
        window = timedelta(days=H["followup_free_days"])
        earlier = [b for b in rows
                   if b["phone"] == digits and b["doctor_id"] == doc["id"]
                   and b["status"] == "booked"
                   and b["visit"] == "new"
                   and day - window <= datetime.strptime(
                       b["date"], "%Y-%m-%d").date() < day]
        if earlier:
            fee = 0
        else:
            visit = "new"
            note = "No first visit in the last 7 days, charged as new."

    used = {b["id"] for b in rows}
    new_id = f"NS{random.randint(1000, 9999)}"
    while new_id in used:
        new_id = f"NS{random.randint(1000, 9999)}"

    booking = {
        "id": new_id,
        "name": name, "age": age, "phone": digits,
        "doctor_id": doc["id"], "doctor": doc["name"],
        "date": date, "time": time, "visit": visit,
        "fee": fee, "status": "booked",
        "booked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _db.bookings.insert_one(dict(booking))   # copy: insert_one adds _id
    record("BOOKED", booking["id"], describe(booking))
    return {"ok": True, "booking_id": booking["id"],
            "fee": fee, "note": note}


def find_booking(phone):
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    today = datetime.now().strftime("%Y-%m-%d")
    rows = list(_db.bookings.find(
        {"phone": digits, "status": "booked", "date": {"$gte": today}},
        {"_id": 0}))
    return {"bookings": rows}


def _get(booking_id):
    return _db.bookings.find_one(
        {"id": booking_id.upper(), "status": "booked"}, {"_id": 0})


def _too_late(b):
    limit = timedelta(hours=H["cancel_hours_before"])
    return slot_dt(b["date"], b["time"]) - datetime.now() < limit


def cancel(booking_id):
    b = _get(booking_id)
    if not b:
        return {"error": "Booking not found."}
    if _too_late(b):
        return {"error": "Cancel is allowed only up to 2 hours before."}
    _db.bookings.update_one({"id": b["id"]}, {"$set": {"status": "cancelled"}})
    record("CANCELLED", b["id"], f"was {b['doctor']} {b['date']} {b['time']}")
    return {"ok": True}


def reschedule(booking_id, date, time):
    b = _get(booking_id)
    if not b:
        return {"error": "Booking not found."}
    if _too_late(b):
        return {"error": "Changes allowed only up to 2 hours before."}
    doc = find_doctor(b["doctor_id"])
    problem = validate_slot(doc, date, time, load(), skip_id=b["id"])
    if problem:
        return {"error": problem}
    was = f"{b['date']} {b['time']}"
    _db.bookings.update_one({"id": b["id"]},
                            {"$set": {"date": date, "time": time}})
    record("RESCHEDULED", b["id"], f"{was} -> {date} {time}")
    return {"ok": True, "date": date, "time": time}


FUNCTIONS = {
    "list_doctors": list_doctors,
    "check_slots": check_slots,
    "book": book,
    "find_booking": find_booking,
    "cancel": cancel,
    "reschedule": reschedule,
}


def run_tool(name, args_json):
    """The LLM gives a name + JSON text. We run the real function."""
    fn = FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool {name}"}
    try:
        args = json.loads(args_json or "{}")
        return fn(**args)
    except Exception as e:
        return {"error": f"Bad input: {e}"}


# ---------- tool descriptions sent to the LLM ----------

def _tool(name, desc, props, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


S = {"type": "string"}
TOOLS = [
    _tool("list_doctors", "Doctors in a department (empty = all).",
          {"dept": S}, []),
    _tool("check_slots", "Free times for a doctor on a date.",
          {"doctor_id": S,
           "date": {"type": "string", "description": "YYYY-MM-DD"}},
          ["doctor_id", "date"]),
    _tool("book", "Book only after the caller said yes to the read-back.",
          {"name": S, "age": {"type": "integer"},
           "phone": {"type": "string", "description": "10 digits"},
           "doctor_id": S,
           "date": {"type": "string", "description": "YYYY-MM-DD"},
           "time": {"type": "string", "description": "HH:MM 24h"},
           "visit": {"type": "string", "enum": ["new", "follow-up"]}},
          ["name", "age", "phone", "doctor_id", "date", "time"]),
    _tool("find_booking", "Upcoming bookings for a phone number.",
          {"phone": S}, ["phone"]),
    _tool("cancel", "Cancel a booking by id.",
          {"booking_id": S}, ["booking_id"]),
    _tool("reschedule", "Move a booking to a new date and time.",
          {"booking_id": S,
           "date": {"type": "string", "description": "YYYY-MM-DD"},
           "time": {"type": "string", "description": "HH:MM 24h"}},
          ["booking_id", "date", "time"]),
]
