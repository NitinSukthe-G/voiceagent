"""Put the hospital, its doctors and its departments into MongoDB.

    python seed.py

Run this once after cloning, before the first `python agent.py`. It is safe to
run again: each document is replaced by its natural key, and bookings and
conversations are never touched.

This is the reference data for the sample hospital. Edit it here (or in the
database afterwards) to change the roster, the hours or the rules.
"""
import db

HOSPITAL = {
    "name": "Nova Suraksha Multispeciality Hospital",
    "address": "Plot 14, KPHB Phase 3, Kukatpally, Hyderabad 500072, near KPHB Metro Station",
    "reception": "040 4000 1234",
    "emergency": "040 4000 9999",
    "opd_hours": "09:00-20:00",
    "lunch": "13:00-14:00",
    "closed_days": [
        "Sun"
    ],
    "slot_minutes": 15,
    "max_days_ahead": 14,
    "min_minutes_ahead": 30,
    "cancel_hours_before": 2,
    "followup_free_days": 7,
    "pediatric_age_below": 14
}

DOCTORS = [
    {
        "id": "D01",
        "name": "Dr. Ramesh Varma",
        "dept": "General Medicine",
        "days": [
            "Mon",
            "Tue",
            "Wed",
            "Thu",
            "Fri",
            "Sat"
        ],
        "start": "09:00",
        "end": "13:00",
        "fee": 500
    },
    {
        "id": "D02",
        "name": "Dr. Kavitha Reddy",
        "dept": "General Medicine",
        "days": [
            "Mon",
            "Tue",
            "Wed",
            "Thu",
            "Fri",
            "Sat"
        ],
        "start": "14:00",
        "end": "20:00",
        "fee": 500
    },
    {
        "id": "D03",
        "name": "Dr. Sanjay Rao",
        "dept": "Cardiology",
        "days": [
            "Mon",
            "Wed",
            "Fri"
        ],
        "start": "10:00",
        "end": "13:00",
        "fee": 900
    },
    {
        "id": "D04",
        "name": "Dr. Anjali Mehta",
        "dept": "Pediatrics",
        "days": [
            "Mon",
            "Tue",
            "Wed",
            "Thu",
            "Fri",
            "Sat"
        ],
        "start": "10:00",
        "end": "13:00",
        "fee": 600
    },
    {
        "id": "D05",
        "name": "Dr. Farhan Ali",
        "dept": "Orthopedics",
        "days": [
            "Tue",
            "Thu",
            "Sat"
        ],
        "start": "14:00",
        "end": "18:00",
        "fee": 800
    },
    {
        "id": "D06",
        "name": "Dr. Lakshmi Prasanna",
        "dept": "Gynecology",
        "days": [
            "Mon",
            "Tue",
            "Wed",
            "Thu",
            "Fri"
        ],
        "start": "11:00",
        "end": "13:00",
        "fee": 700
    },
    {
        "id": "D07",
        "name": "Dr. Vikram Singh",
        "dept": "Dermatology",
        "days": [
            "Wed",
            "Sat"
        ],
        "start": "16:00",
        "end": "20:00",
        "fee": 700
    },
    {
        "id": "D08",
        "name": "Dr. Sneha Iyer",
        "dept": "ENT",
        "days": [
            "Tue",
            "Thu"
        ],
        "start": "09:00",
        "end": "12:00",
        "fee": 600
    }
]

DEPARTMENTS = [
    {
        "name": "Cardiology",
        "about": "The heart and blood vessels: chest discomfort, high blood pressure, palpitations, breathlessness on exertion, and heart check-ups."
    },
    {
        "name": "Dermatology",
        "about": "Skin, hair and nails: rashes, allergies, acne, hair loss, skin infections."
    },
    {
        "name": "ENT",
        "about": "Ear, nose and throat: ear pain, hearing problems, sinus, sore throat, tonsils."
    },
    {
        "name": "General Medicine",
        "about": "Everyday health problems and first consultations: fever, cough and cold, infections, body pain, blood pressure, diabetes, weakness. The right place to start when you are not sure which specialist you need."
    },
    {
        "name": "Gynecology",
        "about": "Women's health: pregnancy care, period problems, and reproductive health."
    },
    {
        "name": "Orthopedics",
        "about": "Bones, joints and muscles: fractures, back and knee pain, sports injuries, arthritis."
    },
    {
        "name": "Pediatrics",
        "about": "Children under fourteen: fever, growth and development, vaccinations, and any illness in a child."
    }
]


def seed():
    d = db.connect()
    d.hospital.replace_one({}, HOSPITAL, upsert=True)
    for doc in DOCTORS:
        d.doctors.replace_one({"id": doc["id"]}, doc, upsert=True)
    for dept in DEPARTMENTS:
        d.departments.replace_one({"name": dept["name"]}, dept, upsert=True)
    print(f"hospital: 1   doctors: {len(DOCTORS)}   departments: {len(DEPARTMENTS)}")
    print(f"database: {d.name}")
    print()
    print("in the database now:")
    for name in ("hospital", "doctors", "departments",
                 "bookings", "appointments", "conversations", "phrases"):
        print(f"  {name:<14} {d[name].count_documents({})}")
    db.close()


if __name__ == "__main__":
    seed()
