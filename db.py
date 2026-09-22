"""The MongoDB connection. Every piece of data the agent reads or writes
lives here: the hospital and its doctors, bookings, the appointment event
log, conversation transcripts, and the pre-synthesized phrase cache.

    hospital       one document: name, address, hours, rules
    doctors        one document per doctor (id, name, dept, days, hours, fee)
    bookings       one document per booking; the source of truth
    appointments   one document per event: BOOKED / RESCHEDULED / CANCELLED
    conversations  one document per session, lines pushed as they happen
    phrases        fixed lines as PCM audio, keyed by voice + text
"""
from pymongo import MongoClient

import config

_client = None


def connect():
    """Connect once and fail loudly if the database is unreachable."""
    global _client
    if _client is None:
        if not config.MONGODB_URL:
            raise RuntimeError("MONGODB_URL missing. Put it in the .env file.")
        _client = MongoClient(config.MONGODB_URL, serverSelectionTimeoutMS=8000)
        _client.admin.command("ping")
    return _client[config.MONGODB_DB]


def close():
    global _client
    if _client is not None:
        _client.close()
        _client = None
